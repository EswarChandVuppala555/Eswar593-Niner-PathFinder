# feedback_dashboard.py
import streamlit as st
import pandas as pd
import os


def safe_read_feedback(path: str) -> pd.DataFrame:
    # Robust CSV read: split only on commas outside quotes; skip malformed lines
    return pd.read_csv(
        path,
        engine="python",
        sep=r',(?=(?:[^"]*"[^"]*")*[^"]*$)',
        on_bad_lines="skip",
    )

st.set_page_config(page_title="Feedback Dashboard", layout="wide")

FEEDBACK_FILE = "/app/data/feedback_log.csv"

st.title("📊 Feedback Dashboard")

if not os.path.exists(FEEDBACK_FILE):
    st.warning("No feedback data found yet.")
else:
    df = safe_read_feedback(FEEDBACK_FILE)
    # --- Tiny debug (temporary) ---
    st.caption(f"📁 Debug: loaded {len(df)} rows from feedback_log.csv")
    st.caption(f"🧱 Columns: {', '.join(df.columns.astype(str))}")
    st.caption(f"📄 Loaded {len(df)} feedback rows from file.")
    st.dataframe(df.head(5))


    # Clean up dataframe columns if any spaces exist
    df.columns = df.columns.str.strip()

    # Optional: Convert pursued_courses into a string (if it’s stored as a list)
    if "pursued_courses" in df.columns:
        df["pursued_courses"] = df["pursued_courses"].astype(str)

    # --- Optional Filtering by Major ---
    if "student_degree_program" in df.columns:
        st.sidebar.header("Filters")
        selected_major = st.sidebar.selectbox(
            "Filter by Major", ["All"] + sorted(df["student_degree_program"].dropna().unique().tolist())
        )
        if selected_major != "All":
            df = df[df["student_degree_program"] == selected_major]
    # --- Tiny debug after filtering ---
    st.caption(f"🔎 Debug after filters: {len(df)} rows currently in view")


    # --- If file is empty ---
    if df.empty:
        st.warning("Feedback log is empty.")
    else:
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

        # --- Summary Metrics ---
        st.subheader("Summary Overview")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Feedback", len(df))
        with col2:
            st.metric("👍 Positive", (df["feedback_type"] == "positive").sum())
        with col3:
            st.metric("👎 Negative", (df["feedback_type"] == "negative").sum())

        # --- Courses Section ---
        if "pursued_courses" in df.columns:
            st.subheader("🎓 Courses Selected by Students")
            st.dataframe(df[["student_degree_program", "pursued_courses"]].tail(20))

            # --- Visualization: Most Common Courses ---
            st.subheader("📊 Most Commonly Selected Courses")
            course_counts = (
                df["pursued_courses"]
                .dropna()
                .str.replace("[\[\]']", "", regex=True)
                .str.split(",")
                .explode()
                .str.strip()
                .value_counts()
                .head(10)
            )
            if not course_counts.empty:
                st.bar_chart(course_counts)
            else:
                st.info("No course data available yet for visualization.")

        # --- Display all feedback table ---
        st.subheader("🗂️ All Feedback Records")
        st.dataframe(df.tail(20))

        # --- Optional Export Button ---
        st.download_button(
            label="⬇️ Download Full Feedback CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="feedback_log.csv",
            mime="text/csv"
        )
