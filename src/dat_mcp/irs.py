"""IRS exempt-organization data: EO BMF fields, and Form 990 e-file downloads.

Tools read IRS data from the local database (scripts/build_db.py): the EO BMF,
downloaded by hand, and Schedule R rows that scripts/fetch_schedule_r.py takes
from e-filed returns. That script is the only code that reaches the IRS, with
the download functions below.
"""

import re
import struct
import zipfile
import zlib
from contextlib import contextmanager
from string import ascii_uppercase

import httpx
import inflate64
from fastmcp.exceptions import ToolError

from dat_mcp import net

EO_BMF_PAGE = (
    "https://www.irs.gov/charities-non-profits/"
    "exempt-organizations-business-master-file-extract-eo-bmf"
)
EFILE = "https://apps.irs.gov/pub/epostcard/990/xml"
EFILE_PAGE = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"

# Only indexes from 2024 on name the zip (XML_BATCH_ID) each return is in.
FIRST_INDEX_YEAR = 2024
BATCH_ID = re.compile(r"(\d{4})_TEOS_XML_(\d{2})[A-Za-z]")
OBJECT_ID = re.compile(r"\d{18}")
MAX_RETURN_BYTES = 100_000_000  # the largest returns are a few MB of XML
DEFLATE64 = 9  # zip method some batches use, which zipfile can't read

OPTIONS = {
    "timeout": 120,
    "follow_redirects": False,
    "trust_env": False,  # ignore proxy settings from the environment
    "verify": net.SSL_CONTEXT,
}


def teos_url(ein):
    """The organization's page in the IRS Tax Exempt Organization Search."""
    return f"https://apps.irs.gov/app/eos/details/?ein={ein}"


# EO BMF codes, from the IRS's field guide:
# https://www.irs.gov/pub/foia/ig/tege/eo-info.pdf

AFFILIATION = {
    "1": "central organization, no group exemption",
    "2": "intermediate organization, no group exemption",
    "3": "independent organization",
    "6": "central organization of a group exemption",
    "7": "intermediate organization in a group exemption",
    "8": "central organization of a group exemption (church or 501(c)(1))",
    "9": "subordinate in a group exemption",
}
GROUP_CENTRAL = ("6", "8")
NO_GROUP = "0000"

SUBSECTION = {  # codes 01-29 are 501(c)(1) through 501(c)(29)
    "40": "501(d)",
    "50": "501(e)",
    "60": "501(f)",
    "70": "501(k)",
    "71": "501(n)",
    "81": "529",
    "92": "4947(a)(1)",
}

STATUS = {
    "01": "unconditional exemption",
    "02": "conditional exemption",
    "12": "trust described in 4947(a)(2)",
    "25": "terminating private foundation status under 507(b)(1)(B)",
}

FOUNDATION = {  # 00 is every organization that isn't a 501(c)(3)
    "02": "private operating foundation exempt from excise tax",
    "03": "private operating foundation",
    "04": "private non-operating foundation",
    "09": "suspense",
    "10": "church",
    "11": "school",
    "12": "hospital or medical research organization",
    "13": "supports a government-owned college or university",
    "14": "governmental unit",
    "15": "publicly supported, 170(b)(1)(A)(vi)",
    "16": "publicly supported, 509(a)(2)",
    "17": "supporting organization, 509(a)(3)",
    "18": "public safety testing organization",
    "21": "supporting organization, 509(a)(3) Type I",
    "22": "supporting organization, 509(a)(3) Type II",
    "23": "supporting organization, 509(a)(3) Type III functionally integrated",
    "24": "supporting organization, 509(a)(3) Type III other",
    "25": "agricultural research organization",
}

FILING_REQUIREMENT = {
    "00": "not required to file",
    "01": "Form 990 or 990-EZ",
    "02": "Form 990-N (income under $50,000)",
    "03": "covered by a group return",
    "04": "Form 990-BL",
    "06": "not required to file (church)",
    "07": "Form 990 (government 501(c)(1))",
    "13": "not required to file (religious organization)",
    "14": "not required to file (state or local instrumentality)",
}


def organization(row, central=None):
    """Flatten one eo_bmf row into the fields our tools return.

    central is the (EIN, NAME) row of the group exemption's central
    organization, for a member of a group.
    """
    group = None
    if row["GROUP"] and row["GROUP"] != NO_GROUP:
        group = {
            "number": row["GROUP"],
            "role": AFFILIATION.get(row["AFFILIATION"], row["AFFILIATION"]),
        }
        if central and central["EIN"] != row["EIN"]:
            group["central_ein"] = central["EIN"]
            group["central_name"] = central["NAME"]
    return {
        "ein": row["EIN"],
        "name": row["NAME"],
        "secondary_name": row["SORT_NAME"] or None,
        "street": row["STREET"],
        "city": row["CITY"],
        "state": row["STATE"],
        "zip": row["ZIP"],
        "exemption": _subsection(row["SUBSECTION"]),
        "exemption_status": STATUS.get(row["STATUS"], row["STATUS"]),
        "foundation_status": FOUNDATION.get(row["FOUNDATION"]),
        "ruling_date": _month(row["RULING"]),
        "group_exemption": group,
        "filing_requirement": FILING_REQUIREMENT.get(
            row["FILING_REQ_CD"], row["FILING_REQ_CD"]
        ),
        "latest_return_period": _month(row["TAX_PERIOD"]),
        "fiscal_year_end_month": int(row["ACCT_PD"]) if row["ACCT_PD"] else None,
        "revenue_amount": _amount(row["REVENUE_AMT"]),
        "asset_amount": _amount(row["ASSET_AMT"]),
        "ntee_code": row["NTEE_CD"] or None,
        "source_url": teos_url(row["EIN"]),
    }


# The IRS's note on the revocation list: dates in this window should read
# 2020-07-15, after COVID-19 extended the filing deadline.
COVID_WINDOW = ("2020-04-01", "2020-07-14")
COVID_DATE = "2020-07-15"


def revocation(row):
    """Flatten one irs_revocations row into the fields our tools return."""
    record = {
        "ein": row["ein"],
        "name": row["name"],
        "secondary_name": row["secondary_name"] or None,
        "city": row["city"],
        "state": row["state"],
        "exemption": _subsection(row["subsection"]),
        "revocation_date": row["revocation_date"],
        "revocation_posting_date": row["revocation_posting_date"] or None,
        "reinstatement_date": row["reinstatement_date"] or None,
        "source_url": teos_url(row["ein"]),
    }
    if COVID_WINDOW[0] <= row["revocation_date"] <= COVID_WINDOW[1]:
        record["corrected_revocation_date"] = COVID_DATE
    return record


def _subsection(code):
    if code in SUBSECTION:
        return SUBSECTION[code]
    if code.isdigit() and 1 <= int(code) <= 29:
        return f"501(c)({int(code)})"
    return code or None


def _month(yyyymm):
    """YYYYMM as YYYY-MM, e.g. 202506 -> 2025-06."""
    return f"{yyyymm[:4]}-{yyyymm[4:]}" if len(yyyymm or "") == 6 else None


def _amount(text):
    return int(text) if text else None


# Form 990 e-file downloads. The IRS posts each processing year's returns as
# zips of XML (2025_TEOS_XML_11B.zip), with an index CSV per year naming each
# return's EIN, tax period, form, OBJECT_ID, and the zip it's in.


def index_url(year):
    if not FIRST_INDEX_YEAR <= year <= 2035:
        raise ToolError(f"e-file index years run from {FIRST_INDEX_YEAR}, e.g. 2025.")
    return f"{EFILE}/{year}/index_{year}.csv"


def download(url, path):
    """Save one file from the IRS e-file site to path, streamed."""
    if not url.startswith(f"{EFILE}/"):
        raise ToolError("Only IRS e-file downloads are fetched.")
    try:
        with httpx.stream("GET", url, **OPTIONS) as response:
            if response.status_code != 200:
                raise ToolError(f"IRS returned HTTP {response.status_code}.")
            with open(path, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError:
        raise ToolError("Could not reach the IRS e-file site.") from None


@contextmanager
def efile_reader():
    """Read returns out of the e-file zips; yields read(batch_id, object_id).

    read returns one return's XML. The zips run to hundreds of megabytes, so
    they're read in place with HTTP range requests: each zip's directory once,
    then only the returns asked for.

    The index's XML_BATCH_ID names the zip, but not exactly. Some are
    lowercase (2024_TEOS_XML_05a) where the file is uppercase, and a month
    split across zips (2025_TEOS_XML_05A and 05B) lists every return under
    the first. So read tries the name as given, then each of that month's
    zips in turn until one doesn't exist.
    """
    try:
        with httpx.Client(**OPTIONS) as client:
            zips = {}  # name -> (remote file, {member name: ZipInfo}), or None

            def contents(name):
                if name not in zips:
                    zips[name] = _open_zip(client, name)
                return zips[name]

            def read(batch_id, object_id):
                match = BATCH_ID.fullmatch(batch_id.strip())
                if not match:
                    raise ToolError("XML_BATCH_ID must look like 2025_TEOS_XML_11B.")
                if not OBJECT_ID.fullmatch(object_id):
                    raise ToolError("OBJECT_ID must be 18 digits.")
                year, month = match[1], match[2]
                siblings = [f"{year}_TEOS_XML_{month}{c}" for c in ascii_uppercase]
                for name in dict.fromkeys([match[0], *siblings]):
                    found = contents(name)
                    if found is None:
                        if name in siblings:
                            break  # the month's zips are lettered without gaps
                        continue
                    remote, members = found
                    info = members.get(f"{object_id}_public.xml")
                    if info:
                        return _member(remote, info)
                raise ToolError(f"No {year}-{month} zip holds return {object_id}.")

            yield read
    except httpx.HTTPError:
        raise ToolError("Could not reach the IRS e-file site.") from None


def _open_zip(client, name):
    """(remote file, members by name) for one zip, or None if the IRS has no
    such file. A missing file answers with a redirect to the IRS 404 page."""
    url = f"{EFILE}/{name[:4]}/{name}.zip"
    head = client.head(url)
    if head.status_code in (301, 302, 404):
        return None
    if head.status_code != 200:
        raise ToolError(f"IRS returned HTTP {head.status_code} for {name}.zip.")
    remote = _RangeFile(client, url, head)
    try:
        infos = zipfile.ZipFile(remote).infolist()
    except zipfile.BadZipFile:
        raise ToolError(f"{name}.zip isn't a readable zip.") from None
    return remote, {info.filename.rsplit("/", 1)[-1]: info for info in infos}


class _RangeFile:
    """A remote file read by HTTP range requests, seekable for zipfile."""

    def __init__(self, client, url, head):
        self.client, self.url, self.position = client, url, 0
        if head.headers.get("accept-ranges") != "bytes":
            raise ToolError("The IRS e-file site stopped serving byte ranges.")
        self.size = int(head.headers["content-length"])

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = (0, self.position, self.size)[whence] + offset
        return self.position

    def read(self, n=-1):
        end = self.size if n < 0 else min(self.position + n, self.size)
        if end <= self.position:
            return b""
        headers = {"Range": f"bytes={self.position}-{end - 1}"}
        # Streamed so a server that ignores Range can't send the whole zip.
        with self.client.stream("GET", self.url, headers=headers) as response:
            if response.status_code != 206:
                raise ToolError(f"IRS returned HTTP {response.status_code}.")
            data = response.read()
        if len(data) != end - self.position:
            raise ToolError("The IRS e-file site returned a short range.")
        self.position = end
        return data


def _member(remote, info):
    """One file out of the zip, decompressed and checked against its CRC."""
    if info.file_size > MAX_RETURN_BYTES:
        raise ToolError(f"{info.filename} is too large to read.")
    remote.seek(info.header_offset)
    signature, name_length, extra_length = struct.unpack("<4s22xHH", remote.read(30))
    if signature != b"PK\x03\x04":
        raise ToolError(f"{info.filename} has a damaged zip header.")
    remote.seek(info.header_offset + 30 + name_length + extra_length)
    raw = remote.read(info.compress_size)
    try:
        if info.compress_type == zipfile.ZIP_DEFLATED:
            data = zlib.decompress(raw, -15)
        elif info.compress_type == DEFLATE64:
            data = inflate64.Inflater().inflate(raw)
        elif info.compress_type == zipfile.ZIP_STORED:
            data = raw
        else:
            raise ToolError(f"{info.filename} uses zip method {info.compress_type}.")
    except (zlib.error, ValueError):
        # A damaged stream: zlib raises zlib.error, inflate64 ValueError.
        # Damage neither notices is caught by the length and CRC check below.
        raise ToolError(f"{info.filename} won't decompress: it's damaged.") from None
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        raise ToolError(f"{info.filename} failed its zip checksum.")
    return data
