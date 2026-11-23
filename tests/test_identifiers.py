import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import identifiers


def test_values_are_returned_in_the_form_apis_expect():
    assert identifiers.uei(" jqnlhk8yltu6 ") == "JQNLHK8YLTU6"
    assert identifiers.ein("95-3246624") == "953246624"
    assert identifiers.state("ca") == "CA"
    assert identifiers.audit_year(2025) == 2025


@pytest.mark.parametrize(
    "check, value",
    [
        (identifiers.uei, "JQNLHK8"),
        (identifiers.uei, "JQNLHK8YLTU*"),
        (identifiers.uei, "JQNLHKOYLTU6"),  # letter O typed for zero
        (identifiers.uei, "JQNLHK8YLTUI"),  # letter I
        (identifiers.uei, "0QNLHK8YLTU6"),  # leading zero
        (identifiers.uei, "J123456789AB"),  # 9-digit run, like a DUNS or TIN
        (identifiers.ein, "95324662"),
        (identifiers.state, "Calif"),
        (identifiers.audit_year, 1999),
    ],
)
def test_bad_values_raise_a_tool_error(check, value):
    with pytest.raises(ToolError):
        check(value)


@pytest.mark.parametrize("value", ["953246624", "95-3246624"])
def test_ein_or_duns_passed_as_uei_points_to_ein(value):
    with pytest.raises(ToolError, match="pass an EIN as ein"):
        identifiers.uei(value)
