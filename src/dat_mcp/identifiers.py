"""Checks for the grantee identifiers that every service's tools accept.

Each function returns the value in the form the APIs expect, or raises a
ToolError whose message shows a valid example.
"""

import re

from fastmcp.exceptions import ToolError

MAX_BATCH = 20  # identifiers one call may look up at once


def many(value, check, name):
    """Return a list of checked identifiers from one value or a list of them.

    Tools take uei, ein, and report_id as a string or a list so an agent can
    look up several in one call instead of one call each. Duplicates are
    dropped, order is kept, and each value goes through its check.
    """
    values = [value] if isinstance(value, str) else list(value)
    if not values:
        raise ToolError(f"{name} was an empty list; give at least one value.")
    if len(values) > MAX_BATCH:
        raise ToolError(f"Give at most {MAX_BATCH} {name} values in one call.")
    return list(dict.fromkeys(check(v) for v in values))


def uei(value):
    """Return the UEI uppercased, e.g. jqnlhk8yltu6 -> JQNLHK8YLTU6.

    Follows SAM.gov's UEI rules (as enforced by FAC intake): no O or I, no
    leading zero, and no run of 9 digits, so a UEI can't be mistaken for an
    EIN or DUNS number.
    """
    value = value.strip().upper()
    if re.fullmatch(r"\d{9}", value.replace("-", "")):
        raise ToolError(
            "uei looks like an EIN or DUNS number; pass an EIN as ein. "
            "A UEI is 12 characters, e.g. A1B2C3D4E5F6."
        )
    if not re.fullmatch(r"[A-Z0-9]{12}", value):
        raise ToolError("uei must be 12 letters or digits, e.g. A1B2C3D4E5F6.")
    if "O" in value or "I" in value:
        raise ToolError(
            "UEIs never contain the letters O or I; check for 0 or 1, "
            "e.g. A1B2C3D4E5F6."
        )
    if value.startswith("0"):
        raise ToolError("UEIs never start with 0, e.g. A1B2C3D4E5F6.")
    if re.search(r"\d{9}", value):
        raise ToolError("UEIs never contain 9 digits in a row, e.g. A1B2C3D4E5F6.")
    return value


def ein(value):
    """Return the EIN as 9 digits, accepting 95-3246624 or 953246624."""
    value = value.strip().replace("-", "")
    if not re.fullmatch(r"\d{9}", value):
        raise ToolError("ein must be 9 digits, e.g. 112223333.")
    return value


def state(value):
    """Return a 2-letter state code uppercased, e.g. ca -> CA."""
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise ToolError("state must be a 2-letter code, e.g. CA.")
    return value


def audit_year(value):
    """Return the year an entity's fiscal year ends, within FAC's data range."""
    if not 2016 <= value <= 2035:
        raise ToolError("audit_year must be between 2016 and 2035.")
    return value
