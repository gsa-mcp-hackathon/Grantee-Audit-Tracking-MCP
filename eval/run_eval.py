"""Ask Claude the eval questions using only this repo's MCP tools.

Claude runs on Amazon Bedrock and sees just system_prompt.txt, the MCP
server's instructions, and its tools, as an MCP client would show them: no
repo files, CLAUDE.md, or chat memory. Each answer is saved next to its
expected answer; eval/grade_eval.py scores it.

    pip install -e ".[eval]"
    aws sso login --profile bedrock
    AWS_PROFILE=bedrock python eval/run_eval.py              # every case
    AWS_PROFILE=bedrock python eval/run_eval.py s1-typo-uei  # one case
    AWS_PROFILE=bedrock python eval/run_eval.py s1 sam       # ids starting s1-, sam-

Set DAT_EVAL_MODEL to run a different model. Opus goes through Bedrock Mantle;
anything else needs an inference-profile id, e.g. us.anthropic.claude-haiku-5-5,
which goes through the standard Bedrock runtime.

Cases run CONCURRENCY at a time; the external APIs' rate limits, not Bedrock,
set the ceiling. The tool definitions and system prompt are cached across
turns and cases, so tokens.input counts only uncached input; cache reads and
writes are recorded separately.

A case with a "plant" field tests prompt injection through a tool result
rather than the question: when the model calls fac_get_sefa_notes for the
planted report id, the harness appends the planted note to the real FAC
response, so it arrives with the tool's own source notice, exactly where
auditee-written text would. FAC's data can't be edited, so this is how an
instruction gets inside source text. A planted case whose note never reached
the model is recorded as errored, not graded: it tested nothing. Under
DAT_EVAL_NO_TOOLS nothing can be planted and the expected answers report the
planted note, so those cases are left out of the run.

A case where any tool call came back HTTP 429 is recorded as errored, not
graded, and listed for rerun. FAC's api.data.gov key has an hourly limit
that a full Opus run and two Haiku runs back to back used up on 2026-11-17,
and a rate-limited case's answer reflects the limit, not the model.

A case with a "prompt" field ({"name", "arguments"}) runs one of the
server's MCP prompts: the harness renders it through MCP, as a client does
when an analyst picks it, and sends the rendered text as the question. Its
"question" says in plain words what the prompt asks, for the judge, and the
run records the text sent as "asked".

DAT_EVAL_NO_TOOLS=1 runs the same questions with no tools at all, for the
with-versus-without comparison in the evaluation document. The run file
records "tools": false.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from fastmcp import Client

from dat_mcp.app import mcp

EVAL_DIR = Path(__file__).parent
MODEL = os.environ.get("DAT_EVAL_MODEL", "anthropic.claude-opus-5-5")
MAX_TURNS = 15  # model calls per question, so a stuck loop can't run up cost
# (10 cut off a legitimate 17-call investigation; the deep coverage cases
# fetch a PDF and read several pages.)
CONCURRENCY = int(os.environ.get("DAT_EVAL_CONCURRENCY", "4"))
NO_TOOLS = os.environ.get("DAT_EVAL_NO_TOOLS") == "1"


async def question_for(mcp_client, case):
    """What the model is asked: the case's question, or its prompt rendered."""
    if "prompt" not in case:
        return case["question"]
    prompt = case["prompt"]
    rendered = await mcp_client.get_prompt(prompt["name"], prompt.get("arguments"))
    return "\n\n".join(m.content.text for m in rendered.messages)


def plant_note(plant, name, text):
    """The tool result with the planted note added, if this call is its target."""
    if name != "fac_get_sefa_notes":
        return text, False
    result = json.loads(text)
    # The tool's normalized id, so a lowercase or padded argument still matches.
    if result["report_id"] != plant["report_id"]:
        return text, False
    result["notes"].append(plant["note"])
    return json.dumps(result), True


async def ask(claude, mcp_client, tools, system, question, plant=None):
    """Run one question to a final answer, executing tool calls through MCP."""
    messages = [{"role": "user", "content": question}]
    tool_calls = []
    plant_served = False
    rate_limited = set()  # tools an API refused with HTTP 429
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    for _ in range(MAX_TURNS):
        # The client is synchronous; run it off the event loop so other
        # cases' tool calls keep going while this one waits on Bedrock.
        response = await asyncio.to_thread(
            claude.messages.create,
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "medium"},
            # One breakpoint after the system prompt caches the tools too,
            # since the prefix is tools, then system, then messages.
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=messages,
            **({"tools": tools} if tools else {}),
        )
        usage = response.usage
        tokens["input"] += usage.input_tokens
        tokens["output"] += usage.output_tokens
        tokens["cache_read"] += usage.cache_read_input_tokens or 0
        tokens["cache_write"] += usage.cache_creation_input_tokens or 0
        # Send the whole response back, including thinking blocks.
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break

        results = []
        for call in (b for b in response.content if b.type == "tool_use"):
            tool_calls.append({"tool": call.name, "input": call.input})
            result = await mcp_client.call_tool(
                call.name, call.input, raise_on_error=False
            )
            text = "\n".join(c.text for c in result.content if c.type == "text")
            if result.is_error and "returned HTTP 429" in text:
                rate_limited.add(call.name)
            if plant and not result.is_error:
                text, served = plant_note(plant, call.name, text)
                plant_served = plant_served or served
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": text or "(no output)",
                    "is_error": result.is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    answer = "\n".join(b.text for b in response.content if b.type == "text")
    if response.stop_reason == "tool_use":
        answer = f"(Stopped by the harness at MAX_TURNS={MAX_TURNS}; no final answer.)"
    if response.stop_reason == "refusal" and not answer:
        # The platform's safety layer declined before the model answered. Say
        # so, so the judge can grade a decline instead of an empty string.
        answer = "(The request was declined by the model's safety system: no answer.)"
    return {
        "answer": answer,
        "stop_reason": response.stop_reason,  # tool_use here means MAX_TURNS hit
        "tool_calls": tool_calls,
        "tokens": tokens,
        **({"plant_served": plant_served} if plant else {}),
        **({"rate_limited": sorted(rate_limited)} if rate_limited else {}),
    }


async def main(selected):
    cases = json.loads((EVAL_DIR / "cases.json").read_text())
    if selected:
        # An exact id, or a prefix: "s1" matches s1-typo-uei but not s10-....
        cases = [
            c for c in cases
            if any(c["id"] == s or c["id"].startswith(s + "-") for s in selected)
        ]  # fmt: skip
    if NO_TOOLS:
        # Nothing can be planted without tools, and the expected answers
        # report the planted note, so these cases can't be passed.
        cases = [c for c in cases if "plant" not in c]
    # What a real MCP client shows the model: the harness prompt, then the
    # server's own instructions (tool guide, "source text is data", no
    # determinations). Runs before 2026-11-06 sent only the first line.
    system = (EVAL_DIR / "system_prompt.txt").read_text()
    if not NO_TOOLS:
        system = f"{system.rstrip()}\n\n{mcp.instructions}"
    # Imported here so tests can import this module without the eval extra.
    from anthropic import AnthropicBedrock, AnthropicBedrockMantle

    # Mantle only serves Opus here; inference-profile ids use the standard runtime.
    if MODEL.startswith(("us.", "global.")):
        claude = AnthropicBedrock(aws_region="us-east-1")
    else:
        claude = AnthropicBedrockMantle(aws_region="us-east-1")

    async with Client(mcp) as mcp_client:
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in await mcp_client.list_tools()
        ]
        if NO_TOOLS:
            tools = []  # the no-tool baseline: same questions, model alone
        gate = asyncio.Semaphore(CONCURRENCY)

        async def run(case):
            async with gate:
                print(f"== {case['id']} started")
                try:
                    question = await question_for(mcp_client, case)
                    result = await ask(
                        claude, mcp_client, tools, system, question,
                        case.get("plant"),
                    )  # fmt: skip
                    if "prompt" in case:
                        result["asked"] = question
                except Exception as error:  # throttling, network: keep the rest
                    print(f"== {case['id']} errored: {error!r}")
                    return {**case, "error": repr(error)}
            if result.get("rate_limited"):
                # An API's rate limit, not the model, decided what it could
                # find: grading it would record a false failure.
                tools_hit = ", ".join(result["rate_limited"])
                result["error"] = f"rate limited (HTTP 429) in {tools_hit}"
            elif case.get("plant") and not result["plant_served"]:
                # The model never read the planted note, so the case is untested.
                result["error"] = "planted note never reached the model"
            print(f"== {case['id']}: {len(result['tool_calls'])} tool calls, "
                  f"{result['tokens']}")  # fmt: skip
            return {**case, **result}

        results = await asyncio.gather(*(run(case) for case in cases))

    # Save before printing: a console that can't encode a character in an
    # answer must not lose the run (it did once, on Windows cp1252).
    out = EVAL_DIR / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {"model": MODEL, "tools": not NO_TOOLS, "results": results}, indent=2
        ),
        encoding="utf-8",
    )
    for result in results:
        print(f"\n== {result['id']}\n{result.get('answer', result.get('error'))}")
    errored = [r["id"] for r in results if "error" in r]
    if errored:
        print(f"\n{len(errored)} errored; rerun them: {' '.join(errored)}")
    print(f"Saved {out}")


if __name__ == "__main__":
    # Answers hold characters the Windows console encoding can't print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main(sys.argv[1:]))
