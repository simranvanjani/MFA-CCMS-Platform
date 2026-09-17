"""Task 8: create the Consular Case Intelligence Genie space.

Builds a serialized_space (tables + sample questions + example SQL + instructions)
and creates the space via the Genie CLI. Prints and records the space id.

Run: python -m genie.create_space
"""
import json
import subprocess
import uuid

from pipeline.config import FQ, PROFILE, WAREHOUSE

GOLD = f"{FQ}.gold_case_intelligence"
ACC = f"{FQ}.tag_accuracy"


def nid():
    return uuid.uuid4().hex


def by_id(items):
    return sorted(items, key=lambda x: x["id"])


SAMPLE_QUESTIONS = [
    "Arrest & detention cases in Thailand where ICA was involved in the last 12 months",
    "What is the handling volume by mission?",
    "Average resolution time by case type",
    "Which countries have the most welfare-concern cases?",
    "How often does the AI case type match the CCMS-recorded type?",
]

EXAMPLE_SQLS = [
    ("Arrest & detention cases in Thailand where ICA was involved in the last 12 months",
     f"SELECT Case_Ref, Case_Location, Case_Status, Created_On FROM {GOLD} "
     f"WHERE Case_Type = 'Arrest & Detention' AND L1_Country = 'Thailand' "
     f"AND array_contains(L1_Agencies, 'ICA') "
     f"AND to_date(Created_On) >= add_months(current_date(), -12)"),
    ("Handling volume by mission",
     f"SELECT Assigned_HCG, count(*) AS cases FROM {GOLD} GROUP BY Assigned_HCG ORDER BY cases DESC"),
    ("Average resolution time by case type",
     f"SELECT Case_Type, round(avg(Resolution_Days),1) AS avg_days, count(Resolution_Days) AS closed "
     f"FROM {GOLD} WHERE Resolution_Days IS NOT NULL GROUP BY Case_Type ORDER BY avg_days DESC"),
    ("Scam cases with an MP/POH escalation",
     f"SELECT Case_Ref, Case_Location, Case_Status FROM {GOLD} "
     f"WHERE array_contains(L1_Situational_Flags, 'scam') "
     f"AND array_contains(L1_Situational_Flags, 'MP/POH escalation')"),
]

INSTRUCTIONS = [
    "Each row is one consular case, keyed by Case_Ref / Case_Id.",
    "Case_Type is the CCMS-recorded classification; L1_Case_Type is the AI-derived one — compare them for accuracy.",
    "'unit'/'handler'/'team' = Case_Handler; 'mission'/'post'/'HCG' = Assigned_HCG or L1_Mission.",
    "L1_Agencies and L1_Situational_Flags are ARRAYs — filter with array_contains(). "
    "Agencies: ICA, SPF, MHA, MOH. Flags: scam, MP/POH escalation, language barrier, welfare concern.",
    "'resolution time'/'time to close' = Resolution_Days (only set for closed cases).",
    "Created_On is a YYYY-MM-DD string — wrap with to_date() for date math.",
    "For overall AI-vs-CCMS accuracy, query the tag_accuracy view.",
]


def build_serialized_space():
    return json.dumps({
        "version": 2,
        "config": {
            "sample_questions": by_id([{"id": nid(), "question": [q]} for q in SAMPLE_QUESTIONS])
        },
        "data_sources": {
            "tables": sorted([{"identifier": GOLD}, {"identifier": ACC}], key=lambda x: x["identifier"])
        },
        "instructions": {
            "example_question_sqls": by_id(
                [{"id": nid(), "question": [q], "sql": [s]} for q, s in EXAMPLE_SQLS]),
            "text_instructions": [{"id": nid(), "content": INSTRUCTIONS}],
        },
    })


def main():
    me = json.loads(subprocess.run(
        ["databricks", "current-user", "me", "--profile", PROFILE],
        capture_output=True, text=True, check=True).stdout)["userName"]
    parent = f"/Workspace/Users/{me}/genie_spaces"
    subprocess.run(["databricks", "workspace", "mkdirs", parent, "--profile", PROFILE], check=True)

    payload = {
        "warehouse_id": WAREHOUSE,
        "title": "Consular Case Intelligence Genie",
        "description": "Natural-language analytics over MFA consular cases (structured + AI-derived tags).",
        "parent_path": parent,
        "serialized_space": build_serialized_space(),
    }
    res = subprocess.run(["databricks", "genie", "create-space", "--json", json.dumps(payload),
                          "--profile", PROFILE], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr or res.stdout)
    out = json.loads(res.stdout)
    print("space_id:", out.get("space_id") or out.get("id"))
    print(json.dumps(out, indent=1)[:400])


if __name__ == "__main__":
    main()
