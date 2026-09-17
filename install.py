# Databricks notebook source
# MAGIC %md
# MAGIC # MFA CCMS — Consular Case Intelligence · One-Click Installer
# MAGIC
# MAGIC Stands up the **entire platform** in this workspace: schema & volumes → document parsing →
# MAGIC Layer 1 tags → Layer 2 analytics → governance (PII masks) → Vector Search → Genie space →
# MAGIC AI/BI dashboard → the Case Intelligence **app**.
# MAGIC
# MAGIC **Deploying in a customer environment with real data:** set `data_mode = byo`, drop the
# MAGIC customer's case-email PDFs into the `raw_emails` volume (or point `source_pdf_path` at an
# MAGIC existing location), and run top-to-bottom. Everything downstream is derived automatically.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - Run on **serverless** or a cluster with **DBR 17.3+** (needs `ai_parse_document`).
# MAGIC - A **Pro/Serverless SQL warehouse** id (for Genie / app).
# MAGIC - Permission to create catalogs/schemas/volumes, Vector Search endpoints, Genie spaces, and Apps.
# MAGIC - This notebook lives in a **Git folder** (so the `app/` code sits next to it for deployment).

# COMMAND ----------

# MAGIC %pip install --quiet --upgrade databricks-sdk fpdf2
# MAGIC %restart_python

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("catalog", "mfa_ccms", "Catalog")
dbutils.widgets.text("schema", "consular", "Schema")
dbutils.widgets.text("warehouse_id", "", "SQL Warehouse id (for Genie + app)")
dbutils.widgets.dropdown("ingest_mode", "pdf", ["pdf", "parsed_table", "case_table"], "Ingest mode")
dbutils.widgets.text("source_table", "", "Existing table (for parsed_table / case_table modes)")
dbutils.widgets.text("source_case_ref_col", "case_ref", "parsed_table: case-ref column")
dbutils.widgets.text("source_text_col", "parsed_text", "parsed_table: email-text column")
dbutils.widgets.dropdown("data_mode", "synthetic", ["synthetic", "byo"], "pdf mode: synthetic or BYO")
dbutils.widgets.text("source_pdf_path", "", "BYO: existing PDF folder (blank = use raw_emails volume)")
dbutils.widgets.text("llm_endpoint", "databricks-claude-sonnet-4-5", "LLM endpoint")
dbutils.widgets.text("embedding_endpoint", "databricks-gte-large-en", "Embedding endpoint")
dbutils.widgets.text("app_name", "mfa-ccms-intel", "Databricks App name")
dbutils.widgets.dropdown("build_app", "yes", ["yes", "no"], "Deploy the app?")

CAT = dbutils.widgets.get("catalog").strip()
SCH = dbutils.widgets.get("schema").strip()
FQ = f"{CAT}.{SCH}"
WAREHOUSE = dbutils.widgets.get("warehouse_id").strip()
INGEST_MODE = dbutils.widgets.get("ingest_mode")           # pdf | parsed_table | case_table
SOURCE_TABLE = dbutils.widgets.get("source_table").strip()
SRC_REF_COL = dbutils.widgets.get("source_case_ref_col").strip() or "case_ref"
SRC_TXT_COL = dbutils.widgets.get("source_text_col").strip() or "parsed_text"
DATA_MODE = dbutils.widgets.get("data_mode")
SRC_PDF = dbutils.widgets.get("source_pdf_path").strip()
LLM = dbutils.widgets.get("llm_endpoint").strip()
EMB = dbutils.widgets.get("embedding_endpoint").strip()
APP_NAME = dbutils.widgets.get("app_name").strip()
BUILD_APP = dbutils.widgets.get("build_app") == "yes"

VOL_EMAILS = f"/Volumes/{CAT}/{SCH}/raw_emails"
VOL_SOP = f"/Volumes/{CAT}/{SCH}/sop_corpus"
VS_ENDPOINT = f"{SCH}_vs"[:60]
IDX_EMAIL = f"{FQ}.case_email_index"
IDX_SOP = f"{FQ}.sop_index"
print(f"Target {FQ} | ingest={INGEST_MODE} | LLM={LLM} | warehouse={WAREHOUSE or '(required for Genie/app)'}")
if INGEST_MODE in ("parsed_table", "case_table") and not SOURCE_TABLE:
    raise ValueError(f"ingest_mode={INGEST_MODE} requires source_table to be set.")

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()

# COMMAND ----------

# DBTITLE 1,1 · Schema, volumes, base case table (the provided 52-column CCMS schema)
COLUMNS = [
    ("Do_Not_Modify_Case", "STRING"), ("Do_Not_Modify_Row_Checksum", "STRING"),
    ("Do_Not_Modify_Modified_On", "TIMESTAMP"), ("Title_Name", "STRING"), ("MFA_Ref", "STRING"),
    ("Created_On", "STRING"), ("Origin", "STRING"), ("Additional_Information", "STRING"),
    ("Advice_Provided___Follow_up", "STRING"), ("Allow_Case_History_Access", "STRING"),
    ("Allow_LRS_Access", "STRING"), ("Assigned_HCG", "STRING"), ("Assistance_Required", "STRING"),
    ("Caller___Informant", "STRING"), ("Case_Description", "STRING"), ("Case_Handler", "STRING"),
    ("Case_Location", "STRING"), ("Case_Ref", "STRING"), ("Case_Status", "STRING"),
    ("Case_Subject", "STRING"), ("Case_Title", "STRING"), ("Case_Type", "STRING"),
    ("Category_Multiselect", "STRING"), ("Citizenship", "STRING"), ("Contact_Tracing_Status", "STRING"),
    ("Covering_MP", "STRING"), ("Current_Location", "STRING"), ("Evacuation_Status", "STRING"),
    ("Feedback_Date", "STRING"), ("Feedback_Message", "STRING"), ("Feedback_Provider", "STRING"),
    ("Feedback_Type", "STRING"), ("Handled_By", "STRING"), ("HCG_Assigned_Date", "STRING"),
    ("Incident_ID", "STRING"), ("LRS_Communication", "STRING"), ("LRS_Portal_User_Modify_By", "STRING"),
    ("LRS_Portal_User_Modify_On", "STRING"), ("LRS_Update", "STRING"), ("Media_Query", "STRING"),
    ("Media_Response", "STRING"), ("Modified_On", "STRING"), ("MP_Constituency_Division", "STRING"),
    ("MP_Name", "STRING"), ("New_Case", "STRING"), ("New_Email", "STRING"),
    ("No_of_Open_Child_Cases", "STRING"), ("Parent_Case", "STRING"), ("Recipient_of_Feedback", "STRING"),
    ("Registrant", "STRING"), ("Sub_Category_Multiselect", "STRING"), ("Tag", "STRING"),
]
spark.sql(f"CREATE CATALOG IF NOT EXISTS {CAT}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {FQ} COMMENT 'MFA CCMS Consular Case Intelligence'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {FQ}.raw_emails")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {FQ}.sop_corpus")
cols_ddl = ",\n  ".join(f"{n} {t}" for n, t in COLUMNS)
spark.sql(f"CREATE TABLE IF NOT EXISTS {FQ}.case_extracted (\n  {cols_ddl}\n)")
print(f"Schema + volumes + case_extracted ({len(COLUMNS)} cols) ready.")

# COMMAND ----------

# DBTITLE 1,2 · Data — synthetic generator OR bring-your-own PDFs (pdf mode only)
import os, subprocess, sys
if INGEST_MODE != "pdf":
    print(f"ingest_mode={INGEST_MODE}: skipping PDF ingest; using existing table {SOURCE_TABLE}.")
elif DATA_MODE == "synthetic":
    # Reuse the repo's generator, writing PDFs straight into the volumes.
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    REPO_ROOT = "/Workspace" + nb.rsplit("/", 1)[0]
    sys.path.insert(0, REPO_ROOT)
    import importlib
    gen = importlib.import_module("pipeline.01_generate_data")
    gen.EMAIL_DIR, gen.SOP_DIR = VOL_EMAILS, VOL_SOP
    gen.DATA = "/tmp/mfa_data"; os.makedirs(gen.DATA, exist_ok=True)
    os.makedirs(VOL_EMAILS, exist_ok=True); os.makedirs(VOL_SOP, exist_ok=True)
    gen.generate()
    print("Synthetic PDFs written to the volumes.")
else:
    if SRC_PDF:
        print(f"BYO mode: copying PDFs from {SRC_PDF} -> {VOL_EMAILS}")
        dbutils.fs.cp(SRC_PDF, VOL_EMAILS.replace("/Volumes", "dbfs:/Volumes"), recurse=True)
    n = len([f for f in dbutils.fs.ls(VOL_EMAILS.replace("/Volumes", "dbfs:/Volumes")) if f.name.lower().endswith(".pdf")])
    print(f"BYO mode: {n} PDF(s) present in {VOL_EMAILS}. "
          f"(Drop the customer's case-email PDFs here, and optionally SOP PDFs in {VOL_SOP}.)")

# COMMAND ----------

# DBTITLE 1,3 · Ingest text -> bronze_email_parsed(case_ref, parsed_text)
if INGEST_MODE == "pdf":
    # Parse PDFs with ai_parse_document.
    spark.sql(f"""
    CREATE OR REPLACE TABLE {FQ}.bronze_email_parsed AS
    SELECT path, regexp_extract(txt, '\\\\[([A-Za-z0-9/_-]+)\\\\]', 1) AS case_ref_hint,
      txt AS parsed_text, err AS parse_error
    FROM (
      SELECT path,
        concat_ws('\\n', transform(variant_get(parsed,'$.document.elements','ARRAY<VARIANT>'),
          e -> variant_get(e,'$.content','STRING'))) AS txt,
        parsed:error_status AS err
      FROM (SELECT path, ai_parse_document(content) AS parsed
            FROM read_files('{VOL_EMAILS}/', format => 'binaryFile')))
    """)
    spark.sql(f"""CREATE OR REPLACE TABLE {FQ}.bronze_email_parsed AS
    SELECT *, coalesce(nullif(case_ref_hint,''), regexp_extract(path,'([^/]+)\\\\.pdf$',1)) AS case_ref
    FROM {FQ}.bronze_email_parsed""")

elif INGEST_MODE == "parsed_table":
    # Customer already parsed the emails — point straight at their table.
    spark.sql(f"""CREATE OR REPLACE TABLE {FQ}.bronze_email_parsed AS
    SELECT `{SRC_REF_COL}` AS case_ref, `{SRC_TXT_COL}` AS parsed_text FROM {SOURCE_TABLE}""")

elif INGEST_MODE == "case_table":
    # Customer already built the structured case table — adopt it as case_extracted,
    # and derive a narrative text column for Layer 2 + similar-case search.
    spark.sql(f"CREATE OR REPLACE TABLE {FQ}.case_extracted AS SELECT * FROM {SOURCE_TABLE}")
    spark.sql(f"""CREATE OR REPLACE TABLE {FQ}.bronze_email_parsed AS
    SELECT Case_Ref AS case_ref,
      concat_ws(' ', coalesce(Case_Subject,''), coalesce(Case_Title,''), coalesce(Case_Description,''),
        coalesce(Additional_Information,''), coalesce(Assistance_Required,''),
        coalesce(Advice_Provided___Follow_up,'')) AS parsed_text
    FROM {FQ}.case_extracted""")

print("bronze_email_parsed rows:", spark.sql(f"SELECT count(*) c FROM {FQ}.bronze_email_parsed").first().c)

# COMMAND ----------

# DBTITLE 1,4 · Layer 1 extraction -> case_extracted (provided schema)
L1_FIELDS = ["Title_Name","Case_Type","Case_Status","Case_Handler","Assigned_HCG","Handled_By",
 "Case_Location","Current_Location","Citizenship","Origin","Case_Title","Case_Subject",
 "Case_Description","Assistance_Required","Advice_Provided___Follow_up","Category_Multiselect",
 "Sub_Category_Multiselect","Tag","Caller___Informant","Registrant","MP_Name","Covering_MP",
 "MP_Constituency_Division","Incident_ID","Created_On"]
STRUCT1 = "STRUCT<" + ", ".join(f"{f}:STRING" for f in L1_FIELDS) + ">"
P1 = ("You are extracting structured fields from a consular case email thread. Return ONLY a JSON "
 "object with these keys: " + ", ".join(L1_FIELDS) + ". Apply a value ONLY when the email explicitly "
 "supports it; use null otherwise. Case_Status is Closed or Pending. Case_Handler is the HQ unit. "
 "Assigned_HCG is the responsible overseas mission. Case_Location is 'City, Country'. Created_On is "
 "the first message date as YYYY-MM-DD. Email thread:\\n").replace("'", "''")

if INGEST_MODE == "case_table":
    print("case_table mode: case_extracted supplied by the customer — skipping Layer 1 extraction.")
else:
    spark.sql(f"""
    CREATE OR REPLACE TABLE {FQ}._l1_json AS
    SELECT case_ref, parsed_text,
      from_json(regexp_extract(
        ai_query('{LLM}', concat('{P1}', parsed_text), failOnError => false).result, '(?s)[{{].*[}}]', 0),
        '{STRUCT1}') AS js
    FROM {FQ}.bronze_email_parsed
    """)
    # Map to the exact schema (INSERT BY NAME). System/audit cols set here; unlisted cols -> NULL.
    exprs = {f: f"js.{f}" for f in L1_FIELDS}
    exprs.update({
      "Do_Not_Modify_Case": "case_ref", "Case_Ref": "case_ref",
      "Do_Not_Modify_Modified_On": "current_timestamp()", "Modified_On": "cast(current_timestamp() as string)",
      "New_Case": "'Yes'", "No_of_Open_Child_Cases": "'0'",
      "Allow_Case_History_Access": "'Yes'", "Allow_LRS_Access": "'No'",
    })
    business = [c for c, _ in COLUMNS if not c.startswith("Do_Not_Modify_")]
    exprs["Do_Not_Modify_Row_Checksum"] = ("sha2(concat_ws('|', "
      + ", ".join(f"coalesce({exprs.get(c,'cast(null as string)')},'')" for c in business) + "), 256)")
    select_cols = ",\n  ".join(f"{exprs.get(c,'cast(null as string)')} AS {c}" for c, _ in COLUMNS)
    spark.sql(f"TRUNCATE TABLE {FQ}.case_extracted")
    spark.sql(f"INSERT INTO {FQ}.case_extracted BY NAME\nSELECT\n  {select_cols}\nFROM {FQ}._l1_json")
    print("case_extracted rows:", spark.sql(f"SELECT count(*) c FROM {FQ}.case_extracted").first().c)

# COMMAND ----------

# DBTITLE 1,5 · Layer 1 derived tags + Layer 2 analytics -> gold_case_intelligence
L2_FIELDS = {"L1_Country":"STRING","L1_Mission":"STRING","L1_Case_Type":"STRING",
 "L1_Agencies":"ARRAY<STRING>","L1_Situational_Flags":"ARRAY<STRING>","L1_Status":"STRING",
 "L2_Summary_Pathway":"STRING","L2_Assistance_Req_vs_Provided":"STRING","L2_Complications_Delays":"STRING",
 "L2_External_Resources":"STRING","L2_Lessons_Learnt":"STRING"}
STRUCT2 = "STRUCT<" + ", ".join(f"{k}:{v}" for k, v in L2_FIELDS.items()) + ">"
P2 = ("You are a consular analyst. Read the case email thread and return ONLY a JSON object with keys: "
 + ", ".join(L2_FIELDS) + ". LAYER 1 (evidence-only, null/[] if not stated): L1_Country; L1_Mission "
 "(responsible unit); L1_Case_Type; L1_Agencies (array of external agencies mentioned); "
 "L1_Situational_Flags (array, e.g. scam, MP/POH escalation, language barrier, welfare concern); "
 "L1_Status (closed/pending). LAYER 2 (concise free text): L2_Summary_Pathway; "
 "L2_Assistance_Req_vs_Provided; L2_Complications_Delays; L2_External_Resources; L2_Lessons_Learnt. "
 "Email thread:\\n").replace("'", "''")

spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}._l2_json AS
SELECT case_ref, parsed_text,
  from_json(regexp_extract(
    ai_query('{LLM}', concat('{P2}', parsed_text), failOnError => false).result, '(?s)[{{].*[}}]', 0),
    '{STRUCT2}') AS e
FROM {FQ}.bronze_email_parsed
""")
l1l2 = ",\n  ".join(f"e.e.{c} AS {c}" for c in L2_FIELDS)
dates = "regexp_extract_all(l.parsed_text, '([0-9]{1,2} [A-Za-z]{3} [0-9]{4})', 1)"
res = (f"CASE WHEN lower(ce.Case_Status)='closed' AND size({dates})>=2 THEN "
       f"datediff(to_date(element_at({dates},-1),'d MMM yyyy'), to_date(element_at({dates},1),'d MMM yyyy')) "
       f"ELSE cast(null as int) END")
spark.sql(f"""
CREATE OR REPLACE TABLE {FQ}.gold_case_intelligence AS
SELECT ce.*, {l1l2}, coalesce(ce.MFA_Ref, ce.Case_Ref) AS Case_Id, {res} AS Resolution_Days
FROM {FQ}.case_extracted ce
JOIN {FQ}._l2_json e ON ce.Case_Ref = e.case_ref
JOIN {FQ}.bronze_email_parsed l ON ce.Case_Ref = l.case_ref
""")
print("gold_case_intelligence rows:", spark.sql(f"SELECT count(*) c FROM {FQ}.gold_case_intelligence").first().c)

# COMMAND ----------

# DBTITLE 1,6 · Governance — PII column masks + accuracy view
spark.sql(f"""CREATE OR REPLACE FUNCTION {FQ}.mask_pii(v STRING)
RETURN CASE WHEN is_account_group_member('consular_privileged') THEN v ELSE '***REDACTED***' END""")
for col in ["Title_Name", "Caller___Informant", "Registrant", "New_Email"]:
    spark.sql(f"ALTER TABLE {FQ}.gold_case_intelligence ALTER COLUMN {col} SET MASK {FQ}.mask_pii")
spark.sql(f"""CREATE OR REPLACE VIEW {FQ}.tag_accuracy AS
SELECT round(avg(CASE WHEN lower(trim(L1_Case_Type))=lower(trim(Case_Type)) THEN 1.0 ELSE 0.0 END),3) AS case_type_agreement,
       count(*) AS n_cases FROM {FQ}.gold_case_intelligence
WHERE Case_Type IS NOT NULL AND L1_Case_Type IS NOT NULL""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.case_notes
 (note_id STRING, Case_Ref STRING, author STRING, note STRING, created_at TIMESTAMP)""")
print("Masks applied; tag_accuracy + case_notes ready.")

# COMMAND ----------

# DBTITLE 1,7 · Vector Search — PII-scrubbed email index + SOP index
from databricks.sdk.service.vectorsearch import (DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn, EndpointType, PipelineType, VectorIndexType)
import time

spark.sql(f"""CREATE OR REPLACE TABLE {FQ}.gold_email_chunks AS
SELECT ce.Case_Ref AS chunk_id, ce.Case_Ref AS case_ref, ce.Case_Type AS case_type,
  ce.Case_Location AS case_location,
  replace(regexp_replace(l.parsed_text, '[STFG][0-9]{{7}}[A-Z]', '[NRIC]'), ce.Title_Name, '[NAME]') AS chunk
FROM {FQ}.case_extracted ce JOIN {FQ}.bronze_email_parsed l ON ce.Case_Ref=l.case_ref""")
spark.sql(f"ALTER TABLE {FQ}.gold_email_chunks SET TBLPROPERTIES (delta.enableChangeDataFeed=true)")
spark.sql(f"""CREATE OR REPLACE TABLE {FQ}.gold_sop_chunks AS
SELECT regexp_extract(path,'([^/]+)\\\\.pdf$',1) AS chunk_id, regexp_extract(path,'SOP_(.+)\\\\.pdf$',1) AS sop_title,
  concat_ws('\\n', transform(variant_get(ai_parse_document(content),'$.document.elements','ARRAY<VARIANT>'),
    e -> variant_get(e,'$.content','STRING'))) AS chunk
FROM read_files('{VOL_SOP}/', format => 'binaryFile')""")
spark.sql(f"ALTER TABLE {FQ}.gold_sop_chunks SET TBLPROPERTIES (delta.enableChangeDataFeed=true)")

if VS_ENDPOINT not in [e.name for e in w.vector_search_endpoints.list_endpoints()]:
    w.vector_search_endpoints.create_endpoint(name=VS_ENDPOINT, endpoint_type=EndpointType.STANDARD)
for _ in range(60):
    if w.vector_search_endpoints.get_endpoint(VS_ENDPOINT).endpoint_status.state.value == "ONLINE":
        break
    time.sleep(20)
def _mk_index(name, src):
    if name not in [i.name for i in w.vector_search_indexes.list_indexes(VS_ENDPOINT)]:
        w.vector_search_indexes.create_index(name=name, endpoint_name=VS_ENDPOINT, primary_key="chunk_id",
          index_type=VectorIndexType.DELTA_SYNC,
          delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(source_table=src,
            pipeline_type=PipelineType.TRIGGERED,
            embedding_source_columns=[EmbeddingSourceColumn(name="chunk", embedding_model_endpoint_name=EMB)]))
_mk_index(IDX_EMAIL, f"{FQ}.gold_email_chunks")
if spark.sql(f"SELECT count(*) c FROM {FQ}.gold_sop_chunks").first().c:
    _mk_index(IDX_SOP, f"{FQ}.gold_sop_chunks")
for idx in [IDX_EMAIL, IDX_SOP]:
    try:
        for _ in range(90):
            if getattr(w.vector_search_indexes.get_index(idx).status, "ready", False): break
            time.sleep(20)
    except Exception:
        pass
print("Vector Search indexes ready on endpoint", VS_ENDPOINT)

# COMMAND ----------

# DBTITLE 1,8 · Genie space
import json, uuid
GOLD, ACC = f"{FQ}.gold_case_intelligence", f"{FQ}.tag_accuracy"
def nid(): return uuid.uuid4().hex
ser = {"version": 2,
  "config": {"sample_questions": sorted([{"id": nid(), "question": [q]} for q in [
    "How many cases by type?", "Handling volume by mission",
    "Average resolution time by case type", "Cases where ICA was involved"]], key=lambda x: x["id"])},
  "data_sources": {"tables": sorted([{"identifier": GOLD}, {"identifier": ACC}], key=lambda x: x["identifier"])},
  "instructions": {"text_instructions": [{"id": nid(), "content": [
    "Each row is one consular case (Case_Ref/Case_Id).",
    "L1_Agencies and L1_Situational_Flags are ARRAYs — use array_contains().",
    "'mission'/'post' = Assigned_HCG or L1_Mission; Resolution_Days is set for closed cases.",
    "Created_On is a YYYY-MM-DD string — wrap with to_date() for date math."]}]}}
GENIE_SPACE_ID = ""
if WAREHOUSE:
    me = w.current_user.me().user_name
    parent = f"/Workspace/Users/{me}/genie_spaces"
    w.workspace.mkdirs(parent)
    resp = w.api_client.do("POST", "/api/2.0/genie/spaces", body={
        "warehouse_id": WAREHOUSE, "title": "Consular Case Intelligence Genie",
        "description": "NL analytics over consular cases.", "parent_path": parent,
        "serialized_space": json.dumps(ser)})
    GENIE_SPACE_ID = resp.get("space_id") or resp.get("id", "")
    print("Genie space:", GENIE_SPACE_ID)
else:
    print("Skipped Genie (set warehouse_id to enable).")

# COMMAND ----------

# DBTITLE 1,9 · AI/BI dashboard
DASHBOARD_ID = ""
if WAREHOUSE:
    def ds(name, sql): return {"name": name, "displayName": name, "queryLines": [sql]}
    def bar(dsn, x, y, agg, title, pos):
        return {"widget": {"name": name_id(), "queries": [{"name": "main_query", "query": {
            "datasetName": dsn, "fields": [{"name": x, "expression": f"`{x}`"},
            {"name": f"{agg.lower()}({y})", "expression": f"{agg}(`{y}`)"}], "disaggregated": False}}],
          "spec": {"version": 3, "widgetType": "bar", "encodings": {
            "x": {"fieldName": x, "scale": {"type": "categorical"}},
            "y": {"fieldName": f"{agg.lower()}({y})", "scale": {"type": "quantitative"}}},
            "frame": {"showTitle": True, "title": title}}}, "position": pos}
    import uuid as _u
    def name_id(): return _u.uuid4().hex[:8]
    datasets = [ds("cases", f"SELECT Case_Type, L1_Country, Assigned_HCG, Case_Status, Resolution_Days FROM {GOLD}")]
    layout = [
      bar("cases", "Case_Type", "Case_Type", "COUNT", "Cases by type", {"x":0,"y":0,"width":3,"height":6}),
      bar("cases", "L1_Country", "L1_Country", "COUNT", "Cases by country", {"x":3,"y":0,"width":3,"height":6}),
      bar("cases", "Assigned_HCG", "Assigned_HCG", "COUNT", "Volume by mission", {"x":0,"y":6,"width":3,"height":6}),
      bar("cases", "Case_Type", "Resolution_Days", "AVG", "Avg resolution days", {"x":3,"y":6,"width":3,"height":6}),
    ]
    dash = {"datasets": datasets, "pages": [{"name": name_id(), "displayName": "Overview",
            "pageType": "PAGE_TYPE_CANVAS", "layout": layout}]}
    me = w.current_user.me().user_name
    resp = w.api_client.do("POST", "/api/2.0/lakeview/dashboards", body={
        "display_name": "Consular Case Intelligence", "warehouse_id": WAREHOUSE,
        "parent_path": f"/Users/{me}", "serialized_dashboard": json.dumps(dash)})
    DASHBOARD_ID = resp.get("dashboard_id", "")
    try: w.api_client.do("POST", f"/api/2.0/lakeview/dashboards/{DASHBOARD_ID}/published",
                         body={"warehouse_id": WAREHOUSE})
    except Exception as e: print("publish note:", e)
    print("Dashboard:", DASHBOARD_ID)
else:
    print("Skipped dashboard (set warehouse_id to enable).")

# COMMAND ----------

# DBTITLE 1,10 · Deploy the Case Intelligence app (with OBO scopes + SP grants)
if BUILD_APP and WAREHOUSE:
    from databricks.sdk.service.apps import App, AppDeployment
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    APP_SRC = "/Workspace" + nb.rsplit("/", 1)[0] + "/app"
    # Write app config for this environment.
    app_yaml = f"""command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
env:
  - {{name: CCMS_CATALOG, value: "{CAT}"}}
  - {{name: CCMS_SCHEMA, value: "{SCH}"}}
  - {{name: DATABRICKS_WAREHOUSE_ID, value: "{WAREHOUSE}"}}
  - {{name: GENIE_SPACE_ID, value: "{GENIE_SPACE_ID}"}}
  - {{name: IDX_EMAIL, value: "{IDX_EMAIL}"}}
  - {{name: IDX_SOP, value: "{IDX_SOP}"}}
  - {{name: CCMS_LLM, value: "{LLM}"}}
"""
    from databricks.sdk.service.workspace import ImportFormat
    # Overwrite app.yaml in the Git-folder app path with this environment's config.
    w.workspace.upload(f"{APP_SRC}/app.yaml", app_yaml.encode(), format=ImportFormat.RAW, overwrite=True)
    try:
        w.apps.get(name=APP_NAME)
    except Exception:
        w.apps.create_and_wait(app=App(name=APP_NAME))
    # Request OBO scopes.
    try:
        w.api_client.do("PATCH", f"/api/2.0/apps/{APP_NAME}",
                        body={"name": APP_NAME, "user_api_scopes": ["sql", "dashboards.genie"]})
    except Exception as e: print("scopes note:", e)
    dep = w.apps.deploy_and_wait(app_name=APP_NAME,
        app_deployment=AppDeployment(source_code_path=APP_SRC))
    sp = w.apps.get(name=APP_NAME).service_principal_client_id
    # SP grants for the shared (VS) path.
    for g in [f"GRANT SELECT ON TABLE {IDX_EMAIL} TO `{sp}`",
              f"GRANT SELECT ON TABLE {IDX_SOP} TO `{sp}`"]:
        try: spark.sql(g)
        except Exception as e: print("grant note:", e)
    try:
        epid = w.vector_search_endpoints.get_endpoint(VS_ENDPOINT).id
        w.api_client.do("PATCH", f"/api/2.0/permissions/vector-search-endpoints/{epid}",
            body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_USE"}]})
        w.api_client.do("PATCH", f"/api/2.0/permissions/warehouses/{WAREHOUSE}",
            body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_USE"}]})
    except Exception as e: print("permission note:", e)
    print("App deployed:", w.apps.get(name=APP_NAME).url)
else:
    print("Skipped app (needs build_app=yes and a warehouse_id).")

# COMMAND ----------

# DBTITLE 1,Summary
print(f"""
Install complete — {FQ}
  Gold table   : {FQ}.gold_case_intelligence  ({spark.sql(f'SELECT count(*) c FROM {FQ}.gold_case_intelligence').first().c} cases)
  Accuracy     : {FQ}.tag_accuracy
  VS endpoint  : {VS_ENDPOINT}  ({IDX_EMAIL}, {IDX_SOP})
  Genie space  : {GENIE_SPACE_ID or '(set warehouse_id)'}
  Dashboard    : {DASHBOARD_ID or '(set warehouse_id)'}
  App          : {APP_NAME if (BUILD_APP and WAREHOUSE) else '(skipped)'}

For CUSTOMER data: set data_mode=byo, drop their case-email PDFs into {VOL_EMAILS}
(and SOP PDFs into {VOL_SOP}), set warehouse_id, and re-run top to bottom.
""")
