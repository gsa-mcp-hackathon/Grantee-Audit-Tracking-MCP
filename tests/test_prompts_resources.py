"""Tests for the MCP prompts and resources, through an MCP client."""

import asyncio
import json
import re

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError

from dat_mcp import db
from dat_mcp.app import mcp
from dat_mcp.prompts import grantee_args

# Every prompt's arguments, filled in as an analyst would.
SAMPLE_ARGS = {
    "check_filing": {"grantee": "JQNLHK8YLTU6", "fiscal_year": "2025"},
    "find_covering_audit": {"grantee": "HLKECQTNPLG3", "fiscal_year": "2024"},
    "review_missing_year": {"grantee": "GMFLJZLNKK34", "fiscal_year": "2024"},
    "filing_pattern": {"grantee": "411000060"},
    "grantee_connections": {"grantee": "People's Health Centers", "state": "mo"},
}


def run(call):
    async def go():
        async with Client(mcp) as client:
            return await call(client)

    return asyncio.run(go())


def prompt_text(name, arguments):
    result = run(lambda c: c.get_prompt(name, arguments))
    [message] = result.messages
    assert message.role == "user"
    return message.content.text


def read(uri):
    [contents] = run(lambda c: c.read_resource(uri))
    return contents.text


def test_every_prompt_is_described_with_described_arguments():
    prompts = run(lambda c: c.list_prompts())
    assert {p.name for p in prompts} == set(SAMPLE_ARGS)
    for prompt in prompts:
        assert prompt.title and prompt.description, prompt.name
        for argument in prompt.arguments:
            assert argument.description, f"{prompt.name}.{argument.name}"


def test_prompts_name_only_real_tools_and_resources():
    """A service-prefixed name is a tool, or a field a tool's description
    documents (fac_accepted_date, irs_records), so a rename breaks this."""
    listed = run(lambda c: c.list_tools())
    tools = {t.name for t in listed}
    documented = " ".join(t.description for t in listed)
    resources = {str(r.uri) for r in run(lambda c: c.list_resources())}
    for name, arguments in SAMPLE_ARGS.items():
        text = prompt_text(name, arguments)
        assert "{" not in text.replace('{"', ""), f"{name} left a placeholder"
        named = set(re.findall(r"\b(?:grantee|fac|sam|irs|usaspending)_[a-z_]+", text))
        unknown = {n for n in named - tools if not re.search(rf"\b{n}\b", documented)}
        assert not unknown, f"{name} names {unknown}"
        assert named & tools, f"{name} names no tool"
        assert set(re.findall(r"dat://[a-z/-]+", text)) <= resources, name


def test_every_prompt_asks_for_evidence_not_a_determination():
    for name, arguments in SAMPLE_ARGS.items():
        text = prompt_text(name, arguments)
        assert "source_url" in text and "caveats" in text, name
        assert "don't\nstate whether the grantee is compliant" in text, name


def test_grantee_is_passed_as_uei_ein_or_name():
    assert grantee_args("jqnlhk8yltu6") == 'uei="JQNLHK8YLTU6"'
    assert grantee_args("95-3246624") == 'ein="953246624"'
    assert grantee_args(" 953246624 ") == 'ein="953246624"'
    assert grantee_args("Konza Prairie", "ks") == 'name="Konza Prairie", state="KS"'
    # Claude Code splits arguments on spaces, so a name comes with underscores.
    assert grantee_args("Konza_Prairie_Community") == 'name="Konza Prairie Community"'


def test_a_name_with_underscores_reads_as_words_in_the_question():
    text = prompt_text("filing_pattern", {"grantee": "Community_Health_Service"})
    assert text.startswith("How has the grantee Community Health Service filed")
    assert 'name="Community Health Service"' in text


def test_check_filing_asks_for_one_year_only_when_given():
    one_year = prompt_text("check_filing", SAMPLE_ARGS["check_filing"])
    assert 'with uei="JQNLHK8YLTU6", audit_year=2025.' in one_year
    every_year = prompt_text("check_filing", {"grantee": "JQNLHK8YLTU6"})
    assert "audit_year" not in every_year
    assert "fiscal year ending in" not in every_year


def test_find_covering_audit_asks_for_an_answer_to_the_coverage_question():
    """Coverage is a question of fact: the first eval run, on 2026-11-18, found
    the evidence and then left "is it covered?" to the analyst."""
    text = prompt_text("find_covering_audit", SAMPLE_ARGS["find_covering_audit"])
    assert "Then answer the question: whether the records show an audit" in text


def test_review_missing_year_takes_the_years_either_side_with_funding():
    text = prompt_text("review_missing_year", SAMPLE_ARGS["review_missing_year"])
    assert "audit_year=[2023, 2024, 2025]" in text
    assert "with_funding=True" in text
    assert "$750,000" in text and "$1,000,000" in text


@pytest.mark.parametrize(
    "arguments, message",
    [
        # A name split on spaces lands its second word in fiscal_year.
        ({"grantee": "Inland", "fiscal_year": "Behavioral"}, "Inland_Behavioral"),
        ({"grantee": "HLKECQTNPLG3", "fiscal_year": "1999"}, "between 2016 and 2035"),
        ({"grantee": "Inland", "fiscal_year": "2025", "state": "Cal"}, "2-letter"),
    ],
)
def test_prompts_reject_a_bad_year_or_state_with_an_example(arguments, message):
    with pytest.raises(MCPError, match=message):
        run(lambda c: c.get_prompt("find_covering_audit", arguments))


def test_prompts_need_their_required_arguments():
    with pytest.raises(MCPError):
        run(lambda c: c.get_prompt("find_covering_audit", {"grantee": "HLKECQTNPLG3"}))


def test_audit_rules_resource_has_the_requirement_and_who_never_files():
    text = read("dat://reference/audit-rules")
    assert text.startswith("## 1. The requirement")
    assert "$1,000,000" in text and "9 months after the fiscal year ends" in text
    assert "### S21. Grantees that never file in FAC" in text
    assert "## 2. Vocabulary" not in text
    assert not text.rstrip().endswith("---")


def test_glossary_resource_is_the_vocabulary_section_only():
    text = read("dat://reference/glossary")
    assert text.startswith("## 2. Vocabulary")
    for term in ("**SEFA**", "**UEI**", "**Outlay**", "**FAC `audit_year`**"):
        assert term in text
    assert "## 3. The sources" not in text


def test_snapshot_resource_lists_each_loaded_table(database):
    database({"general": [{"report_id": "2025-06-GSAFAC-0000000001"}]})
    result = json.loads(read("dat://data/snapshots"))
    [general] = [t for t in result["tables"] if t["table_name"] == "general"]
    assert general["source"] == "fac"
    assert general["row_count"] == 1
    assert general["source_date"] and general["loaded_at"]
    assert "live" in result["note"]


def test_snapshot_resource_says_when_the_database_is_not_built(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, "dat_db_path", str(tmp_path / "missing.sqlite"))
    with pytest.raises(MCPError, match="not built"):
        read("dat://data/snapshots")
