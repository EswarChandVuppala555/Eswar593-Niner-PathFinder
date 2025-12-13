# src/retrieval.py

import os
import json
import glob
import re
from typing import List, Dict, Any

# --------------------------------------------------------------------
# Vector search helpers
# --------------------------------------------------------------------

def vector_query(
    query_vector_embedding,
    db_client,
    db_collection_name,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Thin wrapper around Chroma (or similar) vector DB query.

    Returns whatever the DB returns (usually a list of documents/records).
    """
    query_results: List[Any] = []

    db_collection = db_client.get_collection(name=db_collection_name)

    db_response = db_collection.query(
        query_embeddings=query_vector_embedding,
        n_results=limit,
    )

    # Many Chroma setups return: {"documents": [[doc1, doc2, ...]], "metadatas": [[...]], ...}
    docs = db_response.get("documents") or []
    if docs and isinstance(docs[0], list):
        for item in docs[0]:
            query_results.append(item)
    else:
        # Fallback if the shape is different
        for item in docs:
            query_results.append(item)

    return query_results


def openai_extract_vector(response) -> List[float]:
    """
    Extracts the vector embedding from an OpenAI embeddings response.
    https://platform.openai.com/docs/api-reference/embeddings/create
    """
    return response.data[0].embedding


# --------------------------------------------------------------------
# Catalog normalization helpers
# --------------------------------------------------------------------

def _extract_code_and_title_from_raw(raw: str) -> (str, str):
    """
    Best-effort parse for lines like:

        "ENGR 1202 Intro to Engineering II | 2 | C | X | MATH 1241, ENGR 1201 |"

    Strategy:
    - Find first pattern of 2–4 letters + 3–4 digits (course code).
    - Course title is the text immediately after that, up to the next '|'.
    """
    if not raw:
        return "", ""

    text = raw.strip()
    # Find something like ENGR 1202 or MATH1241
    m = re.search(r"\b([A-Z]{2,4}\s*\d{3,4})\b", text)
    if not m:
        return "", ""

    code = m.group(1).strip().upper()
    tail = text[m.end():]  # everything after the code
    # Stop at first '|' if present
    if "|" in tail:
        tail = tail.split("|", 1)[0]
    title = tail.strip()
    return code, title


def normalize_catalog_row(
    meta: Dict[str, Any],
    catalog_year: str,
    raw_content: str = "",
) -> Dict[str, Any]:
    """
    Normalize a single catalog row into:

        {
            "course_code": "ENGR 1202",
            "course_name": "Intro to Engineering II",
            "catalog_year": "2023-2024",
            "prerequisites": ["MATH 1241", "ENGR 1201"],
        }

    - meta: metadata dict from the JSON (may already contain course_code, etc.)
    - catalog_year: derived from folder name (e.g. "2023-2024")
    - raw_content: full text content if we need to parse code/title from it
    """
    meta = meta or {}
    out: Dict[str, Any] = {}

    # 1) Course code
    code = (meta.get("course_code") or meta.get("code") or "").strip()
    name = (meta.get("course_name") or meta.get("title") or "").strip()

    if not code and raw_content:
        parsed_code, parsed_title = _extract_code_and_title_from_raw(raw_content)
        if parsed_code:
            code = parsed_code
        if parsed_title and not name:
            name = parsed_title

    if code:
        out["course_code"] = code.upper()

    # 2) Course name
    if name:
        out["course_name"] = name

    # 3) Catalog year
    out["catalog_year"] = str(catalog_year)

    # 4) Prerequisites → list of codes
    prereq_raw = meta.get("prerequisites") or meta.get("prereqs") or ""
    prereq_list: List[str] = []

    if isinstance(prereq_raw, str):
        pieces = [p.strip() for p in prereq_raw.split(",") if p.strip()]
    elif isinstance(prereq_raw, (list, tuple, set)):
        pieces = [str(p).strip() for p in prereq_raw if str(p).strip()]
    else:
        pieces = []

    for p in pieces:
        up = p.upper()
        # Try to extract a clean course code from things like "MATH 1241 (C or better)"
        m = re.search(r"([A-Z]{2,4}\s*\d{3,4})", up)
        if m:
            prereq_list.append(m.group(1).strip())
        else:
            # As a fallback, keep the whole token
            prereq_list.append(up)

    out["prerequisites"] = prereq_list
    return out


def load_courses(logger) -> Dict[str, Dict[str, Any]]:
    """
    Load ALL undergrad catalog course JSON files under:

        rag_corpus/ug_cat/<catalog_year>/courses/*.json

    Returns a dict keyed by COURSE_CODE (upper-case), value is the normalized row.
    """
    base_dir = os.path.join("rag_corpus", "ug_cat")
    courses: Dict[str, Dict[str, Any]] = {}

    if not os.path.isdir(base_dir):
        logger.warning(f"load_courses: base directory not found: {base_dir}")
        return courses

    total_count = 0

    # Each subfolder name is a catalog year: 2021-2022, 2022-2023, ...
    for year_name in sorted(os.listdir(base_dir)):
        year_path = os.path.join(base_dir, year_name)
        if not os.path.isdir(year_path):
            continue

        catalog_year = year_name  # e.g. "2023-2024"
        courses_dir = os.path.join(year_path, "courses")
        if not os.path.isdir(courses_dir):
            logger.info(f"load_courses: no 'courses' dir for {catalog_year} at {courses_dir}")
            continue

        pattern = os.path.join(courses_dir, "*.json")
        json_files = sorted(glob.glob(pattern))
        if not json_files:
            logger.info(f"load_courses: no JSON files found for {catalog_year} at {courses_dir}")
            continue

        for file_path in json_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.exception(f"load_courses: failed to read {file_path}: {e}")
                continue

            file_count = 0
            for course in data:
                if not isinstance(course, dict):
                    continue

                content = course.get("content", "")
                meta = course.get("metadata") or {}

                row = normalize_catalog_row(
                    meta=meta,
                    catalog_year=catalog_year,
                    raw_content=content,
                )

                code = row.get("course_code")
                if not code:
                    # Without a code we can't really use it for prereq filtering, skip.
                    continue

                courses[code] = row
                file_count += 1
                total_count += 1

            logger.info(
                f"Loaded {file_count} courses from {file_path} for catalog year {catalog_year}"
            )

    logger.info(f"Total courses loaded across all years: {total_count}")
    logger.info(f"Unique course codes in catalog: {len(courses)}")
    return courses


# --------------------------------------------------------------------
# Simple helpers you already had
# --------------------------------------------------------------------

def extract_course_codes(pursued_courses: List[str]) -> List[str]:
    """
    Example: parse 'ITSC 2214 - Data Structures and Algorithms' → 'ITSC 2214'
    """
    codes: List[str] = []
    for c in pursued_courses or []:
        parts = str(c).split(" - ", 1)
        codes.append(parts[0].strip() if parts else str(c).strip())
    return codes


def missing_prereqs(required: List[str], completed: List[str]) -> List[str]:
    """
    Compare a required prereq list with a completed-course list.
    """
    completed_set = {s.upper() for s in (completed or [])}
    return [r for r in (required or []) if r.upper() not in completed_set]


def rerank_and_filter_candidates(
    retrieved_chunks: List[Dict[str, Any]],
    pursued_course_codes: List[str] = None,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Simple placeholder: boosts chunks that mention any pursued course code
    in text or metadata, then returns top_k.
    """
    pursued_course_codes = pursued_course_codes or []
    boosted: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []

    for ch in retrieved_chunks or []:
        meta = ch.get("metadata") or {}
        text = (ch.get("text") or "") + " " + " ".join(str(v) for v in meta.values())
        if any(code in text for code in pursued_course_codes):
            boosted.append(ch)
        else:
            rest.append(ch)

    ordered = boosted + rest
    return ordered[:top_k]
