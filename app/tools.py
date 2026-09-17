"""Tools for the Case Intelligence agent: Genie, similar-case search, SOP lookup, case fetch.

Hybrid auth: SQL reads/writes and Genie run on the *user's* token (OBO) when one is passed
in — so Unity Catalog column masks and row filters evaluate against the real viewer. Vector
Search and the LLM run on the app service principal (shared inference, no PII). If no user
client is passed (local dev), everything falls back to the app/CLI credentials.
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

APP_W = WorkspaceClient()  # app service principal (or CLI profile locally)


def _query(sql, w=None):
    from databricks.sdk.service.sql import StatementState
    client = w or APP_W
    r = client.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE, statement=sql, wait_timeout="30s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        import time; time.sleep(1)
        r = client.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        return [], []
    cols = [c.name for c in r.manifest.schema.columns] if r.manifest else []
    rows = r.result.data_array if (r.result and r.result.data_array) else []
    return cols, rows


def genie_query(question: str, w=None) -> str:
    """Answer a structured/analytical question via the Genie space (as the user when w given)."""
    try:
        msg = (w or APP_W).genie.start_conversation_and_wait(GENIE_SPACE, question)
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
    """Find similar past cases by semantic search over PII-scrubbed email threads (app SP)."""
    r = APP_W.vector_search_indexes.query_index(
        index_name=IDX_EMAIL, columns=["case_ref", "case_type", "case_location", "chunk"],
        query_text=query, num_results=k)
    out = []
    for row in (r.result.data_array or []):
        ref, ctype, loc, chunk = row[0], row[1], row[2], row[3]
        out.append(f"**{ref}** ({ctype}, {loc})\n{chunk[:280]}...")
    return "\n\n".join(out) if out else "No similar cases found."


def sop_lookup(query: str, k: int = 2) -> str:
    """Retrieve the relevant SOP / procedure with a citation (app SP)."""
    r = APP_W.vector_search_indexes.query_index(
        index_name=IDX_SOP, columns=["sop_title", "chunk"], query_text=query, num_results=k)
    out = []
    for row in (r.result.data_array or []):
        out.append(f"**SOP: {row[0].replace('_', ' ')}**\n{row[1][:500]}")
    return "\n\n".join(out) if out else "No matching procedure found."


def get_case(case_ref: str, w=None) -> dict:
    """Fetch a single case record (structured + L1 tags + L2 analysis), masks per viewer."""
    cols, rows = _query(
        "SELECT Case_Ref, Case_Type, Case_Status, Case_Handler, Assigned_HCG, L1_Country, "
        "L1_Agencies, L1_Situational_Flags, Resolution_Days, Title_Name, Case_Description, "
        "L2_Summary_Pathway, L2_Assistance_Req_vs_Provided, L2_Complications_Delays, "
        "L2_External_Resources, L2_Lessons_Learnt "
        f"FROM {FQ}.gold_case_intelligence WHERE Case_Ref = '{case_ref}' LIMIT 1", w)
    if not rows:
        return {}
    return dict(zip(cols, rows[0]))


def count_cases(w=None) -> int:
    _, rows = _query(f"SELECT count(*) FROM {FQ}.gold_case_intelligence", w)
    return int(rows[0][0]) if rows else 0


def list_cases_page(page: int, page_size: int = 20, w=None):
    """Return (columns, rows) for one page, newest first."""
    offset = page * page_size
    return _query(
        "SELECT Case_Ref, Case_Type, L1_Country AS Country, Assigned_HCG AS Mission, "
        "Case_Status AS Status, Created_On "
        f"FROM {FQ}.gold_case_intelligence "
        f"ORDER BY to_date(Created_On) DESC, Case_Ref DESC LIMIT {page_size} OFFSET {offset}", w)


def recommended_steps(case_type: str, w=None) -> str:
    """Fetch the SOP steps for this case type (matched by normalised title)."""
    safe = (case_type or "").replace("'", "''")
    _, rows = _query(
        f"SELECT chunk FROM {FQ}.gold_sop_chunks "
        f"WHERE regexp_replace(lower(sop_title), '[^a-z]', '') = "
        f"regexp_replace(lower('{safe}'), '[^a-z]', '') LIMIT 1", w)
    return rows[0][0] if rows else "No matching SOP found for this case type."


def recommended_steps_list(case_type: str, w=None):
    """Parse the SOP chunk into an ordered list of step strings."""
    import re
    text = recommended_steps(case_type, w)
    return [re.sub(r"^\s*\d+\.\s*", "", ln).strip()
            for ln in text.splitlines() if re.match(r"^\s*\d+\.\s+", ln)]


def dashboard_data(w=None):
    """KPIs + chart series for the dashboard view."""
    G = f"{FQ}.gold_case_intelligence"
    def q(sql):
        c, r = _query(sql, w)
        return [dict(zip(c, row)) for row in r]
    kpi = q(f"SELECT count(*) total, sum(CASE WHEN Case_Status='Closed' THEN 1 ELSE 0 END) closed, "
            f"round(avg(Resolution_Days),1) avg_days FROM {G}")[0]
    return {
        "kpi": kpi,
        "by_type": q(f"SELECT Case_Type k, count(*) v FROM {G} GROUP BY Case_Type ORDER BY v DESC"),
        "by_country": q(f"SELECT L1_Country k, count(*) v FROM {G} GROUP BY L1_Country ORDER BY v DESC"),
        "by_mission": q(f"SELECT Assigned_HCG k, count(*) v FROM {G} GROUP BY Assigned_HCG ORDER BY v DESC"),
        "resolution": q(f"SELECT Case_Type k, round(avg(Resolution_Days),1) v FROM {G} "
                        f"WHERE Resolution_Days IS NOT NULL GROUP BY Case_Type ORDER BY v DESC"),
        "flags": q(f"SELECT flag k, count(*) v FROM {G} LATERAL VIEW explode(L1_Situational_Flags) t AS flag "
                   f"GROUP BY flag ORDER BY v DESC"),
    }


def get_notes(case_ref: str, w=None):
    safe = case_ref.replace("'", "''")
    cols, rows = _query(
        f"SELECT author, note, cast(created_at AS STRING) AS created_at FROM {FQ}.case_notes "
        f"WHERE Case_Ref = '{safe}' ORDER BY created_at DESC", w)
    return [dict(zip(cols, r)) for r in rows]


def add_note(case_ref: str, author: str, note: str, w=None):
    import uuid
    nid = uuid.uuid4().hex
    cr = case_ref.replace("'", "''")
    au = (author or "officer").replace("'", "''")
    nt = note.replace("'", "''")
    _query(f"INSERT INTO {FQ}.case_notes VALUES "
           f"('{nid}', '{cr}', '{au}', '{nt}', current_timestamp())", w)
