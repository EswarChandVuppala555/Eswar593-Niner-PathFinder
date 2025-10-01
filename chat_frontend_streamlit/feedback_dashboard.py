# feedback_dashboard.py
import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="Feedback Dashboard", layout="wide")

FEEDBACK_FILE = "/app/data/feedback_log.csv"

st.title("📊 Feedback Dashboard")

if not os.path.exists(FEEDBACK_FILE):
    st.warning("No feedback data found yet.")
else:
    df = pd.read_csv(FEEDBACK_FILE)
    if df.empty:
        st.warning("Feedback log is empty.")
    else:
        st.subheader("Summary")
        st.metric("Total Feedback", len(df))
        st.metric("👍 Positive", (df["feedback_type"] == "positive").sum())
        st.metric("👎 Negative", (df["feedback_type"] == "negative").sum())

        st.subheader("All Feedback")
        st.dataframe(df.tail(20))
