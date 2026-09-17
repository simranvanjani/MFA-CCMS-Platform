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
    if DASHBOARD_URL:
        st.markdown(f"[Open the full AI/BI dashboard]({DASHBOARD_URL})")
        st.components.v1.iframe(DASHBOARD_URL, height=800)
    else:
        st.info("Set DASHBOARD_URL to embed the Layer 3 dashboard.")
