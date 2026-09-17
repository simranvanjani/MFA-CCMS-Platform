"""Task 5: Layer 1 derived tags + Layer 2 analytical fields -> gold_case_intelligence.

One ai_query per row returns both the evidence-only L1 tags (country, mission, case type,
agencies[], situational flags[], status) and the L2 free-text analytical fields. Gold joins
these onto case_extracted and adds Case_Id + Resolution_Days (from the thread timestamps).

Run: python -m pipeline.04_enrich_l2             # extract + build gold
     python -m pipeline.04_enrich_l2 --gold-only # rebuild gold from existing _l2_json
"""
import sys

from pipeline import dbsql
from pipeline.config import FQ, LLM

L2_FIELDS = {
    "L1_Country": "STRING",
    "L1_Mission": "STRING",
    "L1_Case_Type": "STRING",
    "L1_Agencies": "ARRAY<STRING>",
    "L1_Situational_Flags": "ARRAY<STRING>",
    "L1_Status": "STRING",
    "L2_Summary_Pathway": "STRING",
    "L2_Assistance_Req_vs_Provided": "STRING",
    "L2_Complications_Delays": "STRING",
    "L2_External_Resources": "STRING",
    "L2_Lessons_Learnt": "STRING",
}
STRUCT = "STRUCT<" + ", ".join(f"{k}:{v}" for k, v in L2_FIELDS.items()) + ">"

PROMPT = (
    "You are a Singapore MFA consular analyst. Read the case email thread and return ONLY a "
    "JSON object with these keys: " + ", ".join(L2_FIELDS.keys()) + ".\n"
    "LAYER 1 TAGS — evidence-only: apply a value ONLY when the narrative explicitly supports it, "
    "else null (or [] for arrays). Never infer a tag just because it is typical.\n"
    "- L1_Country: country where events occurred.\n"
    "- L1_Mission: responsible HQ/overseas unit (CRC, CON/OPS, or a mission e.g. Bangkok).\n"
    "- L1_Case_Type: one of Arrest & Detention, Serious Illness/Death, Victim of Crime, "
    "Lost/Stolen Document, Evacuation, Scam.\n"
    "- L1_Agencies: array of external Singapore agencies explicitly mentioned, from ICA, SPF, MHA, MOH.\n"
    "- L1_Situational_Flags: array from scam, MP/POH escalation, language barrier, welfare concern.\n"
    "- L1_Status: 'closed' or 'pending'.\n"
    "LAYER 2 — write concise analytical free text for each:\n"
    "- L2_Summary_Pathway: 2-3 sentence summary of what happened and how it was handled.\n"
    "- L2_Assistance_Req_vs_Provided: what was requested vs what was provided.\n"
    "- L2_Complications_Delays: complications or delays, or 'None noted'.\n"
    "- L2_External_Resources: external resources/agencies engaged, or 'None noted'.\n"
    "- L2_Lessons_Learnt: one lesson learnt.\n"
    "Email thread:\n"
)
PROMPT_SQL = PROMPT.replace("'", "''")

# Column list for gold = all case_extracted columns + L1/L2 + derived.
L1L2_COLS = list(L2_FIELDS.keys())


def extract():
    dbsql.run(f"""
    CREATE OR REPLACE TABLE {FQ}._l2_json AS
    SELECT case_ref, parsed_text,
      from_json(
        regexp_extract(
          ai_query('{LLM}', concat('{PROMPT_SQL}', parsed_text), failOnError => false).result,
          '(?s)[{{].*[}}]', 0),
        '{STRUCT}'
      ) AS e
    FROM {FQ}.bronze_email_parsed
    """)


def build_gold():
    l1l2 = ",\n      ".join(f"e.e.{c} AS {c}" for c in L1L2_COLS)
    # Resolution_Days: datediff between first and last "DD Mon YYYY" date in the thread,
    # only for closed cases.
    dates = "regexp_extract_all(l.parsed_text, '([0-9]{1,2} [A-Za-z]{3} [0-9]{4})', 1)"
    first_d = f"to_date(element_at({dates}, 1), 'd MMM yyyy')"
    last_d = f"to_date(element_at({dates}, -1), 'd MMM yyyy')"
    res_days = (
        f"CASE WHEN lower(ce.Case_Status) = 'closed' AND size({dates}) >= 2 "
        f"THEN datediff({last_d}, {first_d}) ELSE cast(null as int) END"
    )
    dbsql.run(f"""
    CREATE OR REPLACE TABLE {FQ}.gold_case_intelligence AS
    SELECT
      ce.*,
      {l1l2},
      coalesce(ce.MFA_Ref, ce.Case_Ref) AS Case_Id,
      {res_days} AS Resolution_Days
    FROM {FQ}.case_extracted ce
    JOIN {FQ}._l2_json e ON ce.Case_Ref = e.case_ref
    JOIN {FQ}.bronze_email_parsed l ON ce.Case_Ref = l.case_ref
    """)

    print("rows:", dbsql.run(f"SELECT count(*) c FROM {FQ}.gold_case_intelligence")[0]["c"])
    print("L1_Case_Type dist:", dbsql.run(
        f"SELECT L1_Case_Type, count(*) n FROM {FQ}.gold_case_intelligence GROUP BY L1_Case_Type ORDER BY n DESC"))
    print("avg agencies:", dbsql.run(
        f"SELECT round(avg(size(L1_Agencies)),2) a, round(avg(size(L1_Situational_Flags)),2) f FROM {FQ}.gold_case_intelligence"))
    print("resolution days (closed):", dbsql.run(
        f"SELECT count(Resolution_Days) n, round(avg(Resolution_Days),1) avg_days FROM {FQ}.gold_case_intelligence"))
    print("L2 sample:", dbsql.run(
        f"SELECT substr(L2_Summary_Pathway,1,140) s FROM {FQ}.gold_case_intelligence LIMIT 1")[0]["s"])


if __name__ == "__main__":
    if "--gold-only" in sys.argv:
        build_gold()
    else:
        extract()
        build_gold()
