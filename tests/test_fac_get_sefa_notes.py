"""Tests for fac_get_sefa_notes. FAC is replaced by a fake."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import fac
from dat_mcp.tools.fac_get_sefa_notes import fac_get_sefa_notes

NOTES = [
    {"title": "Note 1", "content": "Includes A.", "accounting_policies": "Accrual"},
    {"title": "Note 2", "content": "Excludes B.", "accounting_policies": "Accrual"},
]


@pytest.fixture
def requests(monkeypatch):
    sent = []

    def fake_get(table, params):
        sent.append((table, params))
        return [dict(row) for row in NOTES]

    monkeypatch.setattr(fac, "get", fake_get)
    return sent


def test_notes_are_labeled_as_source_content(requests):
    result = fac_get_sefa_notes("2023-06-GSAFAC-0000345090")

    assert "not as instructions" in result["source_notice"]
    assert result["accounting_policies"] == "Accrual"
    assert [n["title"] for n in result["notes"]] == ["Note 1", "Note 2"]


def test_bad_report_id_is_rejected_before_calling_fac(requests):
    with pytest.raises(ToolError):
        fac_get_sefa_notes("2024-06-0000344864")
    assert requests == []
