# MFA CCMS — Consular Case Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working demo that turns synthetic consular case-email PDFs into a structured, AI-enriched, governed case record served through Genie, an AI/BI dashboard, and a Python + Claude Sonnet chat app.

**Architecture:** Medallion pipeline in `kauvey_poc.mfa_ccms` — synthetic PDFs land in a UC Volume (Bronze), `ai_parse_document` extracts clean text, `ai_query` populates the provided CCMS schema (Silver) and adds Layer 1 tags + Layer 2 analytical fields (Gold). Gold feeds UC column masks, two Vector Search indexes, a Genie space, an AI/BI dashboard, and a Databricks App.

**Tech Stack:** Databricks CLI (DEFAULT profile), SQL warehouse `<warehouse-id>`, AI Functions (`ai_parse_document`, `ai_query`), `databricks-claude-sonnet-4-5`, `databricks-gte-large-en`, Vector Search, Genie, AI/BI (Lakeview) dashboards, Databricks Apps (Python), local Python for PDF generation (`fpdf2`).

**Spec:** `docs/specs/2026-09-17-mfa-ccms-consular-case-intel-design.md`

## Global Constraints

- Catalog/schema: `kauvey_poc.mfa_ccms` — names literal, never normalized.
- CLI profile: always pass `--profile DEFAULT`. Never auto-select another profile.
- SQL warehouse for all SQL / AI Functions: `<warehouse-id>`.
- LLM for all extraction/enrichment/agent: `databricks-claude-sonnet-4-5`. Embeddings: `databricks-gte-large-en`. No external (non-Databricks) model calls.
- Provided CCMS schema (Silver) reproduced **exactly** — all columns `string` except `Do_Not_Modify_Modified_On` (timestamp). Column names verbatim from the spec §4.1.
- Evidence-only tagging: apply a Layer 1 tag only when the email narrative explicitly supports it; otherwise null / empty array.
- All synthetic data fully fabricated — no real names, NRICs, or contacts.
- Git commits end with: `Co-authored-by: Isaac <no-reply@databricks.com>`.
- SQL is executed via `databricks experimental aitools tools query "<SQL>" --profile DEFAULT` (or the repo helper `pipeline/dbsql.py`). Names with hyphens must be backtick-quoted.

---

## File Structure

```
~/mfa_ccms_demo/
├── docs/specs/2026-09-17-...-design.md         # the approved spec
├── docs/plans/2026-09-17-...-plan.md           # this plan
├── pipeline/
│   ├── dbsql.py            # thin SQL runner over the warehouse (reused pattern)
│   ├── config.py           # catalog/schema/model/warehouse constants
│   ├── 00_schema.sql       # schema, volume, silver/gold DDL
│   ├── 01_generate_data.py # synthetic email PDFs + SOP docs → local → volume
│   ├── 02_parse.sql        # ai_parse_document → bronze.email_parsed
│   ├── 03_extract_l1.sql   # ai_query → silver.case_extracted (provided schema)
│   ├── 04_enrich_l2.sql    # ai_query → gold.case_intelligence (+L1/L2/derived)
│   ├── 05_governance.sql   # column-mask functions + tag_accuracy view
│   ├── 06_indexes.py       # vector search endpoint + case_email_index + sop_index
│   ├── 07_dashboard.py     # Lakeview dashboard (5 views)
│   └── run_all.py          # orchestrator (runs 00→06 in order)
├── genie/instructions.md   # Genie space instructions + example SQL
├── app/
│   ├── app.py              # chat UI + case view + embedded dashboard
│   ├── agent.py            # tool-calling loop → Claude Sonnet endpoint
│   ├── tools.py            # genie_query, similar_cases, sop_lookup, get_case
│   ├── app.yaml            # Databricks App config
│   └── requirements.txt
└── data/                   # generated PDFs (gitignored)
```

---

## Task 0: Scaffold + config + SQL runner

**Files:**
- Create: `pipeline/config.py`, `pipeline/dbsql.py`

**Interfaces:**
- Produces: `config.CAT="kauvey_poc"`, `config.SCHEMA="mfa_ccms"`, `config.FQ="kauvey_poc.mfa_ccms"`, `config.WAREHOUSE="<warehouse-id>"`, `config.LLM="databricks-claude-sonnet-4-5"`, `config.EMB="databricks-gte-large-en"`, `config.PROFILE="DEFAULT"`.
- Produces: `dbsql.run(sql: str) -> list[dict]` — executes SQL on the warehouse via the SDK `StatementExecution` API and returns rows.

- [ ] **Step 1: Verify auth** — `databricks current-user me --profile DEFAULT` → expect `userName": "simran.vanjani@databricks.com"`.
- [ ] **Step 2: Write `config.py`** with the constants above.
- [ ] **Step 3: Write `dbsql.py`** — use `databricks.sdk.WorkspaceClient(profile="DEFAULT").statement_execution.execute_statement(warehouse_id=..., statement=sql, wait_timeout="50s")`, poll until `SUCCEEDED`, return `result.data_array` mapped to column names. Raise on `FAILED` with the error message.
- [ ] **Step 4: Smoke test** — `python -c "from pipeline import dbsql; print(dbsql.run('SELECT 1 AS x'))"` → expect `[{'x': '1'}]`.
- [ ] **Step 5: Commit** — `git add pipeline/config.py pipeline/dbsql.py && git commit`.

---

## Task 1: Schema, volume, and base table DDL

**Files:** Create `pipeline/00_schema.sql`

**Interfaces:**
- Produces: schema `kauvey_poc.mfa_ccms`; volume `kauvey_poc.mfa_ccms.raw_emails`; volume `kauvey_poc.mfa_ccms.sop_corpus`; empty table `kauvey_poc.mfa_ccms.case_extracted` with the exact provided schema.

- [ ] **Step 1: Write DDL** —
  - `CREATE SCHEMA IF NOT EXISTS kauvey_poc.mfa_ccms COMMENT 'MFA CCMS Consular Case Intelligence demo';`
  - `CREATE VOLUME IF NOT EXISTS kauvey_poc.mfa_ccms.raw_emails;`
  - `CREATE VOLUME IF NOT EXISTS kauvey_poc.mfa_ccms.sop_corpus;`
  - `CREATE TABLE IF NOT EXISTS kauvey_poc.mfa_ccms.case_extracted (` + all 52 columns from spec §4.1, all `STRING` except `Do_Not_Modify_Modified_On TIMESTAMP` `);` (Silver table; keep flat, no medallion sub-schemas — use table-name prefixes `bronze_`/`gold_` only where noted).
- [ ] **Step 2: Execute** each statement via `dbsql.run`.
- [ ] **Step 3: Verify** — `SHOW TABLES IN kauvey_poc.mfa_ccms` includes `case_extracted`; `DESCRIBE kauvey_poc.mfa_ccms.case_extracted` returns 52 columns with correct types.
- [ ] **Step 4: Commit.**

---

## Task 2: Synthetic case-email PDFs + SOP docs

**Files:** Create `pipeline/01_generate_data.py`

**Interfaces:**
- Consumes: `config`.
- Produces: ~120 PDFs in `data/emails/` and a manifest `data/manifest.json` (one record per case with the ground-truth `Case_Ref`, `Case_Type`, `Case_Handler`, country, agencies, flags — used later for accuracy sanity, NOT injected into extraction). Produces ~6 SOP text/PDF files in `data/sop/`. Uploads both dirs to the volumes.

- [ ] **Step 1: Define controlled vocab in code** — countries `[Thailand, China, Malaysia, Indonesia, UK, ...]` (with cities e.g. Guangzhou), case types `[Arrest & Detention, Serious Illness/Death, Victim of Crime, Lost/Stolen Document, Evacuation, Scam]`, units `[CRC, CON/OPS, Bangkok, Guangzhou, Kuala Lumpur, London]`, agencies `[ICA, SPF, MHA, MOH]`, flags `[scam, MP/POH escalation, language barrier, welfare concern]`.
- [ ] **Step 2: Write a case generator** — for each of ~120 cases pick a plausible combination + a random date in 2024–2026, fabricate a Singaporean-style name and NRIC-format string (`[STFG]\d{7}[A-Z]`), and render a **realistic multi-message email thread** (subject line `Re: [<Case_Ref>] <Case_Title>`, mailbox CC, 2–4 messages between officers describing incident → actions → resolution). The thread must *contain* the facts (case type, location, agencies involved, MP escalation if any) in prose so extraction has evidence. Vary which facts appear (some cases omit agency/flags) to exercise the evidence-only rule.
- [ ] **Step 3: Render to PDF** with `fpdf2` (add to `requirements.txt`). Filename `<Case_Ref>.pdf`.
- [ ] **Step 4: Write SOP docs** — one short procedure per case type (steps an officer follows), as PDFs in `data/sop/`.
- [ ] **Step 5: Upload** — `databricks fs cp -r data/emails/ dbfs:/Volumes/kauvey_poc/mfa_ccms/raw_emails/ --profile DEFAULT` (and `--overwrite`); same for `data/sop/` → `sop_corpus`. (Use the Volumes path form `/Volumes/...`.)
- [ ] **Step 6: Verify** — `databricks fs ls dbfs:/Volumes/kauvey_poc/mfa_ccms/raw_emails/ --profile DEFAULT` shows ~120 PDFs; `SELECT count(*) FROM READ_FILES('/Volumes/kauvey_poc/mfa_ccms/raw_emails/', format=>'binaryFile')` ≈ 120.
- [ ] **Step 7: Commit** (data/ is gitignored; commit the generator only).

---

## Task 3: Parse PDFs → `bronze_email_parsed`

**Files:** Create `pipeline/02_parse.sql`

**Interfaces:**
- Consumes: volume `raw_emails`.
- Produces: table `kauvey_poc.mfa_ccms.bronze_email_parsed(path string, case_ref string, parsed_text string)`.

> Ground `ai_parse_document` syntax against the `databricks:databricks-ai-functions` skill before writing.

- [ ] **Step 1: Write parse SQL** —
```sql
CREATE OR REPLACE TABLE kauvey_poc.mfa_ccms.bronze_email_parsed AS
SELECT
  path,
  regexp_extract(path, '([^/]+)\\.pdf$', 1) AS case_ref,
  ai_parse_document(content) AS parsed
FROM READ_FILES('/Volumes/kauvey_poc/mfa_ccms/raw_emails/', format => 'binaryFile');
```
  then flatten `parsed` to `parsed_text` (the markdown/text field of the parse result) in a follow-up `CREATE OR REPLACE TABLE ... AS SELECT path, case_ref, parsed:document:pages ... ` per the skill's documented output shape.
- [ ] **Step 2: Execute** (run on a small LIMIT first: create a `_sample` table over 3 files, inspect `parsed_text` non-empty and readable).
- [ ] **Step 3: Run full** parse.
- [ ] **Step 4: Verify** — `SELECT count(*), count(parsed_text) FROM bronze_email_parsed` ≈ 120/120; spot-check one `parsed_text` contains the case narrative.
- [ ] **Step 5: Commit.**

---

## Task 4: Layer 1 extraction → `case_extracted` (provided schema)

**Files:** Create `pipeline/03_extract_l1.sql`

**Interfaces:**
- Consumes: `bronze_email_parsed`.
- Produces: populated `kauvey_poc.mfa_ccms.case_extracted` (provided schema) + a temp/staging table `_l1_json` holding the structured extraction.

- [ ] **Step 1: Define the extraction prompt + response schema** — one `ai_query` call per row with `responseFormat` (JSON schema) covering the populatable CCMS fields (`Case_Ref, MFA_Ref, Created_On, Case_Title, Case_Subject, Case_Type, Case_Status, Case_Handler, Assigned_HCG, Handled_By, Case_Location, Current_Location, Citizenship, Origin, Case_Description, Assistance_Required, Advice_Provided___Follow_up, Category_Multiselect, Sub_Category_Multiselect, Tag, MP_Name, Covering_MP, MP_Constituency_Division, Incident_ID, Caller___Informant, Registrant, Title_Name, New_Email, Feedback_*`). Prompt enforces evidence-only + "return null when not stated." Model `databricks-claude-sonnet-4-5`.
```sql
CREATE OR REPLACE TABLE kauvey_poc.mfa_ccms._l1_json AS
SELECT case_ref, ai_query(
  'databricks-claude-sonnet-4-5',
  'Extract consular case fields from this email thread as JSON. Apply a value ONLY when the text explicitly supports it; use null otherwise. Thread:\n' || parsed_text,
  responseFormat => '{"type":"json_schema","json_schema":{"name":"case","schema":{ ... }}}'
) AS js
FROM kauvey_poc.mfa_ccms.bronze_email_parsed;
```
- [ ] **Step 2: Insert into `case_extracted`** — parse `js` fields into the exact columns; set `Do_Not_Modify_Case`/`Do_Not_Modify_Row_Checksum` = `sha2(concat_ws('|', <business cols>), 256)`; `Do_Not_Modify_Modified_On`/`Modified_On` = `current_timestamp()`; `New_Case='Yes'`; `No_of_Open_Child_Cases='0'`.
- [ ] **Step 3: Validate on 5 rows first** — inspect that Case_Type/Case_Location/Case_Handler are sensibly populated and null where absent.
- [ ] **Step 4: Run full; verify** — `SELECT count(*) FROM case_extracted` ≈ 120; `SELECT Case_Type, count(*) GROUP BY Case_Type` shows spread across the 6 types; nulls present where evidence absent.
- [ ] **Step 5: Commit.**

---

## Task 5: Layer 2 enrichment + derived → `gold_case_intelligence`

**Files:** Create `pipeline/04_enrich_l2.sql`

**Interfaces:**
- Consumes: `case_extracted`, `bronze_email_parsed`.
- Produces: table `kauvey_poc.mfa_ccms.gold_case_intelligence` = all `case_extracted` columns + `L1_Country, L1_Mission, L1_Case_Type, L1_Agencies array<string>, L1_Situational_Flags array<string>, L1_Status, L2_Summary_Pathway, L2_Assistance_Req_vs_Provided, L2_Complications_Delays, L2_External_Resources, L2_Lessons_Learnt, Case_Id, Resolution_Days int`.

- [ ] **Step 1: L1 derived tags** — a second `ai_query` (responseFormat with `L1_Country`, `L1_Mission`, `L1_Case_Type`, `L1_Agencies` array, `L1_Situational_Flags` array, `L1_Status`) over `parsed_text`, evidence-only. Keyed by case_ref.
- [ ] **Step 2: L2 analytical fields** — one `ai_query` returning the five free-text fields (summary/pathway, assistance req vs provided, complications/delays, external resources, lessons learnt) from `parsed_text`.
- [ ] **Step 3: Build gold** — join `case_extracted` + L1 + L2 on case_ref; `Case_Id = coalesce(MFA_Ref, Case_Ref)`; `Resolution_Days` = datediff between first and last email timestamp (approximate from `Created_On` / thread; if unavailable, null).
- [ ] **Step 4: Verify** — `count(*)` ≈ 120; `SELECT L1_Case_Type, size(L1_Agencies), size(L1_Situational_Flags)` spot-check; L2 fields non-empty for closed cases.
- [ ] **Step 5: Commit.**

---

## Task 6: Governance (column masks) + accuracy view

**Files:** Create `pipeline/05_governance.sql`

**Interfaces:**
- Consumes: `gold_case_intelligence`.
- Produces: masking function `kauvey_poc.mfa_ccms.mask_pii`; column masks applied to `Title_Name, Caller___Informant, Registrant, New_Email`; view `kauvey_poc.mfa_ccms.tag_accuracy`.

- [ ] **Step 1: Create mask function** —
```sql
CREATE OR REPLACE FUNCTION kauvey_poc.mfa_ccms.mask_pii(v STRING)
RETURN CASE WHEN is_account_group_member('consular_privileged') THEN v ELSE '***REDACTED***' END;
```
- [ ] **Step 2: Apply masks** — `ALTER TABLE kauvey_poc.mfa_ccms.gold_case_intelligence ALTER COLUMN Title_Name SET MASK kauvey_poc.mfa_ccms.mask_pii;` (repeat for the 4 PII columns).
- [ ] **Step 3: Accuracy view** —
```sql
CREATE OR REPLACE VIEW kauvey_poc.mfa_ccms.tag_accuracy AS
SELECT
  avg(CASE WHEN lower(trim(L1_Case_Type))=lower(trim(Case_Type)) THEN 1.0 ELSE 0.0 END) AS case_type_agreement,
  count(*) AS n
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
WHERE Case_Type IS NOT NULL AND L1_Case_Type IS NOT NULL;
```
- [ ] **Step 4: Verify** — `SELECT * FROM tag_accuracy` returns a fraction 0–1 with n>0; selecting a masked column as current user returns `***REDACTED***` (or real value if you're in the group — note which).
- [ ] **Step 5: Commit.**

---

## Task 7: Vector Search indexes (PII-scrubbed)

**Files:** Create `pipeline/06_indexes.py`

**Interfaces:**
- Consumes: `gold_case_intelligence`, `bronze_email_parsed`, volume `sop_corpus`.
- Produces: VS endpoint `mfa_ccms_vs`; index `kauvey_poc.mfa_ccms.case_email_index` (over scrubbed email chunks); index `kauvey_poc.mfa_ccms.sop_index` (over SOP chunks). Source delta tables `gold_email_chunks` and `gold_sop_chunks` with CDF enabled.

> Ground Vector Search API against `databricks:databricks-vector-search` skill.

- [ ] **Step 1: Build scrubbed chunk table** — `gold_email_chunks(chunk_id, case_ref, chunk string)`: take `parsed_text`, `regexp_replace` NRIC-format and known fabricated names to `[NAME]`/`[NRIC]`, split into ~1000-char chunks. Enable `delta.enableChangeDataFeed`.
- [ ] **Step 2: Build `gold_sop_chunks`** from parsed SOP docs (parse `sop_corpus` volume with `ai_parse_document` or read text), chunked, CDF on.
- [ ] **Step 3: Create endpoint** `mfa_ccms_vs` (STANDARD) if absent.
- [ ] **Step 4: Create both indexes** — delta-sync, embedding source column `chunk`, embedding model `databricks-gte-large-en`. Wait until ONLINE.
- [ ] **Step 5: Verify** — similarity query `"Singaporean arrested in Bangkok, ICA involved"` on `case_email_index` returns relevant chunks; `"procedure for serious illness or death"` on `sop_index` returns the right SOP. Confirm no NRIC/name leaks in returned text.
- [ ] **Step 6: Commit.**

---

## Task 8: Genie space

**Files:** Create `genie/instructions.md`

**Interfaces:**
- Consumes: `gold_case_intelligence`, `tag_accuracy`.
- Produces: a Genie space over the gold table with curated instructions + example SQL. (Created via UI or `databricks-genie-agents` skill; record the space id.)

- [ ] **Step 1: Write instructions** — describe the table, the L1/L2 columns, synonyms (mission=unit=handler, flag values), and 6–8 example NL→SQL pairs (e.g. "arrest & detention cases in Thailand where ICA was involved in the last 12 months", "handling volume by unit", "resolution time by case type").
- [ ] **Step 2: Create the Genie space** targeting `gold_case_intelligence` (+ `tag_accuracy`) on warehouse `<warehouse-id>`; paste instructions.
- [ ] **Step 3: Verify** — ask the ICA/Thailand question and the handling-volume question; both return correct SQL + results. Record space id in `genie/instructions.md`.
- [ ] **Step 4: Commit.**

---

## Task 9: AI/BI (Lakeview) dashboard — 5 views

**Files:** Create `pipeline/07_dashboard.py`

**Interfaces:**
- Consumes: `gold_case_intelligence`.
- Produces: a published Lakeview dashboard with 5 datasets/widgets. Record dashboard id/URL.

> Ground against `fe-databricks-tools:databricks-lakeview-dashboard` skill.

- [ ] **Step 1: Define 5 datasets (SQL)** — (1) case-type × country counts, (2) count by `L1_Mission`/`Case_Handler`, (3) situational-flag counts by month (explode `L1_Situational_Flags`), (4) avg `Resolution_Days` by `L1_Case_Type`, (5) agency co-involvement pairs (explode `L1_Agencies`).
- [ ] **Step 2: Build dashboard** — heatmap, bar, line, bar, matrix/heatmap respectively; warehouse `<warehouse-id>`.
- [ ] **Step 3: Publish; verify** each widget renders with data.
- [ ] **Step 4: Commit.**

---

## Task 10: Case Intelligence App (Python + Claude Sonnet)

**Files:** Create `app/app.py`, `app/agent.py`, `app/tools.py`, `app/app.yaml`, `app/requirements.txt`

**Interfaces:**
- Consumes: Genie space id, `case_email_index`, `sop_index`, `gold_case_intelligence`, dashboard URL.
- Produces: a deployed Databricks App `mfa-ccms-intel`.

> Ground against `databricks:databricks-apps-python` + `databricks-model-serving` skills.

- [ ] **Step 1: `tools.py`** — four functions: `genie_query(nl)` (Genie conversation API), `similar_cases(q)` (VS query on `case_email_index`, returns chunks + case_ref citations), `sop_lookup(q)` (VS query on `sop_index`, returns procedure + citation), `get_case(case_ref)` (SELECT the gold row incl. L1/L2, reads masked).
- [ ] **Step 2: `agent.py`** — OpenAI-compat client to `databricks-claude-sonnet-4-5` serving endpoint; tool-calling loop exposing the four tools; system prompt = orchestrator that routes per PRD §4.2 and always cites sources.
- [ ] **Step 3: `app.py`** — chat panel + a Case View (pick `Case_Ref` → structured fields + L1 tags + L2 analysis + source email) + embedded dashboard (iframe/link). Streamlit or FastAPI+static per the skill default.
- [ ] **Step 4: `app.yaml` + requirements** — declare resources (warehouse, serving endpoint, Genie, VS indexes); env for schema/model/ids.
- [ ] **Step 5: Deploy** — create app, sync source, grant the app SP `USE CATALOG/SCHEMA`, `SELECT` on gold, `EXECUTE` on the mask function, query on the VS indexes, `CAN USE` on the warehouse and Genie space.
- [ ] **Step 6: Verify** — app RUNNING; test one question per route (structured / similar / procedure) returns cited answers; case view loads a case; dashboard embeds.
- [ ] **Step 7: Commit.**

---

## Task 11: End-to-end validation + orchestrator

**Files:** Create `pipeline/run_all.py`

- [ ] **Step 1: `run_all.py`** — runs Tasks 1–7 SQL/py in order (idempotent) for a clean rebuild.
- [ ] **Step 2: Full rebuild** on a fresh run; confirm counts (~120), `tag_accuracy` populated, indexes ONLINE.
- [ ] **Step 3: Success-criteria checklist** (spec §13) — tick each: pipeline end-to-end; ~120 enriched cases; accuracy figure; Genie answer; similar-case citation; SOP citation; 5 dashboard views; app 3 routes + case view + dashboard, PII masked.
- [ ] **Step 4: Final commit** + write a short `README.md` with ids/URLs (Genie space, dashboard, app, VS endpoint).

---

## Self-Review

**Spec coverage:**
- §3 architecture → Tasks 1–10. §4.1 provided schema → Task 1/4. §4.2 gold enrichment → Task 5. §4.3 accuracy → Task 6. §5 synthetic data → Task 2. §6 governance → Task 6/7 (scrubbed index). §7 serving/search → Tasks 7 (VS) + 8 (Genie) + 9? no — Genie=8, VS=7. §8 dashboard → Task 9. §9 app → Task 10. §10 orchestration → Task 11. §11 deviation → noted in Global Constraints (Databricks-hosted models only). §13 success criteria → Task 11 Step 3. All covered.
- SOP corpus + "procedure" route (approved add) → Task 2 Step 4, Task 7 (sop_index), Task 10 (sop_lookup). Covered.

**Placeholder scan:** The `{ ... }` in Tasks 4/3 response schemas are intentional expansion points filled at execution against the ai-functions skill (schemas are long; the field lists are fully enumerated in-step). No "TBD/handle edge cases" left.

**Type consistency:** `case_ref` join key consistent across bronze/silver/gold/chunks. `L1_Agencies`/`L1_Situational_Flags` `array<string>` consistent (Task 5 produces, Task 9 explodes, Task 7 indexes text not arrays). Tool names `genie_query/similar_cases/sop_lookup/get_case` consistent Task 10 Steps 1–2. Masked columns identical set in Task 6 Steps 1–2 and Task 10 Step 1 note.

**Note for executor:** AI Functions syntax (`ai_parse_document`, `ai_query` responseFormat), Vector Search API, Lakeview, Genie, and Apps deployment must each be grounded against their named product skill at the start of the owning task — the plan gives structure and exact table/column/model names; the skill gives current API details.
