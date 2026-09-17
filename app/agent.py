"""Orchestrator: routes an officer's question to the right tool, then composes a cited answer.

Uses the Claude Sonnet serving endpoint (OpenAI-compatible) for a lightweight two-step
router -> tool -> compose flow (PRD S4.2 runtime routing).
"""
import json
import os
import re

from databricks.sdk import WorkspaceClient

import tools

LLM = os.getenv("CCMS_LLM", "databricks-claude-sonnet-4-5")
_w = WorkspaceClient()
_client = _w.serving_endpoints.get_open_ai_client()

ROUTE_PROMPT = (
    "Classify the officer's question into exactly one route and return ONLY JSON "
    '{"route": "...", "case_ref": "..."}.\n'
    "Routes:\n"
    "- structured: counts, distributions, trends, filters across cases (answered from the case table).\n"
    "- similar: find or summarise similar/past cases.\n"
    "- procedure: what is the correct procedure / SOP for a case type.\n"
    "- case_lookup: asks about one specific case by its Case_Ref (e.g. SGCON/2024/10004).\n"
    "case_ref: the referenced Case_Ref if route is case_lookup, else null.\n"
    "Question: "
)


def _chat(messages, max_tokens=700):
    resp = _client.chat.completions.create(model=LLM, messages=messages, max_tokens=max_tokens)
    return resp.choices[0].message.content


def route(question: str) -> dict:
    raw = _chat([{"role": "user", "content": ROUTE_PROMPT + question}], max_tokens=120)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)) if m else {"route": "structured"}
    except Exception:
        return {"route": "structured"}


def answer(question: str) -> tuple[str, str]:
    """Return (route, answer_markdown)."""
    r = route(question)
    route_name = r.get("route", "structured")

    if route_name == "similar":
        evidence = tools.similar_cases(question)
        source = "Semantic search over case email threads"
    elif route_name == "procedure":
        evidence = tools.sop_lookup(question)
        source = "SOP / procedures library"
    elif route_name == "case_lookup":
        ref = r.get("case_ref")
        case = tools.get_case(ref) if ref else {}
        evidence = json.dumps(case, default=str, indent=1) if case else "Case not found."
        source = f"Case record {ref}"
    else:
        route_name = "structured"
        evidence = tools.genie_query(question)
        source = "Genie over the structured case table"

    compose = [
        {"role": "system", "content": (
            "You are the MFA Consular Case Intelligence assistant. Answer the officer's question "
            "using ONLY the evidence provided. Be concise and factual. Always end with a "
            "'Source:' line citing where the answer came from, including any Case_Ref cited. "
            "Never invent case details or NRIC/personal data.")},
        {"role": "user", "content": (
            f"Question: {question}\n\nEvidence ({source}):\n{evidence}")},
    ]
    return route_name, _chat(compose)
