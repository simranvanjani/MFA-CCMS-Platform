# MFA CCMS — Consular Case Intelligence (Demo) — Design Spec

**Date:** 2026-09-17
**Author:** simran.vanjani@databricks.com
**Status:** Approved design → ready for implementation plan
**Source PRD:** *Consular Case Intelligence — Turning CCMS case emails into structured, analysable intelligence* (MFA Singapore / Databricks)

---

## 1. Objective

Build a working demo of the PRD's **Consular Case Intelligence** solution on Databricks:
turn synthetic consular case-email PDFs into a structured, analysable case record, enrich it
with AI-derived tags and analytical fields, and serve it through Genie, an AI/BI dashboard,
and a Case Intelligence chat app — governed end-to-end by Unity Catalog.

This is a **demonstration build**, not a production MFA deployment.

## 2. Target environment & config

| Item | Value |
|------|-------|
| CLI profile | `DEFAULT` (workspace `<workspace-host>`, the "kauvey-poc" workspace) |
| Catalog / schema | `kauvey_poc.mfa_ccms` (new schema) |
| Extraction / enrichment / agent LLM | `databricks-claude-sonnet-4-5` |
| Embeddings | `databricks-gte-large-en` |
| SQL warehouse | `<warehouse-id>` |
| App framework | Python Databricks App (reuses the Capex chat-agent pattern) |

## 3. Architecture (build-time flow, mirrors PRD §4.1)

```
Synthetic case-email PDFs                 Synthetic SOP / procedure docs
   → Volume mfa_ccms.raw_emails (Bronze)      → Volume mfa_ccms.sop_corpus
        │ ai_parse_document                        │
        ▼                                          ▼
   bronze.email_parsed (clean text/markdown)   Vector index: sop_index
        │ ai_query / ai_extract  → LAYER 1
        ▼
   silver.case_extracted  = PROVIDED SCHEMA, populated from each email
        │ ai_query  → LAYER 2 (free-text analytical fields)
        ▼
   gold.case_intelligence = provided schema + L1 derived tags + L2 fields + timeline metrics
        ├──▶ Genie space (structured NL Q&A)
        ├──▶ Vector index: case_email_index (similar-case search, PII excluded)
        ├──▶ AI/BI dashboard (Layer 3 views)
        ├──▶ gold.tag_accuracy (L1 vs CCMS-recorded — PRD §9)
        └──▶ Case Intelligence App (chat + case view + embedded dashboard)
```

Runtime flow (PRD §4.2): the app's orchestrator routes each question — countable/analytical →
Genie; find/summarise similar → `case_email_index`; "what's the correct procedure?" → `sop_index` —
always with citations.

## 4. Data model

### 4.1 `silver.case_extracted` — the provided CCMS schema

The exact schema provided by the user is created here and populated by the LLM from each parsed
email. All columns `string` except the audit datetime, per the provided spec:

`Do_Not_Modify_Case`, `Do_Not_Modify_Row_Checksum`, `Do_Not_Modify_Modified_On` (datetime),
`Title_Name`, `MFA_Ref`, `Created_On`, `Origin`, `Additional_Information`,
`Advice_Provided___Follow_up`, `Allow_Case_History_Access`, `Allow_LRS_Access`, `Assigned_HCG`,
`Assistance_Required`, `Caller___Informant`, `Case_Description`, `Case_Handler`, `Case_Location`,
`Case_Ref`, `Case_Status`, `Case_Subject`, `Case_Title`, `Case_Type`, `Category_Multiselect`,
`Citizenship`, `Contact_Tracing_Status`, `Covering_MP`, `Current_Location`, `Evacuation_Status`,
`Feedback_Date`, `Feedback_Message`, `Feedback_Provider`, `Feedback_Type`, `Handled_By`,
`HCG_Assigned_Date`, `Incident_ID`, `LRS_Communication`, `LRS_Portal_User_Modify_By`,
`LRS_Portal_User_Modify_On`, `LRS_Update`, `Media_Query`, `Media_Response`, `Modified_On`,
`MP_Constituency_Division`, `MP_Name`, `New_Case`, `New_Email`, `No_of_Open_Child_Cases`,
`Parent_Case`, `Recipient_of_Feedback`, `Registrant`, `Sub_Category_Multiselect`, `Tag`.

- `Do_Not_Modify_Row_Checksum` = deterministic hash of the business columns.
- `Do_Not_Modify_Modified_On` / `Modified_On` = load timestamp.
- Not every column is populated for every case (matches sparse real-world CCMS data); the LLM
  fills only what the email supports.

### 4.2 `gold.case_intelligence` — enrichment on top of the provided schema

All `silver.case_extracted` columns, **plus**:

**Layer 1 — derived tags (evidence-only, PRD §6):**
| Column | Type | Notes |
|--------|------|-------|
| `L1_Country` | string | Where events occurred (only when explicitly named/clearly implied) |
| `L1_Mission` | string | Responsible HQ/OM unit (CRC, CON/OPS, Bangkok, Guangzhou, …) |
| `L1_Case_Type` | string | Derived CCMS classification |
| `L1_Agencies` | array<string> | External SG agencies explicitly mentioned (ICA, SPF, MHA, MOH) |
| `L1_Situational_Flags` | array<string> | scam, MP/POH escalation, language barrier, welfare concern |
| `L1_Status` | string | pending / closed |

**Layer 2 — qualitative analytical fields (PRD §7):**
| Column | Type |
|--------|------|
| `L2_Summary_Pathway` | string |
| `L2_Assistance_Req_vs_Provided` | string |
| `L2_Complications_Delays` | string |
| `L2_External_Resources` | string |
| `L2_Lessons_Learnt` | string |

**Derived:**
| Column | Type | Notes |
|--------|------|-------|
| `Case_Id` | string | canonical key (from `MFA_Ref`/`Case_Ref`) |
| `Resolution_Days` | int | from `Created_On` → closure, derived from email timestamps |

**Evidence-only rule:** extraction prompts instruct the model to apply a tag only when the
narrative explicitly supports it; absence of info = absence of tag (null / empty array).

### 4.3 `gold.tag_accuracy` (PRD §9)

View comparing derived vs CCMS-recorded values to produce a concrete accuracy figure:
- `L1_Case_Type` vs `Case_Type` (% agreement)
- `L1_Mission` vs `Case_Handler` / `Assigned_HCG`

## 5. Synthetic data plan

- **~120 case-email PDFs**, each a realistic email thread: subject line carries the case ID,
  a designated mailbox is CC'd, and officers (CRC / CON-OPS / a mission) exchange messages
  describing the incident, actions taken, and resolution.
- Coverage designed for good dashboards: spread across **countries** (Thailand, China/Guangzhou,
  Malaysia, Indonesia, UK, …), **case types** (Arrest & Detention, Serious Illness/Death,
  Victim of Crime, Lost/Stolen Document, Evacuation, Scam), **units**, and **dates 2024–2026**.
- All names, NRICs, contacts are **fabricated** (fake Singaporean-style names and NRIC-format
  strings). No real personal data.
- **SOP corpus:** one short synthetic procedure document per case type, for the "procedure" route.
- Generation is a one-time step (Python; text → PDF), landed into the Bronze volume, then parsed
  by the same pipeline as "new arrivals" would be.

## 6. Governance / PII (PRD §10)

- **UC column masks** on `gold.case_intelligence` PII columns (`Title_Name`, `Caller___Informant`,
  `Registrant`, `New_Email`): redacted unless the viewer belongs to a privileged group;
  analysts still see all aggregate trends.
- **Vector Search index built on PII-scrubbed text** — names / NRIC-format strings stripped before
  indexing, per "keep identifiers out of the search index."
- App service principal reads through the masked path.
- (Row-level filters and full audit are notionally covered by UC; masking is the demoed control.)

## 7. Serving & search

- **Genie space** over `gold.case_intelligence`: curated instructions + example SQL for
  countable/analytical questions (counts, distributions, trends).
- **`case_email_index`** (Vector Search): chunked, PII-scrubbed email bodies → find/summarise
  similar cases.
- **`sop_index`** (Vector Search): synthetic SOP corpus → "what's the correct procedure?", cited.

## 8. Layer 3 dashboard (AI/BI) — subset of PRD §8

1. Case-type distribution by country (heatmap)
2. Handling volume by unit (CRC / CON-OPS / OMs)
3. Situational-flag trend over time
4. Resolution timelines by case type
5. Agency co-involvement

(Lessons-learnt clustering deferred unless requested.)

## 9. Case Intelligence App (Python + Claude Sonnet)

- **Orchestrator / tool-calling loop** (OpenAI-compatible client → `databricks-claude-sonnet-4-5`)
  with three tools matching PRD §4.2 routing: Genie (structured), `case_email_index` (similar),
  `sop_index` (procedure). Answers cite their source.
- **Case View:** select a `Case_Ref` → structured CCMS fields + L1 tags + L2 analysis + source email.
- **Embedded Layer 3 dashboard.**
- Reads PII-masked; deployed as a Databricks App with a service principal granted least-privilege
  access to the schema, volume, indexes, and warehouse.

## 10. Orchestration & delivery

Notebook-driven pipeline, built and validated stage by stage on a few rows before the full run:
1. Create schema, volume, base tables.
2. Generate synthetic PDFs + SOP docs → Bronze volume.
3. `ai_parse_document` → `bronze.email_parsed`.
4. Layer 1 extraction → `silver.case_extracted` (provided schema).
5. Layer 2 enrichment + derived metrics → `gold.case_intelligence`.
6. Apply column masks; build `case_email_index`, `sop_index`; `gold.tag_accuracy`.
7. Genie space, dashboard, app.

Optionally wired into a Lakeflow Job. Auto Loader on the Bronze volume is the incremental story
(demoed as batch).

## 11. Accepted deviation & risks

- **Model residency:** the demo uses Bedrock-backed Databricks-hosted Claude (Anthropic is a
  limited subprocessor; 30-day retention). This differs from the PRD's "all AI stays within the
  MFA environment." **Accepted for the demo**; a real MFA build would use an in-region/in-VPC model.
- **AI extraction cost/latency:** ~120 PDFs × parse + multiple `ai_query` calls. Mitigated by
  validating on a small sample first and running the full batch once.
- **Genie / dashboard quality** depends on curated instructions; iterate after first data load.

## 12. Out of scope (per PRD "deferred" + demo scope)

- Write-back into CCMS (designed-for, not implemented).
- Production hardening, DR, SLAs.
- Migration of existing Tableau / PowerBI dashboards.
- Multi-language handling; expanded controlled vocabulary.
- Lessons-learnt theme clustering (optional add-on).

## 13. Success criteria

- Pipeline runs end-to-end: PDFs → parsed → provided schema populated → gold enriched.
- `gold.case_intelligence` holds ~120 cases with populated L1 tags and L2 fields.
- `gold.tag_accuracy` shows a real % agreement figure.
- Genie answers a structured question; vector search returns a similar case with citation;
  SOP route returns a procedure with citation.
- Dashboard renders the 5 views.
- App runs: chat (3 routes) + case view + embedded dashboard, reading PII-masked data.
