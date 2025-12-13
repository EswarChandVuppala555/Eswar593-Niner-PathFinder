import os
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import csv

st.set_page_config(page_title="📊 Feedback Dashboard", layout="wide")
st.title("📊 Niner Pathfinder – Feedback Dashboard")

FEEDBACK_FILE = "/app/data/feedback_log.csv"  # keep in sync with backend writer

def safe_read_feedback(path: str) -> pd.DataFrame:
    # Split on commas that are OUTSIDE of double-quotes; skip malformed lines.
    return pd.read_csv(
        path,
        engine="python",
        sep=r',(?=(?:[^"]*"[^"]*")*[^"]*$)',
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        on_bad_lines="skip",
        encoding="utf-8-sig",
    )


# ---- Load --------------------------------------------------------------------
if not os.path.exists(FEEDBACK_FILE):
    st.warning("⚠️ No feedback log found yet. Interact with the chatbot first.")
    st.stop()

try:
    df = safe_read_feedback(FEEDBACK_FILE)
except Exception as e:
    st.error(f"Could not read feedback file: {e}")
    st.stop()

if df.empty:
    st.warning("⚠️ Feedback log is empty.")
    st.stop()

# ---- Normalize expected columns ---------------------------------------------
expected_cols = {
    "timestamp": pd.NaT,
    "feedback_type": "",
    "feedback_reason": "",
    "feedback_text": "",
    "student_catalog_year": "",
    "student_degree_program": "",
    "student_credits_earned": "",
}
for col, default in expected_cols.items():
    if col not in df.columns:
        df[col] = default

# Timestamp to datetime
df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

# --- Normalize & sanitize feedback_type and other text cols ---
# Map common variants/emojis to canonical values
mapping = {
    "👍 helpful": "positive",
    "👎 not helpful": "negative",
    "helpful": "positive",
    "not helpful": "negative",
    "pos": "positive",
    "neg": "negative",
}
ft = (
    df["feedback_type"]
    .astype(str).str.strip().str.lower()
    .replace(mapping)
)
# Keep only valid types; drop rows where malformed text leaked into the column
df["feedback_type"] = ft
df = df[df["feedback_type"].isin(["positive", "negative"])].copy()

# (Optional) tidy long free-text fields for UI
for col in ["feedback_text", "feedback_reason"]:
    if col in df.columns:
        df[col] = (
            df[col].astype(str)
            .str.replace("\r\n|\r|\n", " ", regex=True)
            .str.slice(0, 2000)
        )


# ---- Sidebar filters ---------------------------------------------------------
st.sidebar.header("Filters")

# If timestamps present, use real min/max; else disable date filter
has_ts = df["timestamp"].notna().any()
if has_ts:
    min_date = df["timestamp"].min().date()
    max_date = df["timestamp"].max().date()
else:
    # Show all by default when timestamps are missing/invalid
    min_date = max_date = None

start_date = st.sidebar.date_input("Start Date", min_date) if has_ts else None
end_date   = st.sidebar.date_input("End Date",   max_date) if has_ts else None

catalog_options = sorted(df["student_catalog_year"].dropna().astype(str).unique())
degree_options  = sorted(df["student_degree_program"].dropna().astype(str).unique())

catalog_filter = st.sidebar.multiselect("Catalog Year", options=catalog_options)
degree_filter  = st.sidebar.multiselect("Degree Program", options=degree_options)

# ---- Apply filters -----------------------------------------------------------
mask = pd.Series(True, index=df.index)

if has_ts and start_date and end_date:
    mask &= (df["timestamp"].dt.date >= start_date) & (df["timestamp"].dt.date <= end_date)

if catalog_filter:
    mask &= df["student_catalog_year"].astype(str).isin(catalog_filter)
if degree_filter:
    mask &= df["student_degree_program"].astype(str).isin(degree_filter)

filtered = df[mask].copy()
if filtered.empty:
    st.info("No feedback matches the current filters.")
    st.stop()
df = filtered

# --- Tiny debug readout (temporary) ------------------------------------------
with st.expander("🧪 Debug (temporary)"):
    st.write("**FEEDBACK_FILE path:**", FEEDBACK_FILE)
    try:
        import os
        st.write("**Exists:**", os.path.exists(FEEDBACK_FILE))
        if os.path.exists(FEEDBACK_FILE):
            st.write("**File size (bytes):**", os.path.getsize(FEEDBACK_FILE))
    except Exception as _e:
        st.write("File stat error:", _e)

    st.write("**Rows after filters:**", len(df))
    st.write("**Columns:**", list(df.columns))
    # show a quick peek
    st.write("**Head (5):**")
    st.dataframe(df.head(5), use_container_width=True)
    st.write("**Tail (5):**")
    st.dataframe(df.tail(5), use_container_width=True)


# ---- Summary ----------------------------------------------------------------
st.subheader("📈 Summary Statistics")
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Feedback", len(df))
with col2:
    st.metric("👍 Positive", int((df["feedback_type"] == "positive").sum()))
with col3:
    st.metric("👎 Negative", int((df["feedback_type"] == "negative").sum()))

st.markdown("---")

# ---- Chart 1: Feedback Type Distribution ------------------------------------
st.subheader("📊 Feedback Type Distribution")
if not df["feedback_type"].dropna().empty:
    counts = df["feedback_type"].value_counts().sort_index()
    st.bar_chart(counts)
else:
    st.caption("No feedback type data available.")

# ---- Chart 2: Feedback Over Time --------------------------------------------
st.subheader("📅 Feedback Over Time")
if df["timestamp"].notna().any():
    trend = df.loc[df["timestamp"].notna()].groupby(df["timestamp"].dt.date)["feedback_type"].count()
    if not trend.empty:
        st.line_chart(trend)
    else:
        st.caption("No timestamp data to chart.")
else:
    st.caption("No valid timestamps to chart.")

# ---- Chart 3: Distribution by Degree Program --------------------------------
st.subheader("🎓 Distribution by Degree Program")
prog_counts = df["student_degree_program"].dropna().astype(str).value_counts()
if not prog_counts.empty:
    fig, ax = plt.subplots()
    ax.pie(prog_counts, labels=prog_counts.index, autopct="%1.1f%%", startangle=90)
    ax.axis("equal")
    st.pyplot(fig)
else:
    st.caption("No degree program data to chart.")

st.markdown("---")

# ---- Table -------------------------------------------------------------------
st.subheader("📝 Raw Feedback Records")
st.dataframe(df, use_container_width=True)

# Optional: download filtered slice
st.download_button(
    "⬇️ Download filtered CSV",
    data=df.to_csv(index=False),
    file_name="feedback_filtered.csv",
    mime="text/csv",
    use_container_width=True,
)