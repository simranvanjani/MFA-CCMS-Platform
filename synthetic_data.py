"""Build the Consular Case Intelligence backing tables with SYNTHETIC data.

Use this to stand up the demo in a fresh workspace without any real emails, PDFs,
parsing, or LLM calls — it generates a realistic, *uneven* case table (plus the SOP
and operational tables) entirely in SQL, so the app, dashboard, and filters work
immediately. (Genie space + Vector Search indexes + app deploy still come from
install.py; this only builds the tables.)

Run locally:
    python synthetic_data.py --catalog kauvey_poc --schema mfa_demo \
        --warehouse <warehouse-id> --profile DEFAULT --cases 400

The default install path uses REAL data (see install.py, ingest_mode = parsed_table /
case_table / pdf). This script is the "just give me data" shortcut.
"""
import argparse
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

SOPS = {
    "Arrest & Detention": ["Confirm the citizen's identity and place of detention via the local mission.",
                            "Arrange consular access and verify welfare within 48 hours.",
                            "Provide the family a list of local lawyers; do not recommend one.",
                            "Coordinate with ICA on travel-document validity where relevant."],
    "Serious Illness/Death": ["Establish hospital liaison and obtain the attending doctor's assessment.",
                              "Provide the next-of-kin regular, factual updates.",
                              "Advise on medical evacuation and repatriation, consulting MOH where needed."],
    "Victim of Crime": ["Assist the victim to lodge a local police report.",
                        "Advise on replacing stolen documents; coordinate ICA for emergency travel documents.",
                        "Offer welfare support and monitor until the traveller is safe."],
    "Lost/Stolen Document": ["Verify the applicant's identity against records.",
                            "Issue an emergency Document of Identity via ICA coordination.",
                            "Advise the traveller to lodge a local police report."],
    "Evacuation": ["Activate LRS to identify and contact registrants in the affected area.",
                  "Advise citizens to shelter in place until assisted departure is confirmed.",
                  "Coordinate inter-agency transport and account for all citizens."],
    "Scam": ["Treat as urgent where the victim may be held against their will.",
            "Work with local authorities to locate the victim.",
            "Engage SPF Anti-Scam Centre; coordinate rescue and repatriation."],
}


def make_runner(profile, warehouse):
    w = WorkspaceClient(profile=profile)

    def run(sql):
        r = w.statement_execution.execute_statement(warehouse_id=warehouse, statement=sql, wait_timeout="50s")
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(1)
            r = w.statement_execution.get_statement(r.statement_id)
        if r.status.state != StatementState.SUCCEEDED:
            msg = r.status.error.message if r.status.error else str(r.status.state)
            raise RuntimeError(f"{r.status.state}: {msg}\n{sql[:400]}")
        return r
    return run


def build(catalog, schema, warehouse, profile, n):
    FQ = f"{catalog}.{schema}"
    run = make_runner(profile, warehouse)
    try:
        run(f"CREATE CATALOG IF NOT EXISTS {catalog}")
    except Exception as e:
        print(f"(catalog '{catalog}' not auto-created — assuming it exists; {str(e)[:70]})")
    run(f"CREATE SCHEMA IF NOT EXISTS {FQ} COMMENT 'MFA CCMS synthetic demo'")

    # gold_case_intelligence — weighted, uneven distributions, generated in SQL.
    run(f"""
    CREATE OR REPLACE TABLE {FQ}.gold_case_intelligence AS
    WITH g AS (
      SELECT id, rand() ut, rand() uc, rand() us, rand() ud,
             rand() un1, rand() un2, rand() uag, rand() ufl, rand() ux,
             cast(rand()*720 as int) age
      FROM range(0, {n})
    ), m AS (
      SELECT *,
        CASE WHEN ut<0.26 THEN 'Scam' WHEN ut<0.48 THEN 'Lost/Stolen Document'
             WHEN ut<0.66 THEN 'Victim of Crime' WHEN ut<0.80 THEN 'Serious Illness/Death'
             WHEN ut<0.91 THEN 'Arrest & Detention' ELSE 'Evacuation' END AS Case_Type,
        CASE WHEN uc<0.18 THEN 'Thailand' WHEN uc<0.34 THEN 'China' WHEN uc<0.47 THEN 'Malaysia'
             WHEN uc<0.59 THEN 'Indonesia' WHEN uc<0.70 THEN 'United Kingdom' WHEN uc<0.80 THEN 'Australia'
             WHEN uc<0.90 THEN 'Japan' ELSE 'India' END AS L1_Country,
        CASE WHEN us<0.62 THEN 'Closed' WHEN us<0.74 THEN 'In Progress'
             WHEN us<0.80 THEN 'On Hold' ELSE 'Pending' END AS Case_Status,
        date_sub(current_date(), age) AS cdt
      FROM g
    )
    SELECT
      concat('SGCON/', cast(year(cdt) as string), '/', cast(20000+id as string)) AS Case_Ref,
      concat('SGCON/', cast(year(cdt) as string), '/', cast(20000+id as string)) AS Case_Id,
      Case_Type, Case_Status,
      CASE WHEN id%2=0 THEN 'CRC' ELSE 'CON/OPS' END AS Case_Handler,
      CASE L1_Country WHEN 'Thailand' THEN 'Bangkok' WHEN 'China' THEN 'Guangzhou'
        WHEN 'Malaysia' THEN 'Kuala Lumpur' WHEN 'Indonesia' THEN 'Jakarta'
        WHEN 'United Kingdom' THEN 'London' WHEN 'Australia' THEN 'Canberra'
        WHEN 'Japan' THEN 'Tokyo' ELSE 'New Delhi' END AS Assigned_HCG,
      L1_Country,
      slice(shuffle(array('ICA','SPF','MHA','MOH')), 1, cast(uag*3 as int)) AS L1_Agencies,
      slice(shuffle(array('scam','welfare concern','language barrier','MP/POH escalation')), 1, cast(ufl*2.5 as int)) AS L1_Situational_Flags,
      CASE WHEN Case_Status='Closed' THEN cast(4 + ud*18 as int) ELSE cast(null as int) END AS Resolution_Days,
      concat(element_at(array('Wei Ming','Siti','Arjun','Rachel','Kai','Nurul','Priya','Farah','Daniel','Mei Ling','Ravi','Marcus'), cast(un1*12 as int)+1),
             ' ', element_at(array('Tan','Lim','Kumar','Rahman','Ng','Wong','Pillai','Goh','Chua','Lee','Ong','Nair'), cast(un2*12 as int)+1)) AS Title_Name,
      'Singapore' AS Citizenship,
      concat(Case_Type, ' case involving a Singaporean in ', L1_Country, '.') AS Case_Description,
      concat('Handled by the ', Assigned_HCG, ' mission. ', Case_Type, ' in ', L1_Country,
             '; consular assistance provided and next-of-kin kept informed.') AS L2_Summary_Pathway,
      'Requested consular assistance; provided welfare checks, liaison with local authorities, and guidance to next-of-kin.' AS L2_Assistance_Req_vs_Provided,
      CASE WHEN ux<0.3 THEN 'Delays due to local processing timelines.' ELSE 'None noted.' END AS L2_Complications_Delays,
      'Local authorities and, where relevant, external SG agencies engaged.' AS L2_External_Resources,
      'Timely mission-HQ coordination improves case outcomes.' AS L2_Lessons_Learnt,
      cast(cdt as string) AS Created_On
    FROM m
    """)

    # SOP chunks (drive the record-view "recommended next steps").
    values = ",\n".join(
        "('{t}', '{t}', '{c}')".format(
            t=t.replace("'", "''"),
            c=("Consular SOP - " + t + "\\n" +
               "\\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))).replace("'", "''"))
        for t, steps in SOPS.items())
    run(f"CREATE OR REPLACE TABLE {FQ}.gold_sop_chunks (chunk_id STRING, sop_title STRING, chunk STRING)")
    run(f"INSERT INTO {FQ}.gold_sop_chunks VALUES\n{values}")

    # Operational tables the app writes to.
    run(f"CREATE TABLE IF NOT EXISTS {FQ}.case_notes (note_id STRING, Case_Ref STRING, author STRING, note STRING, created_at TIMESTAMP)")
    run(f"CREATE TABLE IF NOT EXISTS {FQ}.case_steps (Case_Ref STRING, step_idx INT, updated_by STRING, updated_at TIMESTAMP)")
    run(f"CREATE TABLE IF NOT EXISTS {FQ}.chat_messages (conv_id STRING, user_email STRING, role STRING, content STRING, route STRING, created_at TIMESTAMP)")
    run(f"CREATE TABLE IF NOT EXISTS {FQ}.chat_genie (conv_id STRING, genie_conv_id STRING, updated_at TIMESTAMP)")

    total = run(f"SELECT count(*) c FROM {FQ}.gold_case_intelligence").result.data_array[0][0]
    dist = run(f"SELECT Case_Type, count(*) FROM {FQ}.gold_case_intelligence GROUP BY Case_Type ORDER BY 2 DESC").result.data_array
    print(f"\nBuilt {FQ}.gold_case_intelligence with {total} synthetic cases.")
    print("By type:", {r[0]: int(r[1]) for r in dist})
    print(f"SOP + operational tables ready. Point the app at CCMS_CATALOG={catalog}, CCMS_SCHEMA={schema}.")
    print("Next: run install.py steps for Genie space, Vector Search indexes, and app deploy.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build synthetic Consular Case Intelligence tables.")
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse", required=True, help="SQL warehouse id")
    p.add_argument("--profile", default="DEFAULT")
    p.add_argument("--cases", type=int, default=400)
    a = p.parse_args()
    build(a.catalog, a.schema, a.warehouse, a.profile, a.cases)
