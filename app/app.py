"""Case Intelligence app — chat (3 routes) + case view + embedded dashboard."""
import os

import streamlit as st

st.set_page_config(page_title="MFA Consular Case Intelligence", page_icon="🌏", layout="wide")

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")

st.title("🌏 Consular Case Intelligence")
st.caption("Turning CCMS case emails into structured, analysable intelligence — demo on Databricks.")

tab_cases, tab_chat, tab_dash = st.tabs(["🗂️ Cases", "💬 Ask", "📊 Dashboard"])


def _current_officer():
    try:
        h = st.context.headers
        return h.get("X-Forwarded-Email") or h.get("X-Forwarded-Preferred-Username") or "officer"
    except Exception:
        return "officer"


STATUS_BADGE = {"Closed": "🟢 Closed", "Pending": "🟠 Pending"}

with tab_chat:
    import agent
    st.markdown(
        "Ask across cases (counts/trends), find **similar cases**, or ask for the correct "
        "**procedure**. Answers are cited.")
    if "history" not in st.session_state:
        st.session_state.history = []
    for role, text, route in st.session_state.history:
        with st.chat_message(role):
            if route:
                st.caption(f"route: {route}")
            st.markdown(text)
    q = st.chat_input("e.g. Arrest & detention cases in Thailand where ICA was involved")
    if q:
        st.session_state.history.append(("user", q, None))
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    route_name, ans = agent.answer(q)
                except Exception as e:
                    route_name, ans = "error", f"Error: {e}"
            st.caption(f"route: {route_name}")
            st.markdown(ans)
        st.session_state.history.append(("assistant", ans, route_name))

with tab_cases:
    import tools

    PAGE_SIZE = 20
    st.session_state.setdefault("page", 0)
    st.session_state.setdefault("sel_case", None)

    def _open(ref):
        st.session_state.sel_case = ref

    def _back():
        st.session_state.sel_case = None

    # ---------- RECORD VIEW ----------
    if st.session_state.sel_case:
        ref = st.session_state.sel_case
        c = tools.get_case(ref)
        st.button("← Back to case list", on_click=_back)
        if not c:
            st.warning("Case not found.")
        else:
            st.header(f"{ref}")
            st.markdown(f"**{c.get('Case_Type')}** · {c.get('L1_Country')} · "
                        f"{STATUS_BADGE.get(c.get('Case_Status'), c.get('Case_Status'))}")
            left, right = st.columns([3, 2])
            with left:
                st.subheader("Case details")
                st.write({k: c.get(k) for k in
                          ["Case_Ref", "Case_Type", "Case_Status", "Case_Handler",
                           "Assigned_HCG", "Title_Name", "Resolution_Days"]})
                st.markdown("**Layer 1 tags**")
                st.write({k: c.get(k) for k in ["L1_Country", "L1_Agencies", "L1_Situational_Flags"]})
                st.markdown("**Description**")
                st.write(c.get("Case_Description"))
                with st.expander("Layer 2 analysis", expanded=True):
                    for k in ["L2_Summary_Pathway", "L2_Assistance_Req_vs_Provided",
                              "L2_Complications_Delays", "L2_External_Resources", "L2_Lessons_Learnt"]:
                        st.markdown(f"**{k.replace('L2_', '').replace('_', ' ')}**")
                        st.write(c.get(k))
                with st.expander("✅ Recommended next steps (SOP)", expanded=True):
                    st.markdown(tools.recommended_steps(c.get("Case_Type")))
            with right:
                st.subheader("🗒️ Notes")
                with st.form(f"note_form_{ref}", clear_on_submit=True):
                    txt = st.text_area("Record a step taken / update", height=100,
                                       placeholder="e.g. Contacted next-of-kin; confirmed welfare with mission.")
                    if st.form_submit_button("Add note") and txt.strip():
                        tools.add_note(ref, _current_officer(), txt.strip())
                        st.success("Note added.")
                        st.rerun()
                for n in tools.get_notes(ref):
                    st.markdown(f"**{n['author']}** · _{n['created_at']}_")
                    st.info(n["note"])
                if not tools.get_notes(ref):
                    st.caption("No notes yet.")

    # ---------- LIST VIEW ----------
    else:
        total = tools.count_cases()
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(st.session_state.page, pages - 1)

        top = st.columns([4, 1, 1, 1])
        top[0].markdown(f"**{total} cases** · newest first · page {page + 1} of {pages}")
        if top[1].button("⏮ First", disabled=(page == 0)):
            st.session_state.page = 0; st.rerun()
        if top[2].button("◀ Prev", disabled=(page == 0)):
            st.session_state.page = page - 1; st.rerun()
        if top[3].button("Next ▶", disabled=(page >= pages - 1)):
            st.session_state.page = page + 1; st.rerun()

        cols, rows = tools.list_cases_page(page, PAGE_SIZE)
        hdr = st.columns([2.2, 2.2, 1.6, 1.6, 1.2, 1.4, 1])
        for col, label in zip(hdr, ["Case Ref", "Type", "Country", "Mission", "Status", "Created", ""]):
            col.markdown(f"**{label}**")
        for r in rows:
            row = dict(zip(cols, r))
            cc = st.columns([2.2, 2.2, 1.6, 1.6, 1.2, 1.4, 1])
            cc[0].write(row["Case_Ref"])
            cc[1].write(row["Case_Type"])
            cc[2].write(row["Country"])
            cc[3].write(row["Mission"])
            cc[4].write(STATUS_BADGE.get(row["Status"], row["Status"]))
            cc[5].write(row["Created_On"])
            cc[6].button("View", key=f"view_{row['Case_Ref']}", on_click=_open, args=(row["Case_Ref"],))

with tab_dash:
    import pandas as pd
    import tools as _t

    if DASHBOARD_URL:
        st.markdown(f"↗ [Open the full AI/BI dashboard in Databricks]({DASHBOARD_URL})")

    def df(sql):
        cols, rows = _t._query(sql)
        return pd.DataFrame(rows, columns=cols)

    G = f"{_t.FQ}.gold_case_intelligence"

    @st.cache_data(ttl=300)
    def load():
        kpi = df(f"SELECT count(*) total, "
                 f"sum(CASE WHEN Case_Status='Closed' THEN 1 ELSE 0 END) closed, "
                 f"round(avg(Resolution_Days),1) avg_days FROM {G}")
        by_type = df(f"SELECT Case_Type, count(*) cases FROM {G} GROUP BY Case_Type ORDER BY cases DESC")
        by_country = df(f"SELECT L1_Country, count(*) cases FROM {G} GROUP BY L1_Country ORDER BY cases DESC")
        by_mission = df(f"SELECT Assigned_HCG mission, count(*) cases FROM {G} GROUP BY Assigned_HCG ORDER BY cases DESC")
        res = df(f"SELECT Case_Type, round(avg(Resolution_Days),1) avg_days FROM {G} "
                 f"WHERE Resolution_Days IS NOT NULL GROUP BY Case_Type ORDER BY avg_days DESC")
        flags = df(f"SELECT flag, count(*) cases FROM {G} LATERAL VIEW explode(L1_Situational_Flags) t AS flag "
                   f"GROUP BY flag ORDER BY cases DESC")
        return kpi, by_type, by_country, by_mission, res, flags

    kpi, by_type, by_country, by_mission, res, flags = load()

    k1, k2, k3 = st.columns(3)
    k1.metric("Total cases", int(kpi["total"][0]))
    k2.metric("Closed cases", int(kpi["closed"][0]))
    k3.metric("Avg resolution (days)", float(kpi["avg_days"][0]))

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Case-type distribution")
        st.bar_chart(by_type.set_index("Case_Type"))
        st.subheader("Handling volume by mission")
        st.bar_chart(by_mission.set_index("mission"))
    with c2:
        st.subheader("Cases by country")
        st.bar_chart(by_country.set_index("L1_Country"))
        st.subheader("Avg resolution days by case type")
        st.bar_chart(res.set_index("Case_Type"))
    st.subheader("Situational flags")
    st.bar_chart(flags.set_index("flag"))
