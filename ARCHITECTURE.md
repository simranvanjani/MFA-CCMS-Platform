# Architecture

Consular Case Intelligence turns case-email threads into structured, analysable intelligence,
then serves it through Genie, an AI/BI dashboard, and a governed app. Unity Catalog governs every
stage.

## Build-time flow — from documents to intelligence

```mermaid
flowchart TD
    subgraph ingest["Ingest (choose one)"]
        A1["Case-email PDFs<br/>Volume: raw_emails"]
        A2["Existing parsed-text table<br/>(parsed_table mode)"]
        A3["Existing CCMS case table<br/>(case_table mode)"]
    end

    A1 -->|ai_parse_document| B["bronze_email_parsed<br/>(case_ref, parsed_text)"]
    A2 --> B
    A3 -->|adopt as case record + derive narrative| C

    B -->|"ai_query · Layer 1"| C["case_extracted<br/>52-column CCMS schema"]
    B -->|"ai_query · Layer 1 tags + Layer 2 analytics"| D
    C --> D["gold_case_intelligence<br/>+ L1 tags, L2 analysis, Resolution_Days"]

    D --> G["UC column masks (PII)<br/>+ tag_accuracy view"]
    D --> E["gold_email_chunks<br/>(PII-scrubbed)"]
    F["sop_corpus (Volume)"] -->|ai_parse_document| H["gold_sop_chunks"]
    E -->|GTE embeddings| I["Vector index:<br/>case_email_index"]
    H -->|GTE embeddings| J["Vector index:<br/>sop_index"]

    D --> K["Genie space"]
    D --> L["AI/BI dashboard"]
    D --> M["case_notes<br/>(officer updates)"]

    classDef store fill:#e8eff9,stroke:#1f4e8c,color:#173c6d;
    classDef ai fill:#fbeeda,stroke:#9a5810,color:#7a4708;
    classDef serve fill:#e2f2ea,stroke:#137a4d,color:#0f5c3a;
    class A1,A2,A3,B,C,D,E,H,M store;
    class I,J,K ai;
    class G,L,K serve;
```

## Runtime flow — an officer uses the app

```mermaid
flowchart LR
    U["Officer / analyst"] --> APP["Case Intelligence app<br/>(FastAPI + case console UI)"]

    APP -->|"case queue · record view · notes"| SQL["gold_case_intelligence<br/>+ case_notes (OBO: user token)"]
    APP -->|"ask"| ORCH{"Orchestrator<br/>routes the question"}

    ORCH -->|"counts / trends"| GEN["Genie<br/>(OBO: user token)"]
    ORCH -->|"find similar cases"| VE["case_email_index<br/>(app SP)"]
    ORCH -->|"what's the procedure?"| VS["sop_index<br/>(app SP)"]
    ORCH -->|"compose cited answer"| LLM["Claude Sonnet<br/>(app SP)"]

    GEN --> D["gold_case_intelligence"]
    VE --> D
    VS --> SOP["SOP corpus"]

    UC["Unity Catalog — masks · row filters · lineage · audit"] -.governs.- SQL
    UC -.governs.- GEN
    UC -.governs.- D

    classDef store fill:#e8eff9,stroke:#1f4e8c,color:#173c6d;
    classDef ai fill:#fbeeda,stroke:#9a5810,color:#7a4708;
    class SQL,D,SOP store;
    class GEN,VE,VS,LLM,ORCH ai;
```

## Auth model (hybrid OBO)

| Path | Runs as | Why |
|------|---------|-----|
| SQL reads/writes (cases, notes) | **User (OBO)** — `x-forwarded-access-token`, scope `sql` | UC column masks & row filters evaluate against the *real viewer* |
| Genie (NL analytics) | **User (OBO)** — scope `dashboards.genie` | Same per-viewer governance on the case table |
| Vector Search (similar cases, SOPs) | App service principal | Shared inference over PII-scrubbed / non-PII text |
| Claude LLM (routing + compose) | App service principal | Shared inference, no PII |

## Layers (per the PRD)

- **Layer 1 — structured annotation tags:** country, mission/unit, case type, external agencies,
  situational flags, status. Evidence-only (a tag is applied only when the narrative supports it).
- **Layer 2 — qualitative analytics:** summary & pathway, assistance requested vs provided,
  complications & delays, external resources engaged, lessons learnt.
- **Layer 3 — insights:** dashboard views (case-type by country, handling volume, resolution
  timelines, situational-flag trends, agency co-involvement).
