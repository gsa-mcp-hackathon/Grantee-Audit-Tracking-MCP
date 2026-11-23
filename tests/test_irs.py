"""Tests for dat_mcp.irs reading returns out of e-file zips, with the IRS faked."""

import io
import zipfile

import httpx
import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import irs

XML = b'<Return xmlns="http://www.irs.gov/efile"><ReturnHeader/></Return>'
FIRST = "202531339349300001"
SECOND = "202531339349307668"


def zip_of(*object_ids, deflate64=False, method=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as z:
        for object_id in object_ids:
            z.writestr(f"{object_id}_public.xml", XML)
    data = buffer.getvalue()
    if deflate64:
        # A short deflate stream reads the same as Deflate64, so marking the
        # method 9 in both headers makes a zip only Deflate64 can open.
        data = data.replace(
            b"PK\x03\x04\x14\x00\x00\x00\x08", b"PK\x03\x04\x14\x00\x00\x00\x09"
        )
        data = data.replace(b"\x14\x00\x00\x00\x08\x00", b"\x14\x00\x00\x00\x09\x00", 1)
    return data


@pytest.fixture
def irs_site(monkeypatch):
    """Serve {zip name: bytes} as the IRS does, recording each request.

    Switches set in the same dict make it misbehave: "unreachable" (every
    request fails to connect), "head status" (HEAD answers with that status),
    "no ranges" (HEAD doesn't offer byte ranges), "ignore ranges" (GET sends
    the whole file), and "short range" (GET sends a byte too few)."""
    files, requests = {}, []

    def handler(request):
        requests.append(
            (request.method, request.url.path, request.headers.get("range"))
        )
        if files.get("unreachable"):
            raise httpx.ConnectError("connection refused", request=request)
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".zip")
        if name not in files:  # the IRS redirects a missing file to its 404 page
            return httpx.Response(302, headers={"location": "https://www.irs.gov/404"})
        data = files[name]
        headers = {"accept-ranges": "bytes", "content-length": str(len(data))}
        if files.get("no ranges"):
            del headers["accept-ranges"]
        if request.method == "HEAD":
            return httpx.Response(files.get("head status", 200), headers=headers)
        if files.get("ignore ranges"):
            return httpx.Response(200, content=data)
        start, end = map(
            int, request.headers["range"].removeprefix("bytes=").split("-")
        )
        if files.get("short range"):
            end -= 1
        return httpx.Response(206, content=data[start : end + 1])

    real_client = httpx.Client
    monkeypatch.setattr(
        irs.httpx,
        "Client",
        lambda **options: real_client(transport=httpx.MockTransport(handler)),
    )
    return files, requests


def test_reads_one_return_with_range_requests(irs_site):
    files, requests = irs_site
    files["2025_TEOS_XML_05A"] = zip_of(FIRST, SECOND)

    with irs.efile_reader() as read:
        assert read("2025_TEOS_XML_05A", SECOND) == XML
        assert read("2025_TEOS_XML_05A", FIRST) == XML

    # One HEAD, then ranges; the zip's directory is read once for both.
    assert [r[0] for r in requests].count("HEAD") == 1
    assert all(r[2] for r in requests if r[0] == "GET")


def test_finds_a_return_the_index_puts_in_the_wrong_zip(irs_site):
    """The index says 2024_TEOS_XML_05a; the file is 05A, the return is in 05B."""
    files, requests = irs_site
    files["2024_TEOS_XML_05A"] = zip_of(FIRST)
    files["2024_TEOS_XML_05B"] = zip_of(SECOND, deflate64=True)

    with irs.efile_reader() as read:
        assert read("2024_TEOS_XML_05a", SECOND) == XML
        with pytest.raises(ToolError, match="No 2024-05 zip holds"):
            read("2024_TEOS_XML_05a", "202400000000000000")

    heads = [
        path.rsplit("/", 1)[-1] for method, path, _ in requests if method == "HEAD"
    ]
    # Lowercase first, then the month's zips in order until one is missing.
    assert heads == [
        "2024_TEOS_XML_05a.zip",
        "2024_TEOS_XML_05A.zip",
        "2024_TEOS_XML_05B.zip",
        "2024_TEOS_XML_05C.zip",
    ]


def test_refuses_a_server_that_ignores_ranges(irs_site):
    files, _ = irs_site
    files["2025_TEOS_XML_05A"] = zip_of(SECOND)
    files["ignore ranges"] = True

    with pytest.raises(ToolError, match="HTTP 200"), irs.efile_reader() as read:
        read("2025_TEOS_XML_05A", SECOND)


def test_rejects_ids_that_arent_the_irss(irs_site):
    with irs.efile_reader() as read:
        with pytest.raises(ToolError, match="XML_BATCH_ID"):
            read("../2025_TEOS_XML_05A", SECOND)
        with pytest.raises(ToolError, match="OBJECT_ID"):
            read("2025_TEOS_XML_05A", "2025?x=1")
    with pytest.raises(ToolError, match="2024"):
        irs.index_url(2023)
    with pytest.raises(ToolError, match="Only IRS"):
        irs.download("https://example.com/index_2025.csv", "unused")


BATCH = "2025_TEOS_XML_05A"


def read_one(object_id=SECOND):
    with irs.efile_reader() as read:
        return read(BATCH, object_id)


@pytest.mark.parametrize(
    ("switch", "value", "message"),
    [
        ("unreachable", True, "Could not reach the IRS e-file site"),
        ("head status", 503, f"HTTP 503 for {BATCH}.zip"),
        ("no ranges", True, "stopped serving byte ranges"),
        ("short range", True, "short range"),
    ],
)
def test_a_misbehaving_site_is_a_tool_error(irs_site, switch, value, message):
    files, _ = irs_site
    files[BATCH] = zip_of(SECOND)
    files[switch] = value

    with pytest.raises(ToolError, match=message):
        read_one()


def test_a_file_that_isnt_a_zip_is_a_tool_error(irs_site):
    files, _ = irs_site
    files[BATCH] = b"<html>Service unavailable</html>" * 40

    with pytest.raises(ToolError, match=f"{BATCH}.zip isn't a readable zip"):
        read_one()


def test_a_damaged_return_in_a_good_zip_is_a_tool_error(irs_site):
    """The zip's directory reads, but the return's own bytes are bad."""
    files, _ = irs_site
    good = zip_of(SECOND, method=zipfile.ZIP_STORED)
    # The local header's signature: the directory still points at it.
    files[BATCH] = good.replace(b"PK\x03\x04", b"PX\x03\x04", 1)
    with pytest.raises(ToolError, match="damaged zip header"):
        read_one()

    # Stored, so a changed byte of the XML decompresses fine and fails the CRC.
    files[BATCH] = good.replace(b"<ReturnHeader/>", b"<ReturnHeaderX>", 1)
    with pytest.raises(ToolError, match="failed its zip checksum"):
        read_one()


def test_a_return_over_the_size_limit_isnt_read(irs_site, monkeypatch):
    files, requests = irs_site
    files[BATCH] = zip_of(SECOND)
    monkeypatch.setattr(irs, "MAX_RETURN_BYTES", len(XML) - 1)

    with pytest.raises(ToolError, match="too large to read"):
        read_one()


@pytest.fixture
def irs_download(monkeypatch):
    """Answer irs.download's single GET with a handler set by the test."""
    answer = {}

    def stream(method, url, **options):
        client = httpx.Client(transport=httpx.MockTransport(answer["handler"]))
        return client.stream(method, url)

    monkeypatch.setattr(irs.httpx, "stream", stream)
    return answer


def test_download_says_why_it_failed(irs_download, tmp_path):
    url, path = irs.index_url(2025), tmp_path / "index_2025.csv"

    # A missing index redirects to the IRS 404 page; nothing is written.
    irs_download["handler"] = lambda request: httpx.Response(
        302, headers={"location": "https://www.irs.gov/404"}
    )
    with pytest.raises(ToolError, match="HTTP 302"):
        irs.download(url, path)
    assert not path.exists()

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    irs_download["handler"] = refuse
    with pytest.raises(ToolError, match="Could not reach the IRS e-file site"):
        irs.download(url, path)

    irs_download["handler"] = lambda request: httpx.Response(200, content=b"a,b\n")
    irs.download(url, path)
    assert path.read_bytes() == b"a,b\n"


def test_a_return_that_wont_decompress_is_a_tool_error(irs_site):
    files, _ = irs_site
    good = zip_of(SECOND)  # deflated
    info = zipfile.ZipFile(io.BytesIO(good)).getinfo(f"{SECOND}_public.xml")
    start = info.header_offset + 30 + len(info.filename)
    # Garbage in place of the deflate stream, same length: the directory and
    # local header still read, the data doesn't.
    damaged = (
        good[:start] + b"\xff" * info.compress_size + good[start + info.compress_size :]
    )
    files[BATCH] = damaged

    with pytest.raises(ToolError, match="decompress"):
        read_one()
