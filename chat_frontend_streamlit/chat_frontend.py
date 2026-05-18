import os
import requests
import streamlit as st
from src.menu_options import Catalog_Menu_Options_Loader
import logging
import pandas as pd
import io
import re
import time
from datetime import datetime
from html import escape



# --- Grade helpers used on the frontend for export filtering ---
PASSING_GRADES = {"A", "B", "C", "D", "P", "S", "H"}  # UNC-Charlotte style

def _is_pass_frontend(grade: str) -> bool:
    """Return True if the grade counts as passed (for export filtering)."""
    if not grade:
        # If we don't know, treat as passed so we don't nag them again
        return True
    g = re.sub(r"[+-]$", "", grade.strip().upper())
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

st.set_page_config(page_title="Student Chat", page_icon="💬", layout="wide")
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

COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6}\s*\d{3,4}[A-Z]?)\b", re.I)

def apply_named_export_filename(default_name: str, input_key: str, target_key: str, status_key: str):
    temp = (st.session_state.get(input_key) or "").strip()

    new_name = temp or default_name

    # infer extension from default name
    if "." in default_name:
        ext = "." + default_name.split(".")[-1]
        if not new_name.lower().endswith(ext.lower()):
            new_name += ext

    new_name = re.sub(r'[\\/:*?"<>|]+', "_", new_name)

    st.session_state[target_key] = new_name
    st.session_state[status_key] = time.time()
    
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
        r"\b([A-Z]{2,6}\s?\d{3,4}[A-Z]?)\b(?:\s*\(([^)]+)\))?"
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

def format_suggested_courses_for_display(rows):
    """
    Add UI polish to suggested course rows:
    - icon based on note type
    - short status label
    - tooltip-friendly notes
    """
    formatted = []

    for r in rows or []:
        code = str(r.get("course_code", "")).strip()
        name = str(r.get("course_name", "")).strip()
        notes = str(r.get("notes", "")).strip()

        note_lower = notes.lower()

        if "retake" in note_lower:
            icon = "🔁"
            status = "Retake"
        elif "missing prerequisite" in note_lower or "missing prerequisite(s)" in note_lower:
            icon = "⚠️"
            status = "Near Eligible"
        elif "eligible" in note_lower:
            icon = "✅"
            status = "Eligible"
        else:
            icon = "📘"
            status = "Suggested"

        formatted.append({
            "Status": f"{icon} {status}",
            "Course Code": code,
            "Course Name": name,
            "Notes": notes,
        })

    return formatted

def apply_export_filename(default_name: str, input_key: str, target_key: str):
    temp = (st.session_state.get(input_key) or "").strip()

    new_name = temp or default_name
    if not new_name.lower().endswith(".md"):
        new_name += ".md"
    new_name = re.sub(r'[\\/:*?"<>|]+', "_", new_name)

    st.session_state[target_key] = new_name
    st.session_state["filename_status_time"] = time.time()





def _courses_to_csv_bytes(detailed_rows: list[dict]) -> bytes:
    if not detailed_rows:
        return b""
    df = pd.DataFrame(detailed_rows)
    # Ensure consistent columns
    for col in ["course_code", "course_name", "grade"]:
        if col not in df.columns:
            df[col] = ""
    df = df[["course_code", "course_name", "grade"]]
    return df.to_csv(index=False).encode("utf-8")

def _suggestion_ui_meta(notes: str) -> tuple[str, str, str]:
    """
    Return:
    - icon
    - short status label
    - tooltip text
    """
    n = (notes or "").strip().lower()

    if "retake" in n:
        return (
            "🔁",
            "Retake",
            "You previously attempted this course or did not complete it successfully. Retaking it is recommended."
        )

    if "missing prerequisite" in n or "missing prereq" in n:
        return (
            "⚠️",
            "Near eligible",
            "You are close to being eligible, but still missing one or more prerequisites listed in Notes."
        )

    return (
        "✅",
        "Eligible",
        "All listed prerequisite course requirements appear satisfied, so this course is eligible now."
    )

def render_suggested_courses_snapshot(rows: list[dict]):
    """
    Render a clean suggested-courses table using Streamlit-native display.
    """
    if not rows:
        st.info("No structured suggested courses available yet.")
        return

    display_rows = []
    for r in rows:
        code = str(r.get("course_code", "") or "—")
        name = str(r.get("course_name", "") or "—")
        notes = str(r.get("notes", "") or "—")

        notes_lower = notes.lower()
        if "retake" in notes_lower:
            status = "🔁 Retake"
        elif "missing prerequisite" in notes_lower or "missing prereq" in notes_lower:
            status = "⚠️ Near eligible"
        else:
            status = "✅ Eligible"

        display_rows.append({
            "Status": status,
            "Course Code": code,
            "Course Name": name,
            "Notes": notes,
        })

    df = pd.DataFrame(display_rows)

    st.caption("Status meanings: 🔁 Retake · ✅ Eligible now · ⚠️ Missing prerequisite(s)")
    st.dataframe(df, use_container_width=True, hide_index=True)


def build_safe_export_filename(
    prefix: str,
    selected_catalog_year: str = "",
    selected_degree: str = "",
    selected_major: str = "",
    selected_concentration: str = "",
    selected_credits: str = "",
    ext: str = "md",
) -> str:
    """
    Build a safe, descriptive filename:
    Prefix + Year + Degree + Major + (Concentration) + (Credits) + .ext
    Sanitizes to avoid filesystem-illegal characters.
    """
    def _safe(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        # Replace anything not alnum or dash with underscore
        s = re.sub(r"[^A-Za-z0-9\-]+", "_", s)
        # Collapse multiple underscores
        s = re.sub(r"_+", "_", s).strip("_")
        return s

    parts = [
        _safe(prefix),
        _safe(selected_catalog_year),
        _safe(selected_degree),
        _safe(selected_major),
    ]

    # concentration is optional
    conc = _safe(selected_concentration)
    if conc:
        parts.append(f"Conc_{conc}")

    # credits optional
    cred = _safe(selected_credits)
    if cred:
        parts.append(f"Credits_{cred}")

    # remove empties
    parts = [p for p in parts if p]

    # default fallback
    base = "_".join(parts) if parts else _safe(prefix) or "export"

    ext = (ext or "md").lstrip(".")
    return f"{base}.{ext}"

def build_full_chat_markdown(
    messages: list[dict],
    selected_catalog_year: str = "",
    selected_degree: str = "",
    selected_major: str = "",
    selected_concentration: str = "",
    selected_credits: str = "",
    combined_key_for_selection: str = "",
    degree_program: str = "",
) -> str:
    """
    Build a single markdown file containing:
    1) Sidebar selections (catalog year, degree, major, concentration, credits, degree_program)
    2) Entire chat transcript

    Expects Streamlit messages: [{"role":"user"/"assistant", "content":"..."}]
    """
    lines: list[str] = ["# Niner Pathfinder – Full Chat Export", ""]

    # --- Sidebar / student selections ---
    lines += [
        "## Student Selections",
        "",
        f"- Catalog Academic Year: {selected_catalog_year or '—'}",
        f"- Degree: {selected_degree or '—'}",
        f"- Major: {selected_major or '—'}",
        f"- Concentration: {selected_concentration or '—'}",
        f"- Credits Earned: {selected_credits or '—'}",
        f"- Degree Program Key (backend): {combined_key_for_selection or '—'}",
        f"- Degree Program (sent to backend): {degree_program or '—'}",
        "",
        "---",
        "",
        "## Chat Transcript",
        "",
    ]

    if not messages:
        lines.append("_No messages in this session._")
        return "\n".join(lines).strip() + "\n"

    for i, m in enumerate(messages, start=1):
        role = (m.get("role") or "").strip().lower()
        content = (m.get("content") or "").strip()
        if not content:
            continue

        if role == "user":
            lines.append(f"### User ({i})")
        elif role == "assistant":
            lines.append(f"### Assistant ({i})")
        else:
            lines.append(f"### {role.title()} ({i})")

        lines.append("")
        lines.append(content)
        lines.append("")

    return "\n".join(lines).strip() + "\n"




# Initialize menu options
if not st.session_state.get('catalog_menu_options'):
    menu_options = Catalog_Menu_Options_Loader()
    st.session_state['catalog_menu_options'] = menu_options.year_degree_major_conc_options  # old structure
    st.session_state['catalog_menu_options_tree'] = menu_options.year_degree_major_conc_tree  # new tree
    logger.info("Catalog menu options initialized in session state.")
    logger.info(f"Loaded menu options: {st.session_state['catalog_menu_options']}")


def reset_conversation(clear_uploads: bool = False):
    # Reset chat state
    st.session_state.messages = []
    st.session_state.selected_message_index = None

     # Always clear export/suggestion state
    for k in [
        "export_markdown_chat",
        "suggested_courses_export",
        "courses_export_filename",
        "export_filename",
        "fullchat_export_filename",
        "export_choice_radio",
        "latest_export_filename_input",
        "courses_export_filename_input",
        "fullchat_export_filename_input",
    ]:
        st.session_state.pop(k, None)

    # (Optional) clear upload-related state
    if clear_uploads:
        for k in [
            "pursued_courses",
            "pursued_courses_detailed",
            "pursued_courses_preview",
            "pursued_courses_file_bytes",
            "pursued_courses_file_meta",
            "courses_upload",
        ]:
            st.session_state.pop(k, None)

    # Ensure a rerun-safe nonce exists
    if "reset_nonce" not in st.session_state:
        st.session_state["reset_nonce"] = 0
    st.session_state["reset_nonce"] += 1

# Sidebar for collecting key student information
with st.sidebar:
    tab_info, tab_upload = st.tabs(["🧑‍🎓 Status", "📤 Upload"])
    with tab_info:
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
            for k in [
                "pursued_courses",
                "pursued_courses_detailed",
                "pursued_courses_preview",
                "pursued_courses_file_bytes",
                "pursued_courses_file_meta",
                "courses_upload",
                "export_markdown_chat",
                "suggested_courses_export",
                "courses_export_filename",
                "export_filename",
                "fullchat_export_filename",
                "export_choice_radio",
            ]:
                st.session_state.pop(k, None)
            
            st.session_state.pop("export_filename_input", None)
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
            for k in [
                "pursued_courses",
                "pursued_courses_detailed",
                "pursued_courses_preview",
                "pursued_courses_file_bytes",
                "pursued_courses_file_meta",
                "courses_upload",
                "export_markdown_chat",
                "suggested_courses_export",
                "courses_export_filename",
                "export_filename",
                "fullchat_export_filename",
                "export_choice_radio",
            ]:
                st.session_state.pop(k, None)
            st.session_state.pop("export_filename_input", None)
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
                for k in [
                    "pursued_courses",
                    "pursued_courses_detailed",
                    "pursued_courses_preview",
                    "pursued_courses_file_bytes",
                    "pursued_courses_file_meta",
                    "courses_upload",
                    "export_markdown_chat",
                    "suggested_courses_export",
                    "courses_export_filename",
                    "export_filename",
                    "fullchat_export_filename",
                    "export_choice_radio",
                ]:
                    st.session_state.pop(k, None)
                st.session_state.pop("export_filename_input", None) 
                st.session_state["reset_nonce"] += 1 

        selected_credits = None
        if combined_key_for_selection:
            credits_options = [
            "", "None yet!", "Up to 29 (Freshman)", "30 to 59 (Sophomore)", "60 to 89 (Junior)", "90 to 119 (Senior)", "120 to 149 (5th year)", "150 or more (Super Senior)"
        ]
            selected_credits = st.selectbox("Credits Earned", credits_options, key="credits")

            prev_credits = st.session_state.get("prev_credits", None)

            # Run reset only when credits actually changed
            if prev_credits is not None and selected_credits != prev_credits:
                # reset chat/messages/selected_message_index
                reset_conversation()

            st.session_state.prev_credits = selected_credits

    with tab_upload:
        # ========= Upload Pursued Courses (single, robust block) =========
        st.markdown("---")
        st.subheader("📤 Upload Pursued Courses")

        @st.dialog("Uploaded Courses")
        def show_uploaded_courses_dialog():
            rows = st.session_state.get("pursued_courses_detailed", []) or []
            if not rows:
                st.info("No courses loaded yet. Upload a file first.")
                return

            df = pd.DataFrame(rows)
            for col in ["course_code", "course_name", "grade"]:
                if col not in df.columns:
                    df[col] = ""
            df = df[["course_code", "course_name", "grade"]]

            st.caption(f"Total courses loaded: **{len(df)}**")

            search_term = st.text_input(
                "Search courses",
                placeholder="Type course code, course name, or grade",
                key="uploaded_courses_search"
            ).strip()

            if search_term:
                q = search_term.lower()
                filtered_df = df[
                    df["course_code"].astype(str).str.lower().str.contains(q, na=False) |
                    df["course_name"].astype(str).str.lower().str.contains(q, na=False) |
                    df["grade"].astype(str).str.lower().str.contains(q, na=False)
                ]
            else:
                filtered_df = df

            filtered_df = filtered_df.sort_values(by=["course_code", "course_name"])
            st.caption(f"Showing {len(filtered_df)} of {len(df)} courses")

            st.dataframe(filtered_df, use_container_width=True, height=420)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            file_name_pc = re.sub(r"[^\w\-\.]", "_", f"Pursued_Courses_{selected_catalog_year}_{selected_major}_{timestamp}.csv")

            st.download_button(
                "⬇️ Download courses (CSV)",
                data=_courses_to_csv_bytes(rows),
                file_name = file_name_pc,
                mime="text/csv",
                use_container_width=True,
            )

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
        c1, c2, c3 = st.columns([2.3, 2, 3], gap="small")

        with c1:
            send_btn = st.button("Upload", type="primary", use_container_width=True, disabled=uploaded_file is None)

        with c2:
            clear_btn = st.button("Clear", use_container_width=True)

        with c3:
            loaded_count = len(st.session_state.get("pursued_courses_detailed", []) or [])
            view_disabled = (loaded_count == 0)

            if st.button(
                f"🔎 View courses ({loaded_count})",
                use_container_width=True,
                disabled=view_disabled,
            ):
                show_uploaded_courses_dialog()

        # Clear action
        if clear_btn:
            for k in [
                "pursued_courses",
                "pursued_courses_detailed",
                "pursued_courses_preview",
                "pursued_courses_file_bytes",
                "pursued_courses_file_meta",
                "courses_upload",
                "export_markdown_chat",
                "suggested_courses_export",
                "courses_export_filename",
                "export_filename",
                "fullchat_export_filename",
                "export_choice_radio",
            ]:
                st.session_state.pop(k, None)
            st.session_state.pop("export_filename_input", None)
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
                        st.caption("Use **View courses** to see the full list.")
        
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
    chat_col, details_col = st.columns([6, 4])
    
    # Left column - Chat interface
    with chat_col:
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
                        "student_credits_earned": st.session_state.get("credits", ""),
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
                                "student_credits_earned": st.session_state.get("credits", "") or selected_credits,
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


        # ===== Suggested Courses UI Snapshot =====
        suggested_rows = st.session_state.get("suggested_courses_export", [])
        if suggested_rows:
            st.markdown("---")
            with st.expander("🧭 Suggested Courses Snapshot", expanded=True):
                render_suggested_courses_snapshot(suggested_rows)

        # ===== Compact Export Section =====
        export_md = st.session_state.get("export_markdown_chat", "")
        has_response = bool(export_md)

        if has_response:
            st.markdown("---")

            # ---------- Build export payloads ----------
            export_rows = st.session_state.get("suggested_courses_export", [])
            export_df = pd.DataFrame(export_rows) if export_rows else pd.DataFrame()

            if not export_df.empty:
                for col in ["course_code", "course_name", "notes"]:
                    if col not in export_df.columns:
                        export_df[col] = ""
                export_df = export_df[["course_code", "course_name", "notes"]]
                courses_csv_bytes = export_df.to_csv(index=False).encode("utf-8")
            else:
                courses_csv_bytes = b""

            degree_program_for_export = ""
            if combined_key_for_selection:
                if selected_concentration:
                    degree_program_for_export = f"{combined_key_for_selection}, {selected_concentration} Concentration"
                else:
                    degree_program_for_export = combined_key_for_selection

            full_chat_md = build_full_chat_markdown(
                st.session_state.get("messages", []),
                selected_catalog_year=selected_catalog_year,
                selected_degree=selected_degree,
                selected_major=selected_major,
                selected_concentration=selected_concentration,
                selected_credits=st.session_state.get("credits", ""),
                combined_key_for_selection=combined_key_for_selection,
                degree_program=degree_program_for_export,
            )

            # ---------- Default filenames ----------
            default_courses_filename = re.sub(
                r"[^\w\-\.]",
                "_",
                f"Suggested_Courses_{selected_catalog_year}_{selected_major}.csv"
            )

            default_latest_filename = st.session_state.get(
                "export_filename", "niner_pathfinder_export.md"
            )

            default_fullchat_filename = build_safe_export_filename(
                prefix="Niner_Pathfinder_FullChat",
                selected_catalog_year=selected_catalog_year or "UnknownYear",
                selected_degree=selected_degree or "UnknownDegree",
                selected_major=selected_major or "UnknownMajor",
                selected_concentration=selected_concentration or "",
                ext="md",
            )

            # ---------- Session defaults ----------
            if "courses_export_filename" not in st.session_state:
                st.session_state["courses_export_filename"] = default_courses_filename

            if "export_filename" not in st.session_state:
                st.session_state["export_filename"] = default_latest_filename

            if "fullchat_export_filename" not in st.session_state:
                st.session_state["fullchat_export_filename"] = default_fullchat_filename

            # ---------- Export selector ----------
            with st.expander("⬇️ Export Options", expanded=True):
                export_choice = st.radio(
                    "Choose what to export:",
                    [
                        "Download Course List",
                        "Download Latest Response",
                        "Download Full Chat",
                    ],
                    key="export_choice_radio",
                )
                preview_map = {
                    "Download Course List": "📄 Course List (CSV)",
                    "Download Latest Response": "📝 Latest Response (.md)",
                    "Download Full Chat": "📚 Full Chat (.md)",
                }

                st.caption(f"Currently selected: {preview_map.get(export_choice, export_choice)}")

                # ---------- Course List ----------
                if export_choice == "Download Course List":
                    st.markdown("**Filename Options**")
                    st.text_input(
                        "Filename",
                        value=st.session_state["courses_export_filename"],
                        key="courses_export_filename_input",
                        on_change=apply_named_export_filename,
                        args=(
                            default_courses_filename,
                            "courses_export_filename_input",
                            "courses_export_filename",
                            "courses_filename_status_time",
                        ),
                    )

                    if st.button("Apply", key="apply_courses_export_filename", use_container_width=True):
                        apply_named_export_filename(
                            default_courses_filename,
                            "courses_export_filename_input",
                            "courses_export_filename",
                            "courses_filename_status_time",
                        )

                    status_time = st.session_state.get("courses_filename_status_time")
                    if status_time and time.time() - status_time < 2:
                        st.success("Filename updated ✓")
                    elif status_time:
                        st.session_state["courses_filename_status_time"] = None

                    st.download_button(
                        label="⬇️ Download Course List",
                        data=courses_csv_bytes,
                        file_name=st.session_state["courses_export_filename"],
                        mime="text/csv",
                        use_container_width=True,
                        disabled=not bool(courses_csv_bytes),
                        key="download_courses_compact",
                    )

                # ---------- Latest Response ----------
                elif export_choice == "Download Latest Response":
                    st.markdown("**Filename Options**")
                    st.text_input(
                        "Filename",
                        value=st.session_state["export_filename"],
                        key="latest_export_filename_input",
                        on_change=apply_named_export_filename,
                        args=("niner_pathfinder_export.md","latest_export_filename_input","export_filename","latest_filename_status_time",),
                    )

                    if st.button("Apply", key="apply_latest_export_filename", use_container_width=True):
                        apply_named_export_filename(
                            "niner_pathfinder_export.md",
                            "latest_export_filename_input",
                            "export_filename",
                            "latest_filename_status_time",
                        )

                    status_time = st.session_state.get("latest_filename_status_time")
                    if status_time and time.time() - status_time < 2:
                        st.success("Filename updated ✓")
                    elif status_time:
                        st.session_state["latest_filename_status_time"] = None

                    st.download_button(
                        label="⬇️ Download Latest Response (.md)",
                        data=export_md.encode("utf-8"),
                        file_name=st.session_state["export_filename"],
                        mime="text/markdown",
                        use_container_width=True,
                        key="download_latest_compact",
                    )

                # ---------- Full Chat ----------
                elif export_choice == "Download Full Chat":
                    st.markdown("**Filename Options**")
                    st.text_input(
                        "Filename",
                        value=st.session_state["fullchat_export_filename"],
                        key="fullchat_export_filename_input",
                        on_change=apply_named_export_filename,
                        args=(default_fullchat_filename, "fullchat_export_filename_input", "fullchat_export_filename","fullchat_filename_status_time",),
                    )

                    if st.button("Apply", key="apply_fullchat_export_filename", use_container_width=True):
                        apply_named_export_filename(
                            default_fullchat_filename,
                            "fullchat_export_filename_input",
                            "fullchat_export_filename",
                            "fullchat_filename_status_time",
                        )

                    status_time = st.session_state.get("fullchat_filename_status_time")
                    if status_time and time.time() - status_time < 2:
                        st.success("Filename updated ✓")
                    elif status_time:
                        st.session_state["fullchat_filename_status_time"] = None

                    st.download_button(
                        label="⬇️ Download Full Chat (.md)",
                        data=full_chat_md.encode("utf-8"),
                        file_name=st.session_state["fullchat_export_filename"],
                        mime="text/markdown",
                        use_container_width=True,
                        disabled=not bool(full_chat_md),
                        key="download_fullchat_compact",
                    )

            st.markdown("---")
            if st.button("🔄 Reset Conversation", use_container_width=True, key="reset_conversation_compact"):
                for k in [
                    "messages",
                    "selected_message_index",
                    "feedback_submitted",
                    "show_feedback_form",
                ]:
                    st.session_state.pop(k, None)

                for k in [
                    "pursued_courses",
                    "pursued_courses_detailed",
                    "pursued_courses_preview",
                    "pursued_courses_file_bytes",
                    "pursued_courses_file_meta",
                ]:
                    st.session_state.pop(k, None)

                for k in [
                    "export_filename_input",
                    "latest_export_filename_input",
                    "courses_export_filename_input",
                    "fullchat_export_filename_input",
                    "courses_export_filename",
                    "export_filename",
                    "fullchat_export_filename",
                    "export_choice_radio",
                ]:
                    st.session_state.pop(k, None)

                st.session_state["reset_nonce"] += 1
                st.success("Conversation reset successfully!")
                st.rerun()                                       
                 
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
    with details_col:
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


