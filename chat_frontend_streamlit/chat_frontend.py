import os
import requests
import streamlit as st
from src.menu_options import Catalog_Menu_Options_Loader
import logging
import pandas as pd
import io
import re


# --- Grade helpers used on the frontend for export filtering ---
PASSING_GRADES = {"A", "B", "C", "D", "P", "S", "H"}  # UNC-Charlotte style

def _is_pass_frontend(grade: str) -> bool:
    """Return True if the grade counts as passed (for export filtering)."""
    if not grade:
        # If we don't know, treat as passed so we don't nag them again
        return True
    g = grade.strip().upper()
    if g in PASSING_GRADES:
        return True
    if g in {"F", "I", "IP", "W", "WE", "U", "NR", "N", "AU"}:
        return False
    # Any strange code → assume passed (conservative)
    return True

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_PORT = os.getenv("CHAT_BACKEND_PORT", "8001")
BACKEND_BASE = f"http://chat-backend:{BACKEND_PORT}"           # works from inside Docker
API_BASE     = f"http://host.docker.internal:{BACKEND_PORT}"

st.set_page_config(page_title="Student Chat", page_icon="💬")
st.title("⛏️ Niner Pathfinder ⛏️")

# Unique suffix for widget keys so we can “reset” them safely
if "reset_nonce" not in st.session_state:
    st.session_state["reset_nonce"] = 0

def _normalize_courses_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    code_col = next((c for c in ["course_code", "code", "course id", "courseid", "course"] if c in df.columns), None)
    name_col = next((c for c in ["course_name", "name", "title"] if c in df.columns), None)
    grade_col = next((c for c in ["grade", "letter", "result", "status"] if c in df.columns), None)

    if not code_col:
        return pd.DataFrame(columns=["course_code", "course_name", "grade"])

    clean = pd.DataFrame()
    clean["course_code"] = df[code_col].astype(str).str.strip()
    clean["course_name"] = df[name_col].astype(str).str.strip() if name_col else ""

    # Standardize grade (optional but helpful)
    if grade_col:
        g = df[grade_col].astype(str).str.strip().str.upper()
        # normalize common words → letters
        g = g.replace({
            "PASS": "P", "PASSED": "P",
            "FAIL": "F", "FAILED": "F",
        })
        clean["grade"] = g
    else:
        clean["grade"] = ""  # missing grade is allowed

    clean = clean[clean["course_code"] != ""].drop_duplicates().reset_index(drop=True)
    return clean

COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,4}\s*\d{3,4})\b")


def extract_suggested_courses_from_text(answer: str, passed_codes: set[str] | None = None):
    """
    Extract course suggestions from the assistant answer, but:

    - drop any course the student already PASSED
    - drop rows where the 'name' is just comments like 'completed' or
      'if prerequisites met', 'if applicable', etc.
    """
    if not answer:
        return []

    # Normalize & strip markdown noise
    text = answer.replace("**", "").replace("`", "")

    # Normalize passed_codes to a set of uppercased codes
    passed_codes = {c.strip().upper() for c in (passed_codes or set())}

    # COURSE #### optionally followed by (Course Name)
    pattern = re.compile(
        r"\b([A-Z]{2,4}\s?\d{3,4})\b(?:\s*\(([^)]+)\))?"
    )

    matches = pattern.findall(text)

    # Phrases we treat as *comments*, not real course names
    bad_name_substrings = [
        "completed",
        "if prerequisites met",
        "if prerequisite",
        "if applicable",
        "if prereqs",
    ]

    by_code: dict[str, str] = {}
    for code, name in matches:
        code_norm = (code or "").strip().upper()
        if not code_norm:
            continue

        # 1) Skip if this course is already passed
        if code_norm in passed_codes:
            continue

        # Clean name & drop "comment" names
        name_clean = (name or "").strip()
        name_clean_lower = name_clean.lower()
        if any(bad in name_clean_lower for bad in bad_name_substrings):
            name_clean = ""  # treat as unknown/empty

        # Deduplicate: keep first non-empty name
        if code_norm not in by_code or (not by_code[code_norm] and name_clean):
            by_code[code_norm] = name_clean

    rows = [
        {"course_code": code, "course_name": by_code[code]}
        for code in sorted(by_code.keys())
    ]
    return rows


# Helper: pick an API base that works (host or docker-internal)
def _choose_api_base() -> str:
    for base in (BACKEND_BASE, API_BASE):
        try:
            r = requests.get(f"{base}/openapi.json", timeout=1)
            if r.ok:
                return base
        except Exception:
            pass
    return API_BASE  # fallback

API = _choose_api_base()



# Initialize menu options
if not st.session_state.get('catalog_menu_options'):
    menu_options = Catalog_Menu_Options_Loader()
    st.session_state['catalog_menu_options'] = menu_options.year_degree_major_conc_options  # old structure
    st.session_state['catalog_menu_options_tree'] = menu_options.year_degree_major_conc_tree  # new tree
    logger.info("Catalog menu options initialized in session state.")
    logger.info(f"Loaded menu options: {st.session_state['catalog_menu_options']}")


# Sidebar for collecting key student information
with st.sidebar:
    st.header("Student Information")
    
    catalog_year_options = ['']
    catalog_year_options.extend(list(st.session_state.get('catalog_menu_options').keys()))
    selected_catalog_year = ''

    selected_catalog_year = st.selectbox(
        label="Catalog Academic Year",
        options=catalog_year_options,
        key="catalog_year"
    )
    
    # Reset messages if catalog year changes
    if selected_catalog_year != st.session_state.get("prev_catalog_year", ""):
        st.session_state["prev_catalog_year"] = selected_catalog_year
        if "messages" in st.session_state:
            st.session_state.messages = []
        st.session_state.pursued_courses = []
        for k in ["pursued_courses", "pursued_courses_preview","pursued_courses_file_bytes", "pursued_courses_file_meta", "courses_upload"]:
            st.session_state.pop(k, None)
        st.session_state["reset_nonce"] += 1
    
    # --- Degree & Major from the new tree ---
    # We stored both structures earlier:
    #   st.session_state['catalog_menu_options']       # back-compat (year -> degree_major -> [concs])
    #   st.session_state['catalog_menu_options_tree']  # new tree (year -> degree -> major -> [concs])

    # Safely pull the tree; if missing, rebuild once
    cat_tree = st.session_state.get('catalog_menu_options_tree')
    if cat_tree is None:
        loader = Catalog_Menu_Options_Loader()
        st.session_state['catalog_menu_options'] = loader.year_degree_major_conc_options
        st.session_state['catalog_menu_options_tree'] = loader.year_degree_major_conc_tree
        cat_tree = st.session_state['catalog_menu_options_tree']

    selected_degree = ""
    selected_major = ""
    combined_key_for_selection = ""  # original degree_major label/code for backend + concentrations

    # Degree dropdown (by year)
    degree_options = [""]
    if selected_catalog_year:
        year_tree = cat_tree.get(selected_catalog_year, {})
        degree_options = [""] + sorted(year_tree.keys())

    selected_degree = st.selectbox(
        label="Degree",
        options=degree_options,
        key="degree"
    )

    # Major dropdown (by degree)
    major_options = [""]
    if selected_catalog_year and selected_degree:
        major_options = [""] + sorted(cat_tree[selected_catalog_year].get(selected_degree, {}).keys())

    selected_major = st.selectbox(
        label="Major",
        options=major_options,
        key="major"
    )

    # Reset conversation if degree/major changed
    curr_degmaj = f"{selected_degree}::{selected_major}"
    if curr_degmaj != st.session_state.get("prev_degree_program", ""):
        st.session_state["prev_degree_program"] = curr_degmaj
        if "messages" in st.session_state:
            st.session_state.messages = []
        st.session_state.pursued_courses = []
        for k in ["pursued_courses", "pursued_courses_preview",
          "pursued_courses_file_bytes", "pursued_courses_file_meta",
          "courses_upload"]:
            st.session_state.pop(k, None)
        st.session_state["reset_nonce"] += 1


    # Resolve the original combined key used in the back-compat map, so your
    # concentration dropdown and backend payload keep working exactly the same.
    if selected_catalog_year and selected_degree and selected_major:
        back_map = st.session_state['catalog_menu_options'][selected_catalog_year]  # dict: {degree_major: [concs]}
        # Use the same mapping/heuristics as loader to compare apples-to-apples
        resolver = Catalog_Menu_Options_Loader()
        for dm in back_map.keys():
            if dm in resolver.code_map:
                deg_lvl, maj_name = resolver.code_map[dm]
            else:
                deg_lvl, maj_name = resolver._heuristic_split(dm)
            if deg_lvl == selected_degree and maj_name == selected_major:
                combined_key_for_selection = dm
                break

    # --- Concentration dropdown (depends on combined key) ---
    # --- Concentration dropdown (depends on combined key) ---
    selected_concentration = ""
    if combined_key_for_selection:
        concentration_options = list(
            st.session_state['catalog_menu_options'][selected_catalog_year][combined_key_for_selection]
        )

        selected_concentration = st.selectbox(
            label="Concentration",
            options=concentration_options,
            key="degree_concentration"
        )

        # Reset conversation if concentration changes
        if selected_concentration != st.session_state.get("prev_degree_concentration", ""):
            st.session_state["prev_degree_concentration"] = selected_concentration
            if "messages" in st.session_state:
                st.session_state.messages = []
            # also clear any uploaded/cached courses if conc. changed
            for k in ["pursued_courses", "pursued_courses_preview",
                    "pursued_courses_file_bytes", "pursued_courses_file_meta",
                    "courses_upload"]:
                st.session_state.pop(k, None)
            st.session_state["reset_nonce"] += 1 


    selected_credits = ''
    credits_options = [
        "", "None yet!", "Up to 29 (Freshman)", "30 to 59 (Sophomore)", "60 to 89 (Junior)", "90 to 119 (Senior)", "120 to 149 (5th year)", "150 or more (Super Senior)"
    ]

    if combined_key_for_selection:
        if selected_credits != st.session_state.get("prev_credits", ""):
            st.session_state["prev_credits"] = selected_credits
            if "messages" in st.session_state:
                st.session_state.messages = []

        selected_credits = st.selectbox(
            label="Credits Earned",
            options=credits_options,
            key="credits_earned"
        )

    
    # ========= Upload Pursued Courses (single, robust block) =========
    st.markdown("---")
    st.subheader("📤 Upload Pursued Courses")

    # Template download
    _template_df = pd.DataFrame([
        {"course_code": "ECGR 2111", "course_name": "Circuits I", "grade": "A"},
        {"course_code": "ITSC 2214", "course_name": "Data Structures and Algorithms", "grade": "B+"},
    ])
    _csv_buf = io.StringIO()
    _template_df.to_csv(_csv_buf, index=False)
    st.download_button(
        label="⬇️ Download template (CSV)",
        data=_csv_buf.getvalue(),
        file_name="pursued_courses_template.csv",
        mime="text/csv",
        use_container_width=True,
    )


    # File uploader
    uploaded_file = st.file_uploader(
        "Upload your course history (CSV or Excel)",
        type=["csv", "xlsx", "xls"],
        key=f"courses_upload_{st.session_state['reset_nonce']}",
        help="Include a 'course_code' column (and optional 'course_name').",
    )

    # Controls
    c1, c2 = st.columns(2)
    with c1:
        send_btn = st.button("Upload", type="primary", use_container_width=True, disabled=uploaded_file is None)
    with c2:
        clear_btn = st.button("Clear", use_container_width=True)

    # Clear action
    if clear_btn:
        for k in ["pursued_courses", "pursued_courses_file_bytes", "pursued_courses_preview","courses_upload"]:
            st.session_state.pop(k, None)
        st.session_state["reset_nonce"] += 1
        st.rerun()

    # Read/preview once, store bytes
    if uploaded_file is not None:
        st.caption(f"Selected: **{uploaded_file.name}** ({uploaded_file.size} bytes)")

        # Detect a new selection and (re)cache bytes
        meta = (uploaded_file.name, uploaded_file.size)
        if st.session_state.get("pursued_courses_file_meta") != meta:
            st.session_state["pursued_courses_file_meta"] = meta
            st.session_state["pursued_courses_file_bytes"] = uploaded_file.getvalue()  # read ONCE

        file_bytes = st.session_state.get("pursued_courses_file_bytes", b"")
        if not file_bytes:
            st.error("File appears empty. Please re-upload.")
        else:
            # Try CSV first, then Excel (no re-reading uploaded_file)
            df = None
            try:
                df = pd.read_csv(
                    io.BytesIO(file_bytes),
                    sep=None,
                    engine="python",
                    encoding="utf-8-sig",
                    on_bad_lines="skip",
                    dtype = str

            )
            except Exception:
                df = None
            if df is None or df.empty or len(df.columns) == 0:
                try:
                    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
                except Exception:
                    df = None

            if df is None or df.empty or len(df.columns) == 0:
                st.error("Failed to read file. Ensure it has a header row and is valid CSV/XLSX.")
            else:
                norm_df = _normalize_courses_df(df)
                if norm_df.empty:
                    st.warning("No valid 'course_code' found. Please use the template or include a 'course_code' column.")
                else:
                    # Save detailed + string forms
                    detailed = norm_df.to_dict(orient="records")
                    st.session_state["pursued_courses_detailed"] = detailed

                    pursued_courses_list = [
                        f"{r['course_code']}"
                        + (f" - {r['course_name']}" if r.get('course_name') else "")
                        + (f" (GRADE: {r['grade']})" if r.get('grade') else "")
                        for r in detailed
                    ]
                    st.session_state["pursued_courses"] = pursued_courses_list

                    st.success(f"Loaded {len(pursued_courses_list)} courses from file.")
                    st.dataframe(norm_df.head(20), use_container_width=True)
    

    # ========= Export suggested courses (from last answer) =========
    st.markdown("---")
    st.subheader("📤 Export Suggested Courses")

    export_rows = st.session_state.get("suggested_courses_export", [])
    if export_rows:
        export_df = pd.DataFrame(export_rows)

        # Make sure all three columns exist, even if backend didn't send notes
        for col in ["course_code", "course_name", "notes"]:
            if col not in export_df.columns:
                export_df[col] = ""

        export_df = export_df[["course_code", "course_name", "notes"]]

        csv_bytes = export_df.to_csv(index=False).encode("utf-8")

        st.download_button(
            label="⬇️ Download Courses List",
            data=csv_bytes,
            file_name="suggested_courses.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.caption("Ask a question that produces course recommendations first.")

    # ========= NEW: Export Prompt + Answer + Suggestions (Markdown) =========
    st.markdown("---")
    st.subheader("📤 Export Full Response")

    # Pull from session state (or use fallbacks)
    year_raw = st.session_state.get("catalog_year", "") or "UnknownYear"
    major_raw = st.session_state.get("major", "") or "UnknownMajor"

    # Sanitize for filesystem: replace spaces & weird chars with underscores
    def _safe_part(s: str) -> str:
        s = str(s)
        s = s.strip()
        # Replace anything that's not letter/number/dash with underscore
        return re.sub(r"[^A-Za-z0-9\-]+", "_", s) or "NA"

    year_part = _safe_part(year_raw)
    major_part = _safe_part(major_raw)

    # Build a safe custom filename for the full export
    safe_year = (selected_catalog_year or "UnknownYear")
    safe_major = (selected_major or "UnknownMajor")

    # Replace characters that are annoying in filenames
    for ch in [" ", "/", "\\", ","]:
        safe_year = safe_year.replace(ch, "_")
        safe_major = safe_major.replace(ch, "_")

    export_filename = f"Niner_Pathfinder_Response_{safe_year}_{safe_major}.md"

    export_md = st.session_state.get("export_markdown_chat", "")
    if export_md:
        st.download_button(
            label="⬇️ Download latest response (.md)",
            data=export_md.encode("utf-8"),
            file_name=export_filename,
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        st.caption("Ask a question first to enable this export.")



    # Send to backend (uses the same stored bytes)
    if send_btn:
        file_bytes = st.session_state.get("pursued_courses_file_bytes")
        if not file_bytes:
            st.warning("Please upload a file first.")
        else:
            files = {
                "file": (uploaded_file.name if uploaded_file else "courses_upload", file_bytes, "application/octet-stream")
            }
            try:
                resp = requests.post(f"{API}/upload-courses", files=files, timeout=30)
                if resp.status_code == 200:
                    st.success(f"✅ {resp.json().get('message', '')}")
                else:
                    st.error(f"❌ Do Upload failed: {resp.status_code} – {resp.text}")

            except Exception as e:
                st.error(f"⚠️ Do Upload encountered an error: {e}")
    # ========= End upload block =========



    # Add reset conversation button
    st.markdown("---")
    if st.button("🔄 Reset Conversation", use_container_width=True):
        # Clear chat state
        for k in [
            "messages",
            "selected_message_index",
            "feedback_submitted",
            "show_feedback_form",
        ]:
            st.session_state.pop(k, None)

        # Clear uploaded-courses caches (do NOT assign to widget key)
        for k in [
            "pursued_courses",
            "pursued_courses_preview",
            "pursued_courses_file_bytes",
            "pursued_courses_file_meta",
        ]:
            st.session_state.pop(k, None)

        # Force a new widget instance on next render
        st.session_state["reset_nonce"] += 1

        st.success("Conversation reset successfully!")
        st.rerun()

        

# Main layout - use different approach with columns at the top level
if (selected_catalog_year == "" or not selected_degree or not selected_major):
    st.warning("Please select a catalog year, degree, and major to start.")
else:
    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "selected_message_index" not in st.session_state:
        st.session_state.selected_message_index = None
    if len(st.session_state.messages) == 0:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "Hello! How can I assist you today?"
        })

    # Create two columns for layout
    left_col, right_col = st.columns([6, 4])
    
    # Left column - Chat interface
    with left_col:
        st.subheader("💬 Chat")
        
        # Create a container with fixed height for the chat messages
        chat_container = st.container(height=500)
        with chat_container:
            for i, message in enumerate(st.session_state.messages):
                with st.chat_message(message["role"]):
                    if message["role"] == "assistant" and i > 0:
                        st.markdown(message["content"])
                        if "analytical_summary" in message:
                            if st.button(
                                "🔍 View Details", 
                                key=f"details_{i}",
                                help="Click to view analysis details in the right panel"
                            ):
                                st.session_state.selected_message_index = i
                                st.rerun()
                    else:
                        st.markdown(message["content"])
        
        # Chat input below the scrollable area
        prompt = st.chat_input("Type your prompt")
        

        # Remember the raw combined key (e.g., "MS - Computer Science") for compatibility
        st.session_state['degree_program'] = combined_key_for_selection

        if selected_concentration:
            degree_program = f"{combined_key_for_selection}, {selected_concentration} Concentration"
        else:
            degree_program = combined_key_for_selection

        # Process prompt
        if prompt:
            try:
                prompt_response = requests.post(
                    f"{API}/chat-request",
                    json={
                        "conversation_history": st.session_state.messages,
                        "user_prompt_text": prompt,
                        "student_degree_program": degree_program,
                        "student_catalog_year": selected_catalog_year,
                        "student_credits_earned": selected_credits,
                        "pursued_courses": st.session_state.get("pursued_courses", []),  # keeps backward-compat
                        "pursued_courses_detailed": st.session_state.get("pursued_courses_detailed", []),  # NEW
                    }
                )


                if prompt_response.status_code == 200:
                    response_data = prompt_response.json()

                    assistant_text = response_data["chat_response_content"]

                    # 🔹 NEW: store markdown export for this prompt
                    export_md = response_data.get("export_markdown", "")
                    if export_md:
                        st.session_state["export_markdown_chat"] = export_md


                    # NEW: prefer structured suggestions from backend
                    structured = response_data.get("suggested_courses") or []
                    if structured:
                        st.session_state["suggested_courses_export"] = structured
                    else:
                        # Fallback to text parsing if backend returns nothing
                        detailed = st.session_state.get("pursued_courses_detailed", []) or []
                        passed_codes = {
                            (row.get("course_code", "") or "").strip().upper()
                            for row in detailed
                            if _is_pass_frontend(row.get("grade", ""))
                        }
                        st.session_state["suggested_courses_export"] = extract_suggested_courses_from_text(
                            assistant_text,
                            passed_codes=passed_codes,
                        )


                    # keep the rest of your message appending exactly as before
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": response_data["chat_response_content"],
                        "analytical_summary": response_data["analytical_summary"],
                        "information_requests": response_data["information_requests"],
                        "retrieved_context": response_data["retrieved_context"],
                        "flattened_context": response_data["flattened_context"],
                    })
                    st.rerun()


                else:
                    st.error(f"API Error: {prompt_response.text}")
            except Exception as e:
                st.error(f"Error: {e}")

        # Feedback section
        feedback_reasons = [
                    "Not accurate",
                    "Not enough detail",
                    "Off-topic",
                    "Too vague",
                    "Other"
                ]
        if len(st.session_state.messages) > 1:
            if "feedback_submitted" not in st.session_state:
                st.session_state.feedback_submitted = False
            
            if not st.session_state.feedback_submitted:
                st.write("How was your experience with Niner Pathfinder?")
                col1, col2 = st.columns(2)
                
                with col1:
                    if st.button("👍 Helpful", key="helpful_btn"):
                        st.session_state.feedback_type = "positive"
                        st.session_state.show_feedback_form = True
                        st.rerun()

                with col2:
                    if st.button("👎 Not Helpful", key="not_helpful_btn"):
                        st.session_state.feedback_type = "negative"
                        st.session_state.show_feedback_form = True
                        st.rerun()
            
            if st.session_state.get("show_feedback_form", False) and not st.session_state.feedback_submitted:
                # Show dropdown if negative feedback
                feedback_reason = ""
                if st.session_state.get("feedback_type") == "negative":
                    feedback_reason = st.selectbox(
                        "Why was it not helpful?",
                        [""] + feedback_reasons,
                        key="feedback_reason"
                    )

                # Free-text area (optional for both positive/negative)
                feedback_text = st.text_area("Please share your suggestions:", key="feedback_text")

                if st.button("Submit Feedback"):
                    try:
                        feedback_response = requests.post(
                            f"{API}/submit-feedback",
                            json={
                                "feedback_type": st.session_state.get("feedback_type", ""),
                                "feedback_reason": feedback_reason,  # <-- new field
                                "feedback_text": feedback_text,
                                "student_catalog_year": st.session_state.get("catalog_year", ""),
                                "student_degree_program": st.session_state.get("degree_program", ""),
                                "student_credits_earned": st.session_state.get("credits_earned", ""),
                                "conversation_history": st.session_state.messages
                            }
                        )

                        if feedback_response.status_code == 200:
                            st.success("Thank you for your feedback!")
                            st.session_state.feedback_submitted = True
                            st.session_state.show_feedback_form = False
                            st.rerun()
                        else:
                            st.error(f"Error submitting feedback: {feedback_response.text}")
                    except Exception as e:
                        st.error(f"Connection error: {e}")
    
        with st.expander("🧩 Debug Info (temporary)"):
            st.write("Selected Catalog Year:", selected_catalog_year)
            st.write("Degree:", selected_degree)
            st.write("Major:", selected_major)
            st.write("Concentration:", selected_concentration)
            st.write("Credits Earned:", selected_credits)
            st.write("Pursued Courses:", st.session_state.get("pursued_courses", []))
        
        with st.expander("🧪 Upload Debug (temporary)"):
            st.write("API base:", API)
            st.write("Uploaded file meta:", st.session_state.get("pursued_courses_file_meta"))
            st.write("Bytes cached:", len(st.session_state.get("pursued_courses_file_bytes", b"")))
            st.write("Pursued courses (count):", len(st.session_state.get("pursued_courses", [])))




    # Right column - Response Details (this will stay in view)
    with right_col:
        st.subheader("📋 Response Details")
        
        # Create a container with fixed height for the details
        details_container = st.container(height=500)
        with details_container:
            if st.session_state.get("selected_message_index") is not None:
                selected_msg = st.session_state.messages[st.session_state.selected_message_index]
                
                if (selected_msg["role"] == "assistant" and "analytical_summary" in selected_msg):
                    # Create tabs for different types of information
                    tab1, tab2, tab3 = st.tabs(["📊 Analysis", "🏷️ Tags", "📚 Context"])
                    
                    with tab1:
                        st.write("**Analytical Summary:**")
                        st.write(selected_msg.get("analytical_summary", "No analytical summary available"))
                    
                    with tab2:
                        st.write("**Information Request Tags:**")
                        tags = selected_msg.get("information_requests", "No tags available")
                        st.write(tags)
                    
                    with tab3:
                        st.write("**Retrieved Context:**")
                        retrieved_context = selected_msg.get("retrieved_context", {})
                        
                        if retrieved_context and isinstance(retrieved_context, dict):
                            for context_type, context_data in retrieved_context.items():
                                st.write(f"**{context_type}:**")
                
                                if isinstance(context_data, list):
                                    for item in context_data:
                                        if isinstance(item, dict):
                                            for doc_name, doc_content in item.items():
                                                with st.expander(f"📄 {doc_name}"):
                                                    st.write(doc_content)
                                        else:
                                            st.write(f"- {item}")
                                elif isinstance(context_data, dict):
                                    for doc_name, doc_content in context_data.items():
                                        with st.expander(f"📄 {doc_name}"):
                                            st.write(doc_content)
                                else:
                                    st.write(context_data)
                                
                                st.divider()
                        else:
                            st.write("No retrieved context available")
                        
                        if "flattened_context" in selected_msg:
                            with st.expander("📋 Flattened Context (Raw)"):
                                st.text(selected_msg["flattened_context"])
                else:
                    st.info("Click on a 'View Details' button in the chat to see response analysis here.")
            else:
                st.info("Click on a 'View Details' button in the chat to see response analysis here.")
        
        # Add a clear button below the details
        if st.session_state.get("selected_message_index") is not None:
            if st.button("🗑️ Clear Details", key="clear_details"):
                st.session_state.selected_message_index = None
                st.rerun()