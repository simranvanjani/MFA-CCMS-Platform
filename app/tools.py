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
WAREHOUSE = os.getenv("DATABRICKS_WAREHOUSE_ID", "<warehouse-id>")
GENIE_SPACE = os.getenv("GENIE_SPACE_ID", "<genie-space-id>")
IDX_EMAIL = os.getenv("IDX_EMAIL", f"{FQ}.case_email_index")
IDX_SOP = os.getenv("IDX_SOP", f"{FQ}.sop_index")

APP_W = WorkspaceClient()  # app service principal (or CLI profile locally)


def _run_one(client, sql):
    from databricks.sdk.service.sql import StatementState
    r = client.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE, statement=sql, wait_timeout="30s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        import time; time.sleep(1)
        r = client.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        msg = r.status.error.message if r.status.error else str(r.status.state)
        raise RuntimeError(f"statement {r.status.state}: {msg}")
    cols = [c.name for c in r.manifest.schema.columns] if r.manifest else []
    rows = r.result.data_array if (r.result and r.result.data_array) else []
    return cols, rows


def _query(sql, w=None):
    """Run SQL as the user (OBO) when given; fall back to the app SP on any error
    so the app stays usable even if the user-token path fails."""
    clients = [c for c in (w, APP_W) if c is not None]
    last = None
    for client in clients:
        try:
            return _run_one(client, sql)
        except Exception as e:
            last = e
            continue
    if last:
        raise last
    return [], []


def _genie_conv_get(conv_id, w):
    if not conv_id:
        return None
    _, rows = _query(
        f"SELECT genie_conv_id FROM {FQ}.chat_genie WHERE conv_id = '{_esc(conv_id)}' LIMIT 1", w)
    return rows[0][0] if rows else None


def _genie_conv_set(conv_id, genie_conv_id, w):
    cid = _esc(conv_id)
    _query(f"DELETE FROM {FQ}.chat_genie WHERE conv_id = '{cid}'", w)
    _query(f"INSERT INTO {FQ}.chat_genie VALUES ('{cid}', '{_esc(genie_conv_id)}', current_timestamp())", w)


def _genie_text(msg):
    parts = []
    for a in (msg.attachments or []):
        if a.text and a.text.content:
            parts.append(a.text.content)
        if a.query and a.query.query:
            parts.append(f"\n_SQL:_ `{a.query.query}`")
    return "\n".join(parts) if parts else "Genie returned no answer."


def genie_query(question: str, w=None, conv_id=None) -> str:
    """Answer via Genie. Continues the same Genie conversation per chat thread so
    follow-ups ('in the last year') keep the prior context."""
    g = w or APP_W
    try:
        gcid = _genie_conv_get(conv_id, w)
        if gcid:
            try:
                return _genie_text(g.genie.create_message_and_wait(GENIE_SPACE, gcid, question))
            except Exception:
                gcid = None  # stale conversation — start fresh below
        msg = g.genie.start_conversation_and_wait(GENIE_SPACE, question)
        if conv_id and getattr(msg, "conversation_id", None):
            try:
                _genie_conv_set(conv_id, msg.conversation_id, w)
            except Exception:
                pass
        return _genie_text(msg)
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


def _esc(v):
    return str(v).replace("'", "''")


def _where(filters):
    """Build a WHERE clause from column filters + a free-text query."""
    if not filters:
        return ""
    c = []
    if filters.get("case_type"):
        c.append(f"Case_Type = '{_esc(filters['case_type'])}'")
    if filters.get("country"):
        c.append(f"L1_Country = '{_esc(filters['country'])}'")
    if filters.get("status"):
        c.append(f"Case_Status = '{_esc(filters['status'])}'")
    if filters.get("flag"):
        c.append(f"array_contains(L1_Situational_Flags, '{_esc(filters['flag'])}')")
    if filters.get("q"):
        cols = ["Case_Ref", "Case_Type", "L1_Country", "Case_Location", "Assigned_HCG",
                "Case_Description", "Case_Title", "Case_Subject", "Citizenship"]
        stop = {"case", "cases", "the", "a", "an", "in", "of", "for", "from", "and", "to",
                "with", "on", "last", "this", "show", "me", "all", "find"}
        toks = [t for t in filters["q"].split() if t.lower() not in stop and len(t) >= 2]
        if not toks:
            toks = [filters["q"]]
        # Each meaningful token must appear in at least one searched column (AND of tokens).
        for t in toks:
            te = _esc(t)
            c.append("(" + " OR ".join(f"{col} ILIKE '%{te}%'" for col in cols) + ")")
    return (" WHERE " + " AND ".join(c)) if c else ""


def count_cases(w=None, filters=None) -> int:
    _, rows = _query(f"SELECT count(*) FROM {FQ}.gold_case_intelligence{_where(filters)}", w)
    return int(rows[0][0]) if rows else 0


_SORTS = {
    "newest": "to_date(Created_On) DESC, Case_Ref DESC",
    "oldest": "to_date(Created_On) ASC, Case_Ref ASC",
    "res_asc": "Resolution_Days ASC NULLS LAST, to_date(Created_On) DESC",
    "res_desc": "Resolution_Days DESC NULLS LAST, to_date(Created_On) DESC",
}


def list_cases_page(page: int, page_size: int = 20, w=None, filters=None, sort="newest"):
    """Return (columns, rows) for one page, honoring filters + sort."""
    offset = page * page_size
    order = _SORTS.get(sort, _SORTS["newest"])
    return _query(
        "SELECT Case_Ref, Case_Type, L1_Country AS Country, Assigned_HCG AS Mission, "
        "Case_Status AS Status, Created_On, Resolution_Days "
        f"FROM {FQ}.gold_case_intelligence{_where(filters)} "
        f"ORDER BY {order} LIMIT {page_size} OFFSET {offset}", w)


def set_status(case_ref: str, status: str, author: str, w=None):
    """Update a case's operational status and log an audit note."""
    cr, st = _esc(case_ref), _esc(status)
    _query(f"UPDATE {FQ}.gold_case_intelligence SET Case_Status = '{st}' WHERE Case_Ref = '{cr}'", w)
    add_note(case_ref, author, f"Status changed to {status}.", w)


def filter_options(w=None):
    """Distinct values for the list-view filter dropdowns."""
    def vals(sql):
        return [r[0] for r in _query(sql, w)[1] if r[0]]
    return {
        "case_type": vals(f"SELECT DISTINCT Case_Type FROM {FQ}.gold_case_intelligence ORDER BY 1"),
        "country": vals(f"SELECT DISTINCT L1_Country FROM {FQ}.gold_case_intelligence WHERE L1_Country IS NOT NULL ORDER BY 1"),
        "status": vals(f"SELECT DISTINCT Case_Status FROM {FQ}.gold_case_intelligence ORDER BY 1"),
        "flag": vals(f"SELECT DISTINCT flag FROM {FQ}.gold_case_intelligence "
                     f"LATERAL VIEW explode(L1_Situational_Flags) t AS flag ORDER BY 1"),
    }


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


def step_status(case_ref: str, w=None):
    """Return the set of completed step indices for a case."""
    _, rows = _query(
        f"SELECT step_idx FROM {FQ}.case_steps WHERE Case_Ref = '{_esc(case_ref)}'", w)
    return {int(r[0]) for r in rows}


def set_step(case_ref: str, step_idx: int, done: bool, author: str, w=None):
    """Mark a SOP step done/undone for a case (a present row = done)."""
    cr, i = _esc(case_ref), int(step_idx)
    _query(f"DELETE FROM {FQ}.case_steps WHERE Case_Ref = '{cr}' AND step_idx = {i}", w)
    if done:
        au = _esc(author or "officer")
        _query(f"INSERT INTO {FQ}.case_steps VALUES ('{cr}', {i}, '{au}', current_timestamp())", w)


def steps_with_status(case_type: str, case_ref: str, w=None):
    """SOP steps annotated with per-case completion."""
    done = step_status(case_ref, w)
    return [{"idx": i, "text": t, "done": i in done}
            for i, t in enumerate(recommended_steps_list(case_type, w))]


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


def add_chat(conv_id: str, user_email: str, role: str, content: str, route=None, w=None):
    cid, em, rl = _esc(conv_id), _esc(user_email), _esc(role)
    ct, rt = _esc(content), (f"'{_esc(route)}'" if route else "NULL")
    _query(f"INSERT INTO {FQ}.chat_messages VALUES "
           f"('{cid}', '{em}', '{rl}', '{ct}', {rt}, current_timestamp())", w)


def chat_list(user_email: str, w=None):
    """Conversations for a user: id, title (first question), last-updated."""
    em = _esc(user_email)
    cols, rows = _query(f"""
        WITH firsts AS (
          SELECT conv_id, content,
                 row_number() OVER (PARTITION BY conv_id ORDER BY created_at) rn
          FROM {FQ}.chat_messages WHERE user_email = '{em}' AND role = 'user'),
        agg AS (SELECT conv_id, max(created_at) updated FROM {FQ}.chat_messages
                WHERE user_email = '{em}' GROUP BY conv_id)
        SELECT a.conv_id, f.content AS title, cast(a.updated AS STRING) AS updated
        FROM agg a JOIN firsts f ON a.conv_id = f.conv_id AND f.rn = 1
        ORDER BY a.updated DESC LIMIT 50""", w)
    return [dict(zip(cols, r)) for r in rows]


def chat_messages_for(conv_id: str, w=None):
    cid = _esc(conv_id)
    cols, rows = _query(
        f"SELECT role, content, route FROM {FQ}.chat_messages "
        f"WHERE conv_id = '{cid}' ORDER BY created_at", w)
    return [dict(zip(cols, r)) for r in rows]


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
