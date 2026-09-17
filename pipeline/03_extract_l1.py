"""Task 4: extract CCMS fields from parsed emails -> case_extracted (provided schema).

Uses ai_query (Claude Sonnet) with a JSON response, parsed via from_json into a typed
struct, then mapped by name into the exact 52-column case_extracted table. Evidence-only:
the prompt returns null for anything the email does not state.

Run: python -m pipeline.03_extract_l1            # full run
     python -m pipeline.03_extract_l1 --sample   # 5-row preview of extracted JSON
"""
import sys

from pipeline import dbsql
from pipeline.config import FQ, LLM

# Fields we ask the model to extract, with their struct types.
EXTRACT_FIELDS = {
    "Title_Name": "STRING",              # name of the citizen the case is about
    "Case_Type": "STRING",               # e.g. Arrest & Detention
    "Case_Status": "STRING",             # Pending / Closed
    "Case_Handler": "STRING",            # HQ unit: CRC or CON/OPS
    "Assigned_HCG": "STRING",            # mission / HCG (e.g. Bangkok)
    "Handled_By": "STRING",              # officer name
    "Case_Location": "STRING",           # City, Country of events
    "Current_Location": "STRING",
    "Citizenship": "STRING",
    "Origin": "STRING",                  # how the case was reported
    "Case_Title": "STRING",
    "Case_Subject": "STRING",
    "Case_Description": "STRING",        # 2-3 sentence factual summary
    "Assistance_Required": "STRING",
    "Advice_Provided___Follow_up": "STRING",
    "Category_Multiselect": "STRING",
    "Sub_Category_Multiselect": "STRING",
    "Tag": "STRING",
    "Caller___Informant": "STRING",
    "Registrant": "STRING",
    "MP_Name": "STRING",
    "Covering_MP": "STRING",
    "MP_Constituency_Division": "STRING",
    "Incident_ID": "STRING",
    "Created_On": "STRING",              # date of first email (YYYY-MM-DD)
}

STRUCT = "STRUCT<" + ", ".join(f"{k}:{v}" for k, v in EXTRACT_FIELDS.items()) + ">"

PROMPT = (
    "You are extracting structured fields from a Singapore MFA consular case email thread. "
    "Return ONLY a JSON object with exactly these keys: "
    + ", ".join(EXTRACT_FIELDS.keys()) + ". "
    "Apply a value ONLY when the email text explicitly supports it; use null otherwise "
    "(do NOT guess). Case_Status is 'Closed' or 'Pending'. Case_Handler is the HQ unit "
    "(CRC or CON/OPS). Assigned_HCG is the responsible overseas mission. Case_Location is "
    "'City, Country'. Created_On is the date of the first message as YYYY-MM-DD. "
    "Tag is a short comma-separated list of keywords. Email thread:\n"
)

# SQL-escaped (single quotes doubled) for safe embedding in a SQL string literal.
PROMPT_SQL = PROMPT.replace("'", "''")

# case_extracted column -> SQL expression sourcing it.
def build_column_exprs():
    exprs = {}
    for f in EXTRACT_FIELDS:
        exprs[f] = f"js.{f}"
    # Audit / system columns not extracted from the email.
    exprs["Do_Not_Modify_Case"] = "case_ref"
    exprs["Do_Not_Modify_Modified_On"] = "current_timestamp()"
    exprs["Modified_On"] = "cast(current_timestamp() as string)"
    exprs["Case_Ref"] = "case_ref"
    # Case_Type is the CCMS-recorded classification, filed via the subject-line token
    # "[<ref>] <Case_Type> - <name> ..." — derive it deterministically for clean categories.
    exprs["Case_Type"] = (
        r"regexp_extract(parsed_text, '\\[SGCON[^\\]]*\\]\\s*(.+?)\\s*-\\s', 1)"
    )
    exprs["MFA_Ref"] = "cast(null as string)"
    exprs["New_Case"] = "'Yes'"
    exprs["No_of_Open_Child_Cases"] = "'0'"
    exprs["Allow_Case_History_Access"] = "'Yes'"
    exprs["Allow_LRS_Access"] = "'No'"
    # Checksum over the business columns (everything except the Do_Not_Modify_* audit trio).
    return exprs


ALL_COLUMNS = None  # loaded from schema module


def _all_columns():
    import importlib
    mod = importlib.import_module("pipeline.00_schema")
    return [name for name, _ in mod.COLUMNS]


def sample():
    sql = f"""
    SELECT case_ref,
      ai_query('{LLM}', concat('{PROMPT_SQL}', parsed_text),
               failOnError => false).result AS js_raw
    FROM {FQ}.bronze_email_parsed LIMIT 5
    """
    for r in dbsql.run(sql):
        print("CASE:", r["case_ref"])
        print(r["js_raw"][:600])
        print("---")


def extract():
    # Stage 1: extraction JSON per row (one ai_query call each — the costly step).
    dbsql.run(f"""
    CREATE OR REPLACE TABLE {FQ}._l1_json AS
    SELECT case_ref, parsed_text,
      from_json(
        regexp_extract(
          ai_query('{LLM}', concat('{PROMPT_SQL}', parsed_text), failOnError => false).result,
          '(?s)[{{].*[}}]', 0),
        '{STRUCT}'
      ) AS js
    FROM {FQ}.bronze_email_parsed
    """)


def remap():
    # Stage 2: map to the exact case_extracted schema (INSERT BY NAME preserves declared types).
    cols = _all_columns()
    exprs = build_column_exprs()
    business = [c for c in cols if not c.startswith("Do_Not_Modify_")]
    checksum = "sha2(concat_ws('|', " + ", ".join(f"coalesce({exprs.get(c, 'cast(null as string)')},'')" for c in business) + "), 256)"
    exprs["Do_Not_Modify_Row_Checksum"] = checksum

    select_cols = ",\n  ".join(f"{exprs.get(c, 'cast(null as string)')} AS {c}" for c in cols)
    dbsql.run(f"TRUNCATE TABLE {FQ}.case_extracted")
    dbsql.run(f"INSERT INTO {FQ}.case_extracted BY NAME\nSELECT\n  {select_cols}\nFROM {FQ}._l1_json")

    print("rows:", dbsql.run(f"SELECT count(*) c FROM {FQ}.case_extracted")[0]["c"])
    print("by Case_Type:", dbsql.run(
        f"SELECT Case_Type, count(*) n FROM {FQ}.case_extracted GROUP BY Case_Type ORDER BY n DESC"))
    print("null Case_Location:", dbsql.run(
        f"SELECT count(*) c FROM {FQ}.case_extracted WHERE Case_Location IS NULL")[0]["c"])
    print("MP_Name populated:", dbsql.run(
        f"SELECT count(MP_Name) c FROM {FQ}.case_extracted")[0]["c"])


def full():
    extract()
    remap()


if __name__ == "__main__":
    if "--sample" in sys.argv:
        sample()
    elif "--map-only" in sys.argv:
        remap()
    else:
        full()
