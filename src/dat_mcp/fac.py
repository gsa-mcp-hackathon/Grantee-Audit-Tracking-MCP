"""Federal Audit Clearinghouse API client (api.fac.gov)."""

import re

import httpx
from fastmcp.exceptions import ToolError

from dat_mcp import net
from dat_mcp.config import settings

FAC_API = "https://api.fac.gov"
FAC_APP = "https://app.fac.gov"
PDF_HOST = "s3-us-gov-west-1.amazonaws.com"  # where FAC's PDF link redirects


def get(table, params):
    """Rows from one FAC table.

    Filter values must use FAC's syntax; build them with equals() or contains()
    below, e.g. {"auditee_ein": equals("953246624")}.
    """
    try:
        response = httpx.get(
            f"{FAC_API}/{table}",
            params=params,
            headers={"X-Api-Key": settings.fac_api_key},
            timeout=30,
            follow_redirects=False,
            trust_env=False,  # ignore proxy settings from the environment
            verify=net.SSL_CONTEXT,
        )
    except httpx.HTTPError:
        raise ToolError("Could not reach the FAC API.") from None
    # Report the status only; upstream error bodies aren't passed through.
    if response.status_code != 200:
        raise ToolError(f"FAC API returned HTTP {response.status_code}.")
    return response.json()


# FAC's API (PostgREST) needs an operator in every filter: column=operator.value.
# A bare value like auditee_ein=953246624 is rejected with HTTP 400.


def equals(value):
    """FAC filter for an exact match, e.g. eq.953246624."""
    return f"eq.{value}"


_NOT_WORD = "[^A-Za-z0-9]"


def contains(text):
    """FAC filter matching names that contain text's words, in order.

    Case and punctuation are ignored: "Family Health Centers, Inc." matches
    "FAMILY HEALTH CENTERS, INC" and "Kerr-People's" matches "KERR - PEOPLES".
    FAC stores each filing's name as typed, so a plain substring match missed
    filings that differ only in a comma or a period. Words must be adjacent,
    so "Family Health Centers" doesn't match "Family Health Care Centers".
    """
    parts = re.split(f"({_NOT_WORD}+)", text.strip())
    if not any(re.search("[A-Za-z0-9]", p) for p in parts):
        raise ToolError("A name search needs at least one letter or digit.")
    pattern = ""
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # An apostrophe before a final s is often left out: People's, Peoples.
            if len(part) > 2 and part[-1] in "sS":
                part = f"{part[:-1]}{_NOT_WORD}?{part[-1]}"
            pattern += part
        elif i + 1 < len(parts) and parts[i + 1]:
            # Between words: punctuation alone may vanish (Kerr-People's), but
            # a space has to be matched by something.
            pattern += _NOT_WORD + ("+" if re.search(r"\s", part) else "*")
    return f"imatch.{pattern}"


def one_of(values):
    """FAC filter matching any of several checked values, e.g. in.(a,b).

    One value becomes an eq filter. Values must already be validated
    identifiers (letters and digits), so none needs quoting.
    """
    values = list(values)
    if len(values) == 1:
        return equals(values[0])
    return f"in.({','.join(values)})"


def filed_under(ueis, eins):
    """The filter, as request parameters, for audits filed under any of the
    UEIs or any of the EINs. Filters on two columns are ANDed, so asking for
    either takes PostgREST's or=(...), which FAC accepts (checked 2026-11-17):
    one request, not one per column. Give at least one identifier."""
    given = {
        column: one_of(sorted(values))
        for column, values in (("auditee_uei", ueis), ("auditee_ein", eins))
        if values
    }
    if not given:
        raise ValueError("filed_under needs a UEI or an EIN")
    if len(given) == 1:
        return given
    return {"or": f"({','.join(f'{c}.{f}' for c, f in given.items())})"}


REPORT_ID = re.compile(r"\d{4}-\d{2}-(GSAFAC|CENSUS)-\d{10}")


def report_id(value):
    """Return a FAC report ID uppercased, e.g. 2025-06-GSAFAC-0000397078."""
    value = value.strip().upper()
    if not REPORT_ID.fullmatch(value):
        raise ToolError("report_id must look like 2024-06-GSAFAC-0000123456.")
    return value


# Where a person can search FAC for an audit; cited by a row that found none.
SEARCH_URL = "https://app.fac.gov/dissemination/search/"


def summary_url(report_id):
    return f"https://app.fac.gov/dissemination/summary/{report_id}"


def pdf_url(report_id):
    return f"{FAC_APP}/dissemination/report/pdf/{report_id}"


def download_pdf(report_id):
    """The bytes of one audit report PDF.

    FAC answers the PDF link with one redirect to its S3 bucket; only that
    host is followed. Suppressed (Tribal) and unknown reports return 404.
    """
    options = {
        "timeout": 60,
        "follow_redirects": False,
        "trust_env": False,
        "verify": net.SSL_CONTEXT,
    }
    try:
        response = httpx.get(pdf_url(report_id), **options)
        if response.is_redirect:
            location = httpx.URL(response.headers.get("location", ""))
            if location.scheme != "https" or location.host != PDF_HOST:
                raise ToolError("FAC redirected the PDF somewhere unexpected.")
            response = httpx.get(location, **options)
    except httpx.HTTPError:
        raise ToolError("Could not reach FAC to download the PDF.") from None
    if response.status_code == 404:
        raise ToolError(
            f"FAC has no public PDF for {report_id}: the report ID is wrong, "
            "or the report is suppressed (Tribal entities can withhold it)."
        )
    if response.status_code != 200:
        raise ToolError(f"FAC PDF download returned HTTP {response.status_code}.")
    if not response.content.startswith(b"%PDF"):
        raise ToolError("FAC returned something other than a PDF.")
    return response.content
