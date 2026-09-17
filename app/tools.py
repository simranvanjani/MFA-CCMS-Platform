"""Tools for the Case Intelligence agent: Genie, similar-case search, SOP lookup, case fetch.

All access goes through the Databricks SDK using the app's credentials (service principal
when deployed, or a CLI profile locally). PII columns are read through the masked gold table.
"""
import os

from databricks.sdk import WorkspaceClient

CATALOG = os.getenv("CCMS_CATALOG", "kauvey_poc")
SCHEMA = os.getenv("CCMS_SCHEMA", "mfa_ccms")
FQ = f"{CATALOG}.{SCHEMA}"
WAREHOUSE = os.getenv("DATABRICKS_WAREHOUSE_ID", "3ca7ddd9d10dbbac")
GENIE_SPACE = os.getenv("GENIE_SPACE_ID", "01f1b2809936166da1a732dc78d8162f")
IDX_EMAIL = os.getenv("IDX_EMAIL", f"{FQ}.case_email_index")
IDX_SOP = os.getenv("IDX_SOP", f"{FQ}.sop_index")

_w = WorkspaceClient()


def _query(sql):
    from databricks.sdk.service.sql import StatementState
    r = _w.statement_execution.execute_statement(warehouse_id=WAREHOUSE, statement=sql, wait_timeout="30s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        import time; time.sleep(1)
        r = _w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        return [], []
    cols = [c.name for c in r.manifest.schema.columns] if r.manifest else []
    rows = r.result.data_array if (r.result and r.result.data_array) else []
    return cols, rows


def genie_query(question: str) -> str:
    """Answer a structured/analytical question via the Genie space."""
    try:
        msg = _w.genie.start_conversation_and_wait(GENIE_SPACE, question)
        parts = []
        for a in (msg.attachments or []):
            if a.text and a.text.content:
                parts.append(a.text.content)
            if a.query and a.query.query:
                parts.append(f"\n_SQL:_ `{a.query.query}`")
        return "\n".join(parts) if parts else "Genie returned no answer."
    except Exception as e:
        return f"Genie error: {e}"


def similar_cases(query: str, k: int = 3) -> str:
    """Find similar past cases by semantic search over PII-scrubbed email threads."""
    r = _w.vector_search_indexes.query_index(
        index_name=IDX_EMAIL, columns=["case_ref", "case_type", "case_location", "chunk"],
        query_text=query, num_results=k)
    out = []
    for row in (r.result.data_array or []):
        ref, ctype, loc, chunk = row[0], row[1], row[2], row[3]
        out.append(f"**{ref}** ({ctype}, {loc})\n{chunk[:280]}...")
    return "\n\n".join(out) if out else "No similar cases found."


def sop_lookup(query: str, k: int = 2) -> str:
    """Retrieve the relevant SOP / procedure with a citation."""
    r = _w.vector_search_indexes.query_index(
        index_name=IDX_SOP, columns=["sop_title", "chunk"], query_text=query, num_results=k)
    out = []
    for row in (r.result.data_array or []):
        out.append(f"**SOP: {row[0].replace('_', ' ')}**\n{row[1][:500]}")
    return "\n\n".join(out) if out else "No matching procedure found."


def get_case(case_ref: str) -> dict:
    """Fetch a single case record (structured + L1 tags + L2 analysis), PII-masked."""
    cols, rows = _query(
        "SELECT Case_Ref, Case_Type, Case_Status, Case_Handler, Assigned_HCG, L1_Country, "
        "L1_Agencies, L1_Situational_Flags, Resolution_Days, Title_Name, Case_Description, "
        "L2_Summary_Pathway, L2_Assistance_Req_vs_Provided, L2_Complications_Delays, "
        "L2_External_Resources, L2_Lessons_Learnt "
        f"FROM {FQ}.gold_case_intelligence WHERE Case_Ref = '{case_ref}' LIMIT 1")
    if not rows:
        return {}
    return dict(zip(cols, rows[0]))


def list_case_refs(limit: int = 200):
    _, rows = _query(f"SELECT Case_Ref FROM {FQ}.gold_case_intelligence ORDER BY Case_Ref LIMIT {limit}")
    return [r[0] for r in rows]


def count_cases() -> int:
    _, rows = _query(f"SELECT count(*) FROM {FQ}.gold_case_intelligence")
    return int(rows[0][0]) if rows else 0


def list_cases_page(page: int, page_size: int = 20):
    """Return (columns, rows) for one page, newest first."""
    offset = page * page_size
    return _query(
        "SELECT Case_Ref, Case_Type, L1_Country AS Country, Assigned_HCG AS Mission, "
        "Case_Status AS Status, Created_On "
        f"FROM {FQ}.gold_case_intelligence "
        f"ORDER BY to_date(Created_On) DESC, Case_Ref DESC LIMIT {page_size} OFFSET {offset}")


def recommended_steps(case_type: str) -> str:
    """Fetch the SOP steps for this case type (matched by normalised title)."""
    safe = (case_type or "").replace("'", "''")
    _, rows = _query(
        f"SELECT chunk FROM {FQ}.gold_sop_chunks "
        f"WHERE regexp_replace(lower(sop_title), '[^a-z]', '') = "
        f"regexp_replace(lower('{safe}'), '[^a-z]', '') LIMIT 1")
    return rows[0][0] if rows else "No matching SOP found for this case type."


def get_notes(case_ref: str):
    safe = case_ref.replace("'", "''")
    cols, rows = _query(
        f"SELECT author, note, cast(created_at AS STRING) AS created_at FROM {FQ}.case_notes "
        f"WHERE Case_Ref = '{safe}' ORDER BY created_at DESC")
    return [dict(zip(cols, r)) for r in rows]


def add_note(case_ref: str, author: str, note: str):
    import uuid
    nid = uuid.uuid4().hex
    cr = case_ref.replace("'", "''")
    au = (author or "officer").replace("'", "''")
    nt = note.replace("'", "''")
    _query(f"INSERT INTO {FQ}.case_notes VALUES "
           f"('{nid}', '{cr}', '{au}', '{nt}', current_timestamp())")
