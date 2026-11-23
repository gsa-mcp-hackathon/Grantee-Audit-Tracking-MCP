"""Tests for fac_get_awards. FAC is replaced by a fake."""

import pytest

from dat_mcp import fac
from dat_mcp.tools.fac_get_awards import fac_get_awards

ROWS = {
    "federal_awards": [
        {"award_reference": "AWARD-0001", "federal_agency_prefix": "93",
         "federal_award_extension": "913", "is_direct": "N", "amount_expended": 10},
    ],
    "passthrough": [
        {"award_reference": "AWARD-0001", "passthrough_name": "State Dept",
         "passthrough_id": "X1"},
    ],
}  # fmt: skip


@pytest.fixture
def requests(monkeypatch):
    sent = []

    def fake_get(table, params):
        sent.append((table, params))
        return [dict(row) for row in ROWS[table]]

    monkeypatch.setattr(fac, "get", fake_get)
    return sent


def test_joins_passthrough_sources_and_builds_the_aln(requests):
    result = fac_get_awards("2024-06-GSAFAC-0000360148", program="93.913")

    awards_params = requests[0][1]
    assert awards_params["federal_agency_prefix"] == "eq.93"
    assert awards_params["federal_award_extension"] == "eq.913"
    [award] = result["awards"]
    assert award["aln"] == "93.913"
    assert award["passthrough"] == [{"name": "State Dept", "id": "X1"}]
