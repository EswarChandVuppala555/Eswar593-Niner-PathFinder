import streamlit as st
import pandas as pd
import os
import matplotlib.pyplot as plt

st.set_page_config(page_title="Feedback Dashboard", layout="wide")

st.title("📊 Feedback Dashboard")

FEEDBACK_FILE = "/app/data/feedback_log.csv"

if not os.path.exists(FEEDBACK_FILE):
    st.warning("No feedback data found yet. Submit some feedback from the chat app first.")
else:
    df = pd.read_csv(FEEDBACK_FILE)

    if df.empty:
        st.warning("Feedback log is empty.")
    else:
        # Convert timestamp to datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

        # --- Summary Stats ---
        st.subheader("Overall Summary")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Total Feedback", len(df))
        with col2:
            st.metric("👍 Positive", (df["feedback_type"] == "positive").sum())
        with col3:
            st.metric("👎 Negative", (df["feedback_type"] == "negative").sum())


        st.divider()

        # --- Feedback Distribution ---
        st.subheader("Feedback Distribution")
        feedback_counts = df["feedback_type"].value_counts()

        fig1, ax1 = plt.subplots()
        ax1.pie(feedback_counts, labels=feedback_counts.index, autopct='%1.1f%%', startangle=90)
        ax1.axis("equal")
        st.pyplot(fig1)

        st.divider()

        # --- Top Reasons for 👎 Feedback ---
        st.subheader("Reasons for Negative Feedback")
        if "feedback_reason" in df.columns:
            negative_reasons = df[df["feedback_type"] == "negative"]["feedback_reason"].value_counts()
            negative_reasons = negative_reasons.sort_values(ascending=True)
            st.bar_chart(negative_reasons)

        st.divider()

        # --- Recent Comments ---
        st.subheader("Recent Feedback Comments")
        st.dataframe(df[["timestamp", "feedback_type", "feedback_reason", "feedback_text"]].sort_values(by="timestamp", ascending=False).head(10))

        st.divider()

        # --- Trend Over Time ---
        st.subheader("Feedback Trend Over Time")
        trend = df.resample("W", on="timestamp")["feedback_type"].value_counts().unstack().fillna(0)
        st.line_chart(trend)
