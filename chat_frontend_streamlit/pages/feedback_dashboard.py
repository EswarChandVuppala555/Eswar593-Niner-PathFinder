import streamlit as st
import pandas as pd
import os
import matplotlib.pyplot as plt

st.set_page_config(page_title="📊 Feedback Dashboard", layout="wide")

st.title("📊 Niner Pathfinder – Feedback Dashboard")

FEEDBACK_FILE = "/app/data/feedback_log.csv"

if not os.path.exists(FEEDBACK_FILE):
    st.warning("⚠️ No feedback log found yet. Interact with the chatbot first.")
else:
    df = pd.read_csv(FEEDBACK_FILE)

    if df.empty:
        st.warning("⚠️ Feedback log is empty.")
    else:
        # Convert timestamp to datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

        # Sidebar filters
        st.sidebar.header("Filters")
        start_date = st.sidebar.date_input("Start Date", df["timestamp"].min().date())
        end_date = st.sidebar.date_input("End Date", df["timestamp"].max().date())

        catalog_filter = st.sidebar.multiselect(
            "Catalog Year", options=sorted(df["student_catalog_year"].dropna().unique())
        )
        degree_filter = st.sidebar.multiselect(
            "Degree Program", options=sorted(df["student_degree_program"].dropna().unique())
        )

        # Apply filters
        mask = (df["timestamp"].dt.date >= start_date) & (df["timestamp"].dt.date <= end_date)
        if catalog_filter:
            mask &= df["student_catalog_year"].isin(catalog_filter)
        if degree_filter:
            mask &= df["student_degree_program"].isin(degree_filter)

        df = df[mask]

        st.subheader("📈 Summary Statistics")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Feedback", len(df))
        with col2:
            st.metric("👍 Positive", (df["feedback_type"] == "positive").sum())
        with col3:
            st.metric("👎 Negative", (df["feedback_type"] == "negative").sum())

        st.markdown("---")

        # --- Chart 1: Feedback Type Count ---
        st.subheader("📊 Feedback Type Distribution")
        feedback_counts = df["feedback_type"].value_counts()
        st.bar_chart(feedback_counts)

        # --- Chart 2: Feedback Over Time ---
        st.subheader("📅 Feedback Over Time")
        trend = df.groupby(df["timestamp"].dt.date)["feedback_type"].count()
        st.line_chart(trend)

        # --- Chart 3: Distribution by Degree Program ---
        st.subheader("🎓 Distribution by Degree Program")
        program_counts = df["student_degree_program"].value_counts()
        fig, ax = plt.subplots()
        ax.pie(program_counts, labels=program_counts.index, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")
        st.pyplot(fig)

        st.markdown("---")

        st.subheader("📝 Raw Feedback Records")
        st.dataframe(df)
