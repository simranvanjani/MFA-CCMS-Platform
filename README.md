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

## Files

- `pipeline/` — `config.py`, `dbsql.py`, `00_schema.py` … `07_dashboard.py`, `run_all.py`
- `genie/instructions.md`, `genie/create_space.py`
- `app/` — `app.py` (Streamlit), `agent.py` (router), `tools.py`, `app.yaml`, `requirements.txt`

## App deploy

```bash
databricks workspace import-dir app /Workspace/Users/<you>/mfa-ccms-intel-src --overwrite --profile DEFAULT
databricks apps deploy mfa-ccms-intel --source-code-path /Workspace/Users/<you>/mfa-ccms-intel-src --profile DEFAULT
```
The app service principal is granted: UC USE/SELECT/EXECUTE on the schema, objects and mask
function; `CAN_USE` on the warehouse and VS endpoint; `CAN_RUN` on the Genie space.

## Notes / deviations

- **Demo only, fully synthetic data.** No real names, NRICs, or contacts.
- **Model residency:** uses Databricks-hosted (Bedrock-backed) Claude. A real MFA build would
  use an in-region/in-VPC model to satisfy "all AI stays in the MFA environment".
- Deferred (per PRD): CCMS write-back, production hardening/DR, Tableau/PowerBI migration.
