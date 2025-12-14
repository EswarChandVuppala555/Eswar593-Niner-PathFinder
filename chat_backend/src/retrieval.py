# Support functions for storage and retrieval

def vector_query(
    query_vector_embedding,
    db_client,
    db_collection_name,
    limit = 5) -> str:

    query_results = []

    db_collection = db_client.get_collection(name=db_collection_name)

    db_response = db_collection.query(
        query_embeddings = query_vector_embedding,
        n_results = limit,
    )

    for item in db_response['documents'][0]:
        query_results.append(item)

    return query_results


# Extracts the actual vector embedding from the OpenAI response
# https://platform.openai.com/docs/api-reference/embeddings/create
def openai_extract_vector(
        response
    ) -> list[float]:
    return response.data[0].embedding


def load_courses(logger):
    import json
    courses = {}

    for i in range(1, 9):
        file_path = f'rag_corpus/ug_cat/2024-2025/courses/chunk_{i}.json'

        # Read in JSON file to dictionary
        with open(file_path, 'r') as f:
            data = json.load(f)
            # i = 0;
            for course in data:
                # if i % 100 == 0:
                    # print(f"Processing course {i} from {file_path}")
                # Load each course name and content into the courses dictionary
                courses[course['id']] = course['content'],

                # i += 1

        logger.info(f"Loaded {len(data)} courses from {file_path}")
        
    logger.info(f"Total courses loaded: {len(courses)}")

    return courses



import re
from typing import Dict, List, Set

COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,4}\s?\d{4}\b")

def extract_course_codes(text: str) -> List[str]:
    if not text:
        return []
    return [m.group(0).upper().replace("  ", " ").strip() for m in COURSE_CODE_RE.finditer(text)]

def missing_prereqs(prereq_text: str, completed: Set[str]) -> List[str]:
    """
    Heuristic: extract all course-like codes in the prereq text and
    mark any not in 'completed' as missing.

    NOTE: This treats complex logic (A and (B or C)) as a simple set.
    It's conservative: if any referenced code isn't completed, it's 'missing'.
    That’s usually fine for advising, but you can upgrade later to parse boolean logic.
    """
    codes = set(extract_course_codes(prereq_text))
    return sorted([c for c in codes if c not in completed])

def rerank_and_filter_candidates(candidates: List[Dict], completed: Set[str]) -> Dict[str, List[Dict]]:
    """
    Split retrieved courses into:
      - 'eligible': prereqs all satisfied (slight score boost)
      - 'needs_prereqs': missing prereq codes listed in 'missing_prereqs'
    Skips already-completed or malformed rows.
    Expected candidate keys:
      - 'course_code' (e.g., 'ITSC 4220')
      - 'title'       (optional)
      - 'prerequisites' or 'prereq' (optional text)
      - 'score'       (optional float)
    """
    good, blocked = [], []
    for c in candidates:
        code = (c.get("course_code") or "").upper().strip()
        if not code:
            continue
        if code in completed:
            # Already done by student; exclude from suggestions
            continue

        prereq_text = c.get("prerequisites", "") or c.get("prereq", "") or ""
        missing = missing_prereqs(prereq_text, completed)

        c = dict(c)  # shallow copy so we can annotate
        c["missing_prereqs"] = missing

        if missing:
            blocked.append(c)
        else:
            # small, safe boost if fully eligible
            base = c.get("score") or 0.0
            c["score"] = base + 0.1
            good.append(c)

    # Sort both buckets by score (desc) if present
    good.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    blocked.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return {"eligible": good, "needs_prereqs": blocked}
