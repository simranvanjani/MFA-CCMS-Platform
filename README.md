# MFA CCMS — Consular Case Intelligence (Demo)

Turns synthetic consular case-email PDFs into a structured, AI-enriched, governed case record,
served through Genie, an AI/BI dashboard, and a Case Intelligence chat app. Built on Databricks
per the *Consular Case Intelligence* PRD (MFA Singapore).

- **Spec:** `docs/specs/2026-09-17-mfa-ccms-consular-case-intel-design.md`
- **Plan:** `docs/plans/2026-09-17-mfa-ccms-consular-case-intel.md`

## Environment

| Item | Value |
|------|-------|
| Profile / workspace | `DEFAULT` (`adb-7405605297651217.17`) |
| Catalog / schema | `kauvey_poc.mfa_ccms` |
| Warehouse | `3ca7ddd9d10dbbac` |
| Models | `databricks-claude-sonnet-4-5`, `databricks-gte-large-en` |

## Deployed objects

| Object | Id / location |
|--------|---------------|
| Gold table | `kauvey_poc.mfa_ccms.gold_case_intelligence` (120 cases) |
| Base CCMS table (provided schema) | `kauvey_poc.mfa_ccms.case_extracted` |
| Accuracy view | `kauvey_poc.mfa_ccms.tag_accuracy` (case-type 1.00 / mission 0.94) |
| Vector Search endpoint | `mfa_ccms_vs` |
| Indexes | `kauvey_poc.mfa_ccms.case_email_index`, `…​.sop_index` |
| Genie space | `01f1b2809936166da1a732dc78d8162f` |
| AI/BI dashboard | `01f1b2804de6171a80599744ddde9a56` |
| App | `mfa-ccms-intel` — https://mfa-ccms-intel-7405605297651217.17.azure.databricksapps.com |

## Pipeline

```
raw_emails (Volume, 120 PDFs) --ai_parse_document--> bronze_email_parsed
  --ai_query (Layer 1)--> case_extracted (provided 52-col CCMS schema)
  --ai_query (Layer 1 tags + Layer 2 fields)--> gold_case_intelligence
      ├─ column masks on PII (Title_Name, Caller___Informant, Registrant, New_Email)
      ├─ tag_accuracy (derived vs CCMS-recorded)
      ├─ gold_email_chunks (PII-scrubbed) --> case_email_index
      ├─ sop_corpus (Volume) --ai_parse--> gold_sop_chunks --> sop_index
      ├─ Genie space  ├─ AI/BI dashboard  └─ Case Intelligence app
```

Rebuild data + serving: `python -m pipeline.run_all`
One-time: `python -m genie.create_space`, `python -m pipeline.07_dashboard`, app deploy (see below).

## One-click install (customer environment)

`install.py` is a **Databricks notebook** that stands up the entire platform in a fresh
workspace — schema, data parsing, Layer 1/2, governance, Vector Search, Genie, dashboard, and
the app. Import this repo as a **Git folder**, open `install.py`, set the widgets, Run All.

- **`data_mode = synthetic`** — generates demo case-email PDFs (no real data needed).
- **`data_mode = byo`** — deploy against the **customer's real data**: drop their case-email PDFs
  into the `raw_emails` volume (or point `source_pdf_path` at an existing folder) and run. Every
  downstream layer (tags, analytics, search, Genie, dashboard, app) is derived automatically.
- Set `warehouse_id` to enable Genie + dashboard + app; requires **serverless or DBR 17.3+**.

## Files

- `pipeline/` — `config.py`, `dbsql.py`, `00_schema.py` … `07_dashboard.py`, `run_all.py`
- `genie/instructions.md`, `genie/create_space.py`
- `app/` — `app.py` (FastAPI API), `index.html` (case-console UI), `agent.py` (router), `tools.py`, `app.yaml`, `requirements.txt`

## App deploy

```bash
databricks workspace import-dir app /Workspace/Users/<you>/mfa-ccms-intel-src --overwrite --profile DEFAULT
databricks apps deploy mfa-ccms-intel --source-code-path /Workspace/Users/<you>/mfa-ccms-intel-src --profile DEFAULT
```
**Auth (hybrid OBO):** the app uses on-behalf-of-user auth (`x-forwarded-access-token`,
scopes `sql` + `dashboards.genie`) for SQL reads/notes and Genie — so Unity Catalog column
masks/row filters evaluate against the *real viewer*. Vector Search and the Claude LLM run on
the app service principal (shared inference, no PII). The SP is granted `CAN_USE` on the
warehouse and VS endpoint and `CAN_QUERY` on the FM endpoint (default-open); the earlier
per-table SELECT grants to the SP are no longer required but are harmless.

## Notes / deviations

- **Demo only, fully synthetic data.** No real names, NRICs, or contacts.
- **Model residency:** uses Databricks-hosted (Bedrock-backed) Claude. A real MFA build would
  use an in-region/in-VPC model to satisfy "all AI stays in the MFA environment".
- Deferred (per PRD): CCMS write-back, production hardening/DR, Tableau/PowerBI migration.
