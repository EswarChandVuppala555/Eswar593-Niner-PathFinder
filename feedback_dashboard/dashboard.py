# feedback_dashboard/dashboard.py

import streamlit as st
import pandas as pd
import os
import matplotlib.pyplot as plt

st.set_page_config(page_title="Feedback Dashboard", layout="wide")

st.title("📊 Feedback Dashboard")

# This is the path inside the container; it matches your docker-compose volume mount
FEEDBACK_FILE = "/app/data/feedback_log.csv"

if not os.path.exists(FEEDBACK_FILE):
    st.warning("No feedback data found yet. Submit some feedback from the chat app first.")
else:
    try:
        df = pd.read_csv(FEEDBACK_FILE)
    except Exception as e:
        st.error(f"Could not read feedback file: {e}")
        st.stop()

    if df.empty:
        st.warning("Feedback log is empty.")
        st.stop()

    # Ensure timestamp is datetime
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    st.subheader("Overall Summary")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total Feedback", len(df))
    with c2:
        st.metric("👍 Positive", int((df["feedback_type"] == "positive").sum()))
    with c3:
        st.metric("👎 Negative", int((df["feedback_type"] == "negative").sum()))

    st.divider()

    st.subheader("Feedback Distribution")
    if "feedback_type" in df.columns:
        counts = df["feedback_type"].value_counts()
        # Pie chart with matplotlib (as requested in earlier code)
        fig, ax = plt.subplots()
        ax.pie(counts, labels=counts.index, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")
        st.pyplot(fig)
    else:
        st.info("No 'feedback_type' column found.")

    st.divider()

    st.subheader("Reasons for Negative Feedback")
    if {"feedback_type", "feedback_reason"} <= set(df.columns):
        neg = df[df["feedback_type"] == "negative"]["feedback_reason"].fillna("")
        reason_counts = neg[neg != ""].value_counts()
        if not reason_counts.empty:
            st.bar_chart(reason_counts)
        else:
            st.info("No reasons submitted yet.")
    else:
        st.info("No 'feedback_reason' column found.")

    st.divider()

    st.subheader("Recent Feedback Comments")
    show_cols = [c for c in ["timestamp", "feedback_type", "feedback_reason", "feedback_text"] if c in df.columns]
    if show_cols:
        st.dataframe(
            df[show_cols].sort_values(by="timestamp", ascending=False).head(12),
            use_container_width=True
        )
    else:
        st.info("No comment columns found to display.")

    st.divider()

    st.subheader("Feedback Trend Over Time")
    if "timestamp" in df.columns and "feedback_type" in df.columns:
        trend = (
            df.dropna(subset=["timestamp"])
              .groupby(df["timestamp"].dt.date)["feedback_type"]
              .value_counts()
              .unstack()
              .fillna(0)
        )
        if not trend.empty:
            st.line_chart(trend)
        else:
            st.info("No trend data available.")
    else:
        st.info("Missing 'timestamp' or 'feedback_type' for trend chart.")
