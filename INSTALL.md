# Installation Guide — Consular Case Intelligence

This guide walks a Databricks workspace admin / engineer through installing the **MFA CCMS
Consular Case Intelligence** platform in your own workspace, against **your own case-email
data**. The whole platform is stood up by one notebook: **`install.py`**.

---

## What you get

Running `install.py` builds, end to end, in a catalog/schema you choose:

| Layer | Object |
|-------|--------|
| Raw documents | UC Volumes `raw_emails`, `sop_corpus` |
| Parsed text | `bronze_email_parsed` (via `ai_parse_document`) |
| Structured case record | `case_extracted` — the 52-column CCMS schema, filled by AI |
| Enriched intelligence | `gold_case_intelligence` — + Layer 1 tags, Layer 2 analysis, resolution days |
| Governance | PII column masks + `tag_accuracy` view + `case_notes` table |
| Search | Vector Search endpoint + `case_email_index` (PII-scrubbed) + `sop_index` |
| NL analytics | Genie space |
| Dashboards | AI/BI (Lakeview) dashboard |
| Application | Case Intelligence app (case queue, record view, notes, chat, dashboard) |

---

## Prerequisites

1. **A Databricks workspace** on a region that supports AI Functions, Vector Search, Genie, and Apps.
2. **Compute:** run the notebook on **Serverless** or a cluster with **DBR 17.3+** (required by `ai_parse_document`).
3. **A Pro or Serverless SQL warehouse** — note its **warehouse id** (Databricks CLI: `databricks warehouses list`, or the SQL Warehouses UI → *Connection details*). Needed for Genie, the dashboard, and the app.
4. **Foundation Model endpoints** available in the workspace:
   - an LLM endpoint (default `databricks-claude-sonnet-4-5`),
   - an embedding endpoint (default `databricks-gte-large-en`).
   Change the widgets if your workspace uses different endpoint names.
5. **Permissions** for the user running the notebook:
   - create a catalog/schema/volumes (or `USE CATALOG` + `CREATE` on an existing catalog),
   - create Vector Search endpoints, a Genie space, an AI/BI dashboard, and a Databricks App.
6. **Governance group (optional but recommended):** an account group named **`consular_privileged`**.
   Members see unmasked PII (names, etc.); everyone else sees `***REDACTED***`. Create it in the
   account console and add the officers who are cleared to see personal identifiers.

---

## Step 1 — Import this repository as a Git folder

In the workspace: **Workspace → (your folder) → Add → Git folder**, and clone:

```
https://github.com/simranvanjani/MFA-CCMS-Platform.git
```

Importing as a Git folder (not just uploading `install.py`) matters: the installer deploys the
app from the `app/` directory that sits next to the notebook.

## Step 2 — Open `install.py` and set the parameters

Open `install.py`. Attach it to serverless/DBR 17.3+ compute. The widgets at the top:

| Widget | What to set |
|--------|-------------|
| **catalog** | Target catalog (created if missing), e.g. `mfa_ccms`. |
| **schema** | Target schema, e.g. `consular`. |
| **warehouse_id** | Your Pro/Serverless SQL warehouse id. Leave blank to build data only (no Genie/dashboard/app). |
| **ingest_mode** | `pdf` (parse PDFs), `parsed_table` (you already parsed the emails), or `case_table` (you already built the structured case table). See below. |
| **source_table** | *(parsed_table / case_table)* the fully-qualified existing table to read from. |
| **source_case_ref_col / source_text_col** | *(parsed_table)* the case-id and email-text column names in your parsed table. |
| **data_mode** | *(pdf mode)* `byo` for your real PDFs (Step 3), or `synthetic` for a self-contained demo. |
| **source_pdf_path** | *(pdf + BYO, optional)* an existing folder of PDFs to copy in. Leave blank if you upload straight to the volume. |

### Already parsed the emails or built the table? Point at it directly.

You don't have to re-parse PDFs if the work is already done:

- **`ingest_mode = parsed_table`** — you already extracted the email text into a table. Set
  `source_table` and the `source_case_ref_col` / `source_text_col` names. The installer skips
  `ai_parse_document` and runs Layer 1 + Layer 2 + search + serving on your table.
- **`ingest_mode = case_table`** — you already built the **structured case table** (the 52-column
  CCMS schema, or a superset). Set `source_table`. The installer adopts it as `case_extracted`,
  **skips parsing and Layer 1**, and runs Layer 2 analytics + governance + search + Genie +
  dashboard + app on top of it. Layer 2 and similar-case search use the narrative it derives from
  your text columns (`Case_Description`, `Additional_Information`, `Assistance_Required`,
  `Advice_Provided___Follow_up`, `Case_Subject`, `Case_Title`) — so richer text columns give
  richer analytics. If you also keep the raw email text, `parsed_table` mode yields the best
  Layer 2 quality.
| **llm_endpoint** | LLM serving endpoint name. |
| **embedding_endpoint** | Embedding serving endpoint name. |
| **app_name** | Name for the deployed app, e.g. `mfa-ccms-intel`. |
| **build_app** | `yes` to create + deploy the app, `no` to skip it. |

## Step 3 — Load your case-email PDFs (BYO mode)

The pipeline reads **case-email threads as PDFs**, one file per case. Each PDF should contain the
email correspondence for a case (subject line, participants, and the narrative of what happened,
what was done, and how it was resolved).

Put them in the Volume the installer creates (run **Cell 1** first if the volume doesn't exist yet):

```
/Volumes/<catalog>/<schema>/raw_emails/
```

You can upload via the Catalog UI (**Volumes → raw_emails → Upload**), `databricks fs cp`, or set
`source_pdf_path` to an existing folder and the installer copies them for you. *(Optional)* drop
procedure/SOP PDFs into `/Volumes/<catalog>/<schema>/sop_corpus/` to power the "what is the
correct procedure?" answers.

> **No CCMS integration is required for the MVP.** The platform derives everything from the email
> PDFs. Write-back into CCMS is a later phase.

### Shortcut: synthetic data for a fresh workspace

Want to stand the demo up in **another workspace without any real data**? Run
**`synthetic_data.py`** — it builds the backing tables (a realistic, *uneven* case table
plus the SOP and operational tables) directly in SQL, no PDFs / parsing / LLM needed:

```bash
python synthetic_data.py --catalog <catalog> --schema <schema> \
    --warehouse <warehouse-id> --profile <profile> --cases 400
```

Then point the app at that catalog/schema (`CCMS_CATALOG` / `CCMS_SCHEMA`) and run the
install.py serving steps (Genie space, Vector Search, app deploy). The **default install
path uses real data** — this script is only the "just give me demo data" shortcut.

## Step 4 — Run All

**Run All**, top to bottom. First run takes several minutes — the Vector Search endpoint and
indexes provision asynchronously (the installer waits for them). The final **Summary** cell prints
the created objects and the **app URL**.

---

## After installation

- **Open the app** at the URL from the Summary cell. It has: a **case queue** (search, paginate,
  status), a **record view** (details, tags, recommended SOP steps, notes), a **chat** (ask across
  cases / find similar / ask a procedure), and an embedded **dashboard**.
- **PII masking is per viewer.** The app uses on-behalf-of-user auth, so masks evaluate against the
  logged-in officer's group membership. Add cleared officers to `consular_privileged` to let them
  see names.
- **Add notes** in the record view — they persist to `case_notes`, stamped with the officer's email.
- **Accuracy:** if your CCMS-recorded case type is present in the data, `tag_accuracy` reports how
  often the AI-derived type agrees with it.

## Re-running / updating

`install.py` is idempotent — re-run it to rebuild after adding more PDFs. To ingest new case
emails, drop them into `raw_emails/` and Run All again (or just re-run cells 3–7).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ai_parse_document not found` | Compute is below DBR 17.3 — use Serverless or a 17.3+ cluster. |
| Genie / dashboard / app cells skipped | `warehouse_id` is blank — set it and re-run those cells. |
| App shows "No SOP found" for every case | The app service principal lacks `SELECT` on `gold_sop_chunks` — the installer grants it; re-run Cell 10, or grant manually. |
| App can't read data / empty pages | Confirm the app has OBO scopes `sql` + `dashboards.genie` (installer sets these) and the user has access to the catalog. |
| Vector Search cell times out | Endpoint provisioning can take 10–20 min on first creation; re-run Cell 7 — it resumes waiting. |
| Foundation model endpoint errors | Set `llm_endpoint` / `embedding_endpoint` to endpoints that exist in your workspace. |

## Data residency note

The default LLM/embedding endpoints are Databricks-hosted foundation models. For deployments with
strict data-residency requirements, point `llm_endpoint` / `embedding_endpoint` at endpoints served
within your approved region/environment before running the installer.

## Cleanup

To remove the demo: delete the app (`databricks apps delete <app_name>`), the Genie space and
dashboard (from their UIs), the Vector Search endpoint, and `DROP SCHEMA <catalog>.<schema> CASCADE`.
