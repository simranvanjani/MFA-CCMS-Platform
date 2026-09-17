"""Case Intelligence app — chat (3 routes) + case view + embedded dashboard."""
import os

import streamlit as st

st.set_page_config(page_title="MFA Consular Case Intelligence", page_icon="🌏", layout="wide")

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")

st.title("🌏 Consular Case Intelligence")
st.caption("Turning CCMS case emails into structured, analysable intelligence — demo on Databricks.")

tab_chat, tab_case, tab_dash = st.tabs(["💬 Ask", "🗂️ Case view", "📊 Dashboard"])

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

with tab_case:
    import tools
    refs = tools.list_case_refs()
    ref = st.selectbox("Select a case", refs)
    if ref:
        c = tools.get_case(ref)
        if c:
            a, b = st.columns(2)
            with a:
                st.subheader("Structured (CCMS)")
                st.write({k: c.get(k) for k in
                          ["Case_Ref", "Case_Type", "Case_Status", "Case_Handler",
                           "Assigned_HCG", "Title_Name", "Resolution_Days"]})
                st.subheader("Layer 1 tags")
                st.write({k: c.get(k) for k in
                          ["L1_Country", "L1_Agencies", "L1_Situational_Flags"]})
            with b:
                st.subheader("Layer 2 analysis")
                for k in ["L2_Summary_Pathway", "L2_Assistance_Req_vs_Provided",
                          "L2_Complications_Delays", "L2_External_Resources", "L2_Lessons_Learnt"]:
                    st.markdown(f"**{k.replace('L2_', '').replace('_', ' ')}**")
                    st.write(c.get(k))
            st.subheader("Case description")
            st.write(c.get("Case_Description"))

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
