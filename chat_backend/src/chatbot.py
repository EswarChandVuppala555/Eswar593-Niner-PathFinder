<<<<<<< HEAD
import os
import time
import re
from typing import List, Dict, Set, Any

from pydantic import BaseModel
from pydantic import Field

from src.retrieval import openai_extract_vector


from src.retrieval import vector_query, load_courses
from src.retrieval import rerank_and_filter_candidates  # currently unused, but keep for future work


# TODO differentiate information requests based on the storage retrieval mode
system_prompt_planning_common_component = """
# Your Role
You are a prompt analyst for a chatbot system that provides information about academic programs and courses and helps students navigate their academic journey.
You do not respond directly to user prompts.
Instead, you analyze user prompts to summarize and clarify the intent behind user prompts, then determine what information (if any) will be requested.

# Your Response Format
Your entire response MUST be contained within two XML sections:

<Analytical_Summary>...</Analytical_Summary>
<Retrieval>...</Retrieval>

## Analytical Summary of Prompt:
Summarize the intent of the user prompt.

IMPORTANT OUTPUT RULE (STRICT):

- Your response MUST contain ONLY the following two XML blocks, in this exact order:
  1) <Analytical_Summary>...</Analytical_Summary>
  2) <Retrieval>...</Retrieval>

- Do NOT include any text before or after these tags.
- Do NOT add explanations, markdown, or prose outside the XML.
- If no retrieval is required, output empty tags:
  <Retrieval></Retrieval>

Any response that violates this format is INVALID.
"""


system_prompt_planning_retrieval_mode_component: Dict[str, str] = {}
planning_response_validation_pattern: Dict[str, str] = {}

# System prompt for most advanced storage and retrieval mode 
system_prompt_planning_retrieval_mode_component["0"] = """
## Information Retrieval Requests
Between <Retrieval> and </Retrieval> tags, submit up to three pairs of the following tags to request additional information from internal repositories.  Don't request information unless it will be useful to generate a better response.  

<Specific_Request_Current_Major> Request information about the student's current degree program, including requirements, courses, and other relevant details, for the student's catalog year. Place no characters between these tags. </Specific_Request_Current_Major>
<Specific_Request_Current_Major_Sample_Schedules> Request example schedules for the student's major and catalog year.  Request this when the user prompt is about scheduling courses or planning a semester or to understand how the program usually flows. Place no characters between these tags.  </Specific_Request_Current_Major_Sample_Schedules>
<Semantic_Request_Programs> Semantic search for degree programs related to the request, such as majors and concentrations, minors, and early graduate programs. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags.  </Semantic_Request_Programs>
<Semantic_Request_Courses> - Semantic search for courses related to user prompt; includes course names, codes, descriptions, and prerequesites and corequisites. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags. </Semantic_Request_Courses>
<Semantic_Request_Support_Resources> - Semantic search for support resources related to user prompt, such as advising, tutoring, career services, and mental health resources. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags. </Semantic_Request_Support_Resources>

Each XML tag that is used must be reproduced verbatim, and the text between the tags must be replaced as directed.

"""


planning_response_validation_pattern["0"] = (
    r"^\s*<Analytical_Summary>.*?</Analytical_Summary>\s*<Retrieval>.*?</Retrieval>\s*$"
)


system_prompt_generation_base = """# Your Role
You generate responses as part of a chatbot system that provides information to undergraduate university students about academic programs and courses and helps those students navigate their academic journey.

# Your Response Style and Priorities
Your response should be informative, friendly, helpful, and concise yet thorough using good word economy.
If the user does not provide enough information to provide an accurate, relevant, and complete response, you ask follow-up questions to clarify their request before answering questions.
Unless the question is very simple and the response can be found in the provided context, encourage the student to speak with their advisor to help ensure their academic success.  
You never provide specific facts about a UNC Charlotte degree program, course, or university policy unless the information is provided in the provided contextual information.

# Prerequisite and Corequisite Rules (IMPORTANT – FOLLOW STRICTLY)

You must apply these rules whenever referring to prerequisites and course eligibility:

1. Prerequisites:
   - A prerequisite must be fully completed and passed BEFORE taking the next course.
   - A student may NOT take a course in the same semester as its prerequisite.
   - Do NOT suggest or imply that a student can take a course “along with” or “in parallel with” its prerequisite.
   - If a course has an unmet prerequisite, label it as NOT eligible and do not recommend it.

2. Corequisites:
   - A corequisite may be taken in the SAME semester as its partner course.
   - You may say “take alongside _____” only when the catalog explicitly states the requirement is a corequisite.

3. If the catalog context does not clearly specify a corequisite:
   - Assume it is a prerequisite only.
   - Treat it as NOT allowed to take concurrently.

Always follow these rules strictly in all recommendations, explanations, and Notes fields in the final course table.
"""

system_prompt_generation_table_addon = """
# Required Final Section – "Courses for next semester"
Whenever you recommend specific courses for the student to take in an upcoming semester, you MUST finish your answer with a separate section titled exactly:

## Courses for next semester

Immediately under that heading, include a markdown table with **three columns** in this order:

| Course Code | Course Name | Notes |

The first row must be the header row shown above, the second row must be the separator row (using ---), and subsequent rows list one course per line. Example:

## Courses for next semester

| Course Code | Course Name | Notes |
| --- | --- | --- |
| MATH 1241 | Calculus I | Required foundation; take as soon as possible. |
| PHYS 2101 | Physics I | Take after or with Calculus I. |

Notes should briefly explain *why* the course is recommended (e.g., “required for major”, “prerequisite for X”, “good technical elective”, “retake due to previous F”, etc.).

If, for a particular user question, you **do not** want to recommend any specific courses, still include the heading and table header, but add one row like:

| Course Code | Course Name | Notes |
| --- | --- | --- |
| — | — | No specific course recommendations for this question. |

Do NOT invent fake codes. Always use real course codes that appear in the provided context, or use “—” if you cannot safely recommend a code.

Important constraints for the table:

- Only include a course in the "Courses for next semester" table if EITHER
  (a) the student has already completed all of its prerequisites with a passing grade, OR
  (b) it is a retake of a course the student previously failed/withdrew from.
- If a course requires a prerequisite the student has not yet passed (for example,
  PHYS 2102 requiring MATH 1242, when MATH 1242 is currently failed), then you MUST
  NOT list that course in the "Courses for next semester" table. You may discuss it
  in narrative text as a future option, but not in the table.

"""


# ----------------------------------------------------
# API Models for Chatbot Requests and Responses
# ----------------------------------------------------
class ChatRequest(BaseModel):
    conversation_history : List = Field(default_factory=list)
    user_prompt_text: str = Field(..., min_length=1, max_length=1000)
    student_catalog_year: str = Field(..., min_length=1, max_length=9)
    student_degree_program: str = Field(..., min_length=1, max_length=120)
    # allow empty string:
    student_credits_earned: str = Field("", min_length=0, max_length=40)
    pursued_courses: List[str] = Field(default_factory=list)
    pursued_courses_detailed: List[Dict[str, str]] = Field(default_factory=list)  # {course_code, course_name, grade}


class ChatResponse(BaseModel):
    error_code: int = 0
    chat_response_content: str = Field(..., min_length=1, max_length=10000)
    analytical_summary: str = Field(..., min_length=1, max_length=10000)
    information_requests: str = Field(..., min_length=0, max_length=1000)
    retrieved_context: Dict[str, List] = Field(default_factory=dict)
    flattened_context: str = Field(..., min_length=0, max_length=120000)
    planning_generation_time_required: float = Field(..., ge=0)
    retrieval_time_required: float = Field(..., ge=0)
    chat_response_generation_time_required: float = Field(..., ge=0)
    planning_attempts: int = Field(..., ge=0)
    planning_input_tokens: int = Field(..., ge=0)
    planning_output_tokens: int = Field(..., ge=0)
    chat_response_input_tokens: int = Field(..., ge=0)
    chat_response_output_tokens: int = Field(..., ge=0)
    # Clean structured suggestions for frontend/export
    suggested_courses: List[Dict[str, str]] = Field(default_factory=list)   # [{"course_code": "...", "course_name": "...", "notes": "..."}]

    # 🔹 NEW: markdown export for this single prompt/answer
    export_markdown: str = ""


class CourseDetails(BaseModel):
    code: str
    title: str = ""
    credits: str = ""
    description: str = ""
    prereqs: str = ""
    coreqs: str = ""



# ----------------------------------------------------
# Chat request handler
# ----------------------------------------------------
class Chatbot:
    def __init__(
        self,
        STORAGE_RETRIEVAL_MODE,
        generation_client,
        PLANNING_MODEL_ID,
        GENERATION_MODEL_ID,
        embedding_client,
        EMBEDDING_MODEL_NAME,
        ENCODING_FORMAT,
        db_client,
        COLLECTION_NAME_DEGREE_PROGRAMS,
        COLLECTION_NAME_COURSES,
        logger,
    ):
        self.STORAGE_RETRIEVAL_MODE = STORAGE_RETRIEVAL_MODE
        self.generation_client = generation_client
        self.PLANNING_MODEL_ID = PLANNING_MODEL_ID
        self.GENERATION_MODEL_ID = GENERATION_MODEL_ID
        self.embedding_client = embedding_client
        self.EMBEDDING_MODEL_NAME = EMBEDDING_MODEL_NAME
        self.ENCODING_FORMAT = ENCODING_FORMAT
        self.db_client = db_client
        self.COLLECTION_NAME_DEGREE_PROGRAMS = COLLECTION_NAME_DEGREE_PROGRAMS
        self.COLLECTION_NAME_COURSES = COLLECTION_NAME_COURSES
        self.logger = logger

        # Load courses from local corpus
        self.courses = load_courses(logger)

        # Optional manual prerequisite overrides (codes must be UPPERCASE)
        # Example:
        # self.prereq_overrides = {
        #     "ENGR 1202": ["ENGR 1201", "MATH 1241"],
        # }
        self.prereq_overrides: Dict[str, List[str]] = {}

    # ------------------------------------------------
    # Main chat handler
    # ------------------------------------------------
    def chat(self, chat_request: ChatRequest) -> ChatResponse:
        started_processing = time.time()
        # --- Build student context with grade-aware classification ---
        detailed = getattr(chat_request, "pursued_courses_detailed", []) or []
        legacy_list = chat_request.pursued_courses or []


        self.logger.info(f"Processing prompt request: {chat_request.user_prompt_text}")
        self.logger.info(f"Conversation history: {chat_request.conversation_history}")
        self.logger.info(
            f"Student degree program: {chat_request.student_degree_program}"
        )
        self.logger.info(
            f"Student catalog year: {chat_request.student_catalog_year}"
        )
        self.logger.info(
            f"Student credits earned: {chat_request.student_credits_earned}"
        )

        def _extract_codes(text: str) -> set[str]:
            out = set()
            for m in self.COURSE_CODE_RE.finditer((text or "").upper()):
                c = self._norm_code(m.group(0))
                if c:
                    out.add(c)
            return out

        def _needs_course_recs(user_text: str) -> bool:
            t = (user_text or "").lower()

            triggers = [
                # recommendation / suggestion intent
                "recommend", "recommendation", "suggest", "suggestion",
                "course suggestion", "course suggestions", "courses suggestion", "courses suggestions",

                # “what should I take” intent
                "what courses", "which courses",
                "what can i take", "can i take",
                "take next", "pursue next",

                # planning / scheduling intent
                "next semester", "next term",
                "schedule", "plan my", "course plan",
                "register", "enroll",
            ]
            return any(k in t for k in triggers)

        def _extract_credit_target(user_text: str) -> int:
            t = (user_text or "").lower()

            # 1️⃣ Strong patterns: "at least 9 credits", "minimum 9 credits"
            m = re.search(r"\b(at\s*least|min(?:imum)?)\s*(\d{1,2})\s*credits?\b", t)
            if m:
                return int(m.group(2))

            # 2️⃣ Standard: "9 credits"
            m2 = re.search(r"\b(\d{1,2})\s*credits?\b", t)
            if m2:
                return int(m2.group(1))

            # 3️⃣ NEW: "minimum 9", "at least 12"
            m3 = re.search(r"\b(at\s*least|min(?:imum)?)\s*(\d{1,2})\b", t)
            if m3:
                return int(m3.group(2))

            # 4️⃣ Optional: "I want 12 next semester"
            # Only trigger if context suggests course load
            if any(k in t for k in ["next semester", "next term", "course load", "schedule"]):
                m4 = re.search(r"\b(\d{1,2})\b", t)
                if m4:
                    value = int(m4.group(1))
                    # guardrail: only treat reasonable credit numbers as credit targets
                    if 6 <= value <= 21:
                        return value

            return 0



        def _min_courses_for_credits(credits: int) -> int:
            # Assume 3 credits per course unless you have per-course credit hours
            if credits <= 0:
                return 0
            return (credits + 2) // 3  # ceil(credits/3)
        

        

        def _is_course_name_lookup(user_text: str) -> bool:
            t = (user_text or "").lower()
            has_code = self.COURSE_CODE_RE.search((user_text or "").upper()) is not None
            if not has_code:
                return False

            name_terms = ["what is the name", "course name", "what is the title", "course title", "name of", "title of"]
            info_terms = ["prereq", "prerequisite", "credits", "description", "syllabus", "details", "coreq", "corequisite"]
            return any(k in t for k in name_terms) and not any(k in t for k in info_terms)

        

        def _is_course_info_request(user_text: str) -> bool:
            t = (user_text or "").lower()

            # must contain a course code
            has_code = self.COURSE_CODE_RE.search((user_text or "").upper()) is not None
            if not has_code:
                return False

            info_terms = [
                "information about", "info about", "tell me about", "details about",
                "describe", "description", "what do you know", "syllabus",
                "credits", "prerequisite", "prerequisites", "corequisite", "corequisites",
                "topics", "learning outcomes"
            ]
            return any(k in t for k in info_terms)
        


        
        is_name_lookup = _is_course_name_lookup(chat_request.user_prompt_text)
        is_course_info = _is_course_info_request(chat_request.user_prompt_text)
        needs_course_recs = (
            _needs_course_recs(chat_request.user_prompt_text)
            and (not is_name_lookup)
            and (not is_course_info)
        )

        self.logger.info(f"Intent gate: is_name_lookup={is_name_lookup}, is_course_info={is_course_info}, needs_course_recs={needs_course_recs}")



        m = None
        if is_name_lookup:
            # Match patterns like: "Course name of XXXX 1501?" or "What is the name of ENGR 1201"
            text = (chat_request.user_prompt_text or "").upper()
            m = re.search(r"\b([A-Z]{2,6})\s*[-]?\s*(\d{3,4}[A-Z]{0,2})\b", text)

        

        
        uploaded_name_map: dict[str, str] = {}

        # 1) If detailed rows exist (from UI), build from those (fast)
        for r in (detailed or []):
            code = self._norm_code(r.get("course_code", ""))
            name = (r.get("course_name") or "").strip()
            if code and name:
                uploaded_name_map[code] = name

        # 2) If an uploaded file path exists, merge it in (authoritative)


        self.logger.info(f"Uploaded course-name map (sample): {list(uploaded_name_map.items())[:5]}")


        if is_course_info:
            code = self._norm_code(self.COURSE_CODE_RE.search((chat_request.user_prompt_text or "").upper()).group(0))

            # 1) name (fast)
            name = self._lookup_course_name_fast(code, uploaded_name_map, chat_request.student_catalog_year)

            # 2) details from catalog (if you have them)
            details = self._lookup_course_details_fast(code, chat_request.student_catalog_year)  # you implement

            if details:
                content = (
                    f"### {details.code} — {details.title or 'Course'}\n\n"
                    f"**Credits:** {details.credits or 'Not listed'}\n\n"
                    f"**Description:** {details.description or 'Not listed'}\n\n"
                    f"**Prerequisites:** {details.prereqs or 'Not listed'}\n\n"
                    f"**Corequisites:** {details.coreqs or 'Not listed'}\n"
                )

                return ChatResponse(
                    error_code=0,
                    chat_response_content=content,
                    analytical_summary=f"User requested course information for {code}; found details in local catalog.",
                    information_requests="",
                    retrieved_context={},
                    flattened_context="",
                    planning_attempts=0,
                    planning_generation_time_required=0.0,
                    retrieval_time_required=0.0,
                    chat_response_generation_time_required=0.0,
                    planning_input_tokens=0,
                    planning_output_tokens=0,
                    chat_response_input_tokens=0,
                    chat_response_output_tokens=0,
                    suggested_courses=[],
                    export_markdown=f"# Course Info\n\n{content}",
                )



            # fallback: you only know the name from upload
            if name:
                content = (
                    f"### {code} — {name}\n\n"
                    "I can confirm the course title from your uploaded list, but I don’t have an official catalog "
                    "description/prerequisites/corequisites for this code in the local catalog data.\n\n"
                    "If you share the course description or syllabus text, I can summarize it and explain prerequisites/topics."
                )

                return ChatResponse(
                    error_code=0,
                    chat_response_content=content,
                    analytical_summary=f"User requested course information for {code}; found course title from uploaded list only.",
                    information_requests="",
                    retrieved_context={},
                    flattened_context="",
                    planning_attempts=0,
                    planning_generation_time_required=0.0,
                    retrieval_time_required=0.0,
                    chat_response_generation_time_required=0.0,
                    planning_input_tokens=0,
                    planning_output_tokens=0,
                    chat_response_input_tokens=0,
                    chat_response_output_tokens=0,
                    suggested_courses=[],
                    export_markdown=f"# Course Info\n\n{content}",
                )


            # not found anywhere
            not_found = (
                f"I couldn’t find **{code}** in your uploaded course list or in the local catalog "
                f"for **{chat_request.student_catalog_year}**.\n\n"
                "Please double-check the course code (department + number). If you paste the official catalog "
                "description text here, I can still summarize it and explain prerequisites/topics."
            )

            return ChatResponse(
                error_code=0,
                chat_response_content=not_found,
                analytical_summary=f"User requested course info for {code}, but it was not found.",
                information_requests="",
                retrieved_context={},
                flattened_context="",
                planning_attempts=0,
                planning_generation_time_required=0.0,
                retrieval_time_required=0.0,
                chat_response_generation_time_required=0.0,
                planning_input_tokens=0,
                planning_output_tokens=0,
                chat_response_input_tokens=0,
                chat_response_output_tokens=0,
                suggested_courses=[],
                export_markdown=f"# Course Info\n\n{not_found}",
            )



        
        if is_name_lookup and m:
            raw_code = f"{m.group(1)} {m.group(2)}"
            code = self._norm_code(raw_code)

            course_name = self._lookup_course_name_fast(
                code=code,
                uploaded_name_map=uploaded_name_map,
                catalog_year=chat_request.student_catalog_year,
            )

            if course_name:
                answer = f"**{code}** is **{course_name}**."
                return ChatResponse(
                    error_code=0,
                    chat_response_content=answer,
                    analytical_summary=f"User asked for course name of {code}; found via uploaded map or local catalog.",
                    information_requests="",
                    retrieved_context={},
                    flattened_context="",
                    planning_attempts=0,
                    planning_generation_time_required=0.0,
                    retrieval_time_required=0.0,
                    chat_response_generation_time_required=0.0,
                    planning_input_tokens=0,
                    planning_output_tokens=0,
                    chat_response_input_tokens=0,
                    chat_response_output_tokens=0,
                    suggested_courses=[],
                    export_markdown=f"# Course Name Lookup\n\n- **{code}**: {course_name}",
                )


            # ✅ NEW: graceful fallback response (no LLM)
            answer = (
                f"I couldn’t find **{code}** in your uploaded course list or in the local catalog "
                f"for **{chat_request.student_catalog_year}**. \n\n"
                "Please double-check the course code (department + number), or tell me your catalog year "
                "and program requirements for that course."
            )
            return ChatResponse(
                error_code=0,
                chat_response_content=answer,
                analytical_summary=f"User asked for the course name of {code}, but it was not found in available mappings.",
                information_requests="",
                retrieved_context={},
                flattened_context="",
                planning_attempts=0,
                planning_generation_time_required=0.0,
                retrieval_time_required=0.0,
                chat_response_generation_time_required=0.0,
                planning_input_tokens=0,
                planning_output_tokens=0,
                chat_response_input_tokens=0,
                chat_response_output_tokens=0,
                suggested_courses=[],
                export_markdown=f"# Course Name Lookup\n\n- **{code}**: (not found in uploaded list or local catalog)",
            )
        

        if needs_course_recs:
            system_hint = (
                "You are an academic advisor. Recommend courses the student has NOT passed. "
                "If a course was failed/withdrawn, prioritize suggesting an appropriate retake "
                "when it fits prerequisites and program flow. Respect prerequisites and avoid duplicates."
            )
        else:
            system_hint = (
                "You are an academic assistant. Answer the user's question directly. "
                "Do not recommend courses unless explicitly asked."
            )

        summary_lines: List[str] = []
        summary_for_llm = ""

        if needs_course_recs:
            summary_for_llm = "\n".join(summary_lines)
        else:
            summary_for_llm = "User is NOT asking for course recommendations. Do NOT suggest courses."


        # Fallback: parse legacy string list if detailed is empty
        if not detailed and legacy_list:
            import re as _re

            patt = _re.compile(
                r"^\s*([A-Z]{2,}\s*\d{3,4})\s*(?:-\s*(.*?))?(?:\s*\(GRADE:\s*([A-Z+\-]+)\))?\s*$",
                _re.I,
            )
            parsed = []
            for s in legacy_list:
                m2 = patt.match(str(s))
                if m2:
                    code = (m2.group(1) or "").strip()
                    name = (m2.group(2) or "").strip()
                    grade = (m2.group(3) or "").strip().upper()
                    parsed.append(
                        {
                            "course_code": code,
                            "course_name": name,
                            "grade": grade,
                        }
                    )
                else:
                    parsed.append(
                        {
                            "course_code": str(s).strip(),
                            "course_name": "",
                            "grade": "",
                        }
                    )
            detailed = parsed
            

        # Prepare system context for the LLM
        student_context: List[str] = []
        if chat_request.student_degree_program:
            student_context.append(f"Program: {chat_request.student_degree_program}")
        if chat_request.student_catalog_year:
            student_context.append(f"Catalog Year: {chat_request.student_catalog_year}")
        if chat_request.student_credits_earned:
            student_context.append(f"Credits: {chat_request.student_credits_earned}")


        passed_codes = set()
        failed_codes = set()
        unknown_grade_codes = set()

        for r in detailed:
            code = self._norm_code(r.get("course_code", ""))
            if not code:
                continue

            status = self._grade_status(r.get("grade", ""))

            if status == "passed":
                passed_codes.add(code)
            elif status == "failed":
                failed_codes.add(code)
            else:
                unknown_grade_codes.add(code)

        
        if passed_codes:
            student_context.append(
                "Completed (passed): " + ", ".join(sorted(c for c in passed_codes if c))
            )
        if failed_codes:
            student_context.append(
                "Failed/Withdrawn (needs retake): "
                + ", ".join(sorted(c for c in failed_codes if c))
            )

        if unknown_grade_codes:
            student_context.append(
                "Courses with unknown/missing grade status: "
                + ", ".join(sorted(unknown_grade_codes))
            )

        self.logger.info(f"passed_codes_count={len(passed_codes)} sample={sorted(passed_codes)[:20]}")
        self.logger.info(f"failed_codes_count={len(failed_codes)} sample={sorted(failed_codes)[:20]}")
        self.logger.info(f"unknown_grade_codes_count={len(unknown_grade_codes)} sample={sorted(unknown_grade_codes)[:20]}")

        
        # ---- Add detailed grade info for LLM ----
        grade_lines = []
        for r in detailed:
            code = self._norm_code(r.get("course_code", ""))
            grade = (r.get("grade") or "").strip().upper()
            name = (r.get("course_name") or "").strip()

            if code:
                if name:
                    grade_lines.append(f"- {code}: {grade or 'NO_GRADE'}")
                else:
                    grade_lines.append(f"- {code}: {grade or 'NO_GRADE'}")

        if grade_lines:
            student_context.append(
                "Uploaded course grades:\n" + "\n".join(grade_lines[:50])
            )
        
        structured_suggestions: List[Dict[str, str]] = []




        MAX_UPLOADED_MAPPINGS_IN_PROMPT = 25  # tune: 15–30 is usually good

        def _pick_uploaded_mapping_subset(
            uploaded: Dict[str, str],
            prompt_codes: Set[str],
            structured: List[Dict[str, str]],
            failed: Set[str],
            passed: Set[str],
            k: int,
        ) -> List[tuple[str, str]]:
            picked: List[tuple[str, str]] = []
            seen: Set[str] = set()

            def _add_codes(codes: Set[str]):
                nonlocal picked, seen
                for c in codes:
                    if c in seen:
                        continue
                    name = uploaded.get(c)
                    if not name:
                        continue
                    picked.append((c, name))
                    seen.add(c)
                    if len(picked) >= k:
                        return True
                return False

            # Priority 1: codes explicitly mentioned in the user prompt
            if _add_codes(prompt_codes):
                return picked

            # Priority 2: structured suggestions (the ones you might recommend)
            structured_codes = {self._norm_code(r.get("course_code", "")) for r in (structured or [])}
            if _add_codes(structured_codes):
                return picked

            # Priority 3: failed courses (retakes can appear in the answer)
            if _add_codes(set(failed)):
                return picked

            # Priority 4: passed courses (lowest value; include only if room)
            _add_codes(set(passed))
            return picked


         



        # --- PREPARE CONTEXT (planning + retrieval) ---
        (
            analytical_summary,
            information_requests,
            retrieved_context,
            flattened_context,
            planning_attempts,
            planning_completed_time,
            planning_input_tokens,
            planning_output_tokens,
            embedding_tokens,
        ) = self.prepare_context(chat_request=chat_request)


        if needs_course_recs:
            forced_retrieval = (
                "<Specific_Request_Current_Major></Specific_Request_Current_Major>\n"
                "<Specific_Request_Current_Major_Sample_Schedules></Specific_Request_Current_Major_Sample_Schedules>"
            )

            retrieved_context_forced, _ = self.retrieve_context_basic(
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                information_requests=forced_retrieval,
            )

            for k, v in retrieved_context_forced.items():
                if k not in retrieved_context:
                    retrieved_context[k] = v
                else:
                    retrieved_context[k].extend(v)

            flattened_context = self._flatten_context(
                chat_request=chat_request,
                retrieved_context=retrieved_context,
            )


        # Build canonical course-name map EARLY so it can be used by fallback table guard too
        name_map = self._build_canonical_course_name_map(
            catalog_year=chat_request.student_catalog_year,
            flattened_context=flattened_context,
            uploaded_name_map=uploaded_name_map,
        )

        degree_plan_codes = self._extract_degree_plan_codes(flattened_context)
        self.logger.info(
            f"degree_plan_codes_count={len(degree_plan_codes)} "
            f"sample={degree_plan_codes[:20]}"
        )

        if needs_course_recs:
            structured_suggestions = self._suggest_courses_structured(
                passed_codes=passed_codes,
                failed_codes=failed_codes,
                catalog_year=chat_request.student_catalog_year,
                max_results=12,
                preferred_codes=degree_plan_codes,
            )
        
        if uploaded_name_map:
            prompt_codes = _extract_codes(chat_request.user_prompt_text or "")

            subset_items = _pick_uploaded_mapping_subset(
                uploaded=uploaded_name_map,
                prompt_codes=prompt_codes,
                structured=structured_suggestions,
                failed=failed_codes,
                passed=passed_codes,
                k=MAX_UPLOADED_MAPPINGS_IN_PROMPT,
            )

            if subset_items:
                lines = [
                    f"Uploaded course name mappings (authoritative, subset; showing up to {MAX_UPLOADED_MAPPINGS_IN_PROMPT}):"
                ]
                for code, name in subset_items:
                    lines.append(f"- {code}: {name}")
                student_context.append("\n".join(lines))
                self.logger.info(f"Injected {len(subset_items)} uploaded name mappings into prompt.")
            else:
                self.logger.info("No uploaded mappings needed for this prompt.")

        self.logger.info(
            f"structured_suggestions_count={len(structured_suggestions)} "
            f"(needs_course_recs={needs_course_recs})"
        )
        self.logger.info(f"structured_suggestions_sample={structured_suggestions[:10]}")


        # ✅ RIGHT HERE: build the gated generation prompt
        generation_prompt = system_prompt_generation_base + flattened_context

        if needs_course_recs:
            generation_prompt += system_prompt_generation_table_addon
        else:
            generation_prompt += (
                "\nIMPORTANT: Do NOT include a 'Courses for next semester' section "
                "unless the user explicitly asks for course recommendations or planning."
            )

        self.logger.info(
            f"Prompt gate applied: needs_course_recs={needs_course_recs} "
            f"table_addon_included={'Courses for next semester' in generation_prompt}"
        )


        retrieval_completed_time = time.time()





        # --- Grade-aware helper summary for the LLM ---

        if needs_course_recs:
            # build the recommendation-flavored summary
            if failed_codes:
                summary_lines.append("Retake opportunities detected (student previously failed/withdrew):")
                for code in sorted(c for c in failed_codes if c):
                    summary_lines.append(f"- {code}")
                summary_lines.append("")

            summary_lines.append("Eligible next-course candidates (already prereq-filtered from catalog):")
            if structured_suggestions:
                for row in structured_suggestions:
                    summary_lines.append(f"- {row['course_code']} ({row.get('course_name', '')})")
            else:
                summary_lines.append("- (none found)")

            summary_for_llm = "\n".join(summary_lines)

        else:
            # non-recommendation mode: keep it minimal and non-advising
            summary_for_llm = "User is NOT asking for course recommendations. Answer the question directly. Do NOT suggest courses."



        messages = [
            {"role": "system", "content": generation_prompt},
            {"role": "system", "content": "\n".join(student_context)},
        ]


        # include conversation history (already in OpenAI format)
        if chat_request.conversation_history:
            messages.extend(chat_request.conversation_history)

        # include the current user prompt
        messages.append({"role": "user", "content": chat_request.user_prompt_text})

        # helpful structured summary (optional)
        messages.append({"role": "system", "content": summary_for_llm})


        # --- GENERATE CHAT RESPONSE ---
        try:
            chat_response = self.generation_client.chat.completions.create(
                model=self.GENERATION_MODEL_ID,
                messages=messages,
            )
            chat_response_content = chat_response.choices[0].message.content
            chat_prompt_tokens = (
                getattr(getattr(chat_response, "usage", None), "prompt_tokens", 0) or 0
            )
            chat_completion_tokens = (
                getattr(getattr(chat_response, "usage", None), "completion_tokens", 0)
                or 0
            )
        except Exception as e:
            self.logger.error(f"Error generating response: {e}")
            raise

        generation_completed_time = time.time()
        self.logger.info(
            f"Generated response obtained: {chat_response_content[:500]}..."
        )


        # ✅ Fix Part C: table-missing fallback guard
        if needs_course_recs:
            # If model forgot to include the table section, append a deterministic minimal one
            if "## Courses for next semester" not in (chat_response_content or ""):
                self.logger.warning("LLM omitted 'Courses for next semester' section; appending fallback table.")

                # Use your already-computed structured_suggestions (prereq-safe) for fallback rows
                fallback_rows = structured_suggestions[:8] if structured_suggestions else []

                table_lines = [
                    "## Courses for next semester",
                    "",
                    "| Course Code | Course Name | Notes |",
                    "| --- | --- | --- |",
                ]

                if fallback_rows:
                    for r in fallback_rows:
                        code = self._norm_code(r.get("course_code") or "")
                        name = name_map.get(code) or (r.get("course_name") or "").strip() or "—"
                        table_lines.append(f"| {code} | {name} | Eligible based on completed prerequisites. |")

                else:
                    table_lines.append("| — | — | No eligible course recommendations based on prerequisites. |")

                chat_response_content = (chat_response_content or "").rstrip() + "\n\n" + "\n".join(table_lines) + "\n"


        
        # ------------------------------------------------
        # Parse & filter course suggestions ONLY if needed
        # ------------------------------------------------
        parsed_from_llm = []
        filtered_llm = []
        final_suggestions = []


        credit_target = _extract_credit_target(chat_request.user_prompt_text)
        min_courses = _min_courses_for_credits(credit_target)

        if needs_course_recs and min_courses >= 3:
            # final_suggestions might be only 2 rows; top-up from structured_suggestions
            existing = {self._norm_code(r.get("course_code", "")) for r in (final_suggestions or [])}

            # Add more eligible courses (non-duplicates) until we hit min_courses
            for r in (structured_suggestions or []):
                code = self._norm_code(r.get("course_code") or "")
                if not code or code in existing:
                    continue
                final_suggestions.append({"course_code": code, "course_name": r.get("course_name",""), "notes": "Additional eligible course to meet your minimum credit target."})
                existing.add(code)
                if len(final_suggestions) >= min_courses:
                    break

        if needs_course_recs:
            # 1) Parse table from LLM output
            parsed_from_llm = self._parse_suggested_courses_from_response(
                chat_response_content
            )

            # 2) Filter by prerequisites
            filtered_llm = self._filter_suggestions_by_prereqs(
                parsed_from_llm,
                passed_codes=passed_codes,
                failed_codes=failed_codes,
                catalog_year=chat_request.student_catalog_year,
            )

            # 3) catalog + uploaded + program-table extracted codes
            known_codes = set(name_map.keys())

            filtered_llm = self._filter_to_known_courses(
                filtered_llm,
                known_codes=known_codes,
                failed_codes=failed_codes,
            )

            # 4) Apply canonical names
            filtered_llm = self._apply_names(filtered_llm, name_map)
            structured_suggestions = self._apply_names(structured_suggestions, name_map)

            # 5) Final fallback
            final_suggestions = filtered_llm or structured_suggestions
            chat_response_content = self._rewrite_courses_table_in_response(
                chat_response_content,
                final_suggestions,
            )
            chat_response_content = self._rewrite_courses_table_in_response(
                original_text=chat_response_content,
                final_suggestions=final_suggestions,
            )


        else:
            # ❗ IMPORTANT: explicitly keep them empty
            parsed_from_llm = []
            filtered_llm = []
            final_suggestions = []

        self.logger.info(
            f"Post-gating: needs_course_recs={needs_course_recs}, "
            f"parsed={len(parsed_from_llm)}, final_suggestions={len(final_suggestions)}"
        )

        # ✅ Polished enforcement: ensure enough courses to match "minimum X credits"
        credit_target = 0
        min_courses = 0

        if needs_course_recs:
            credit_target = self._extract_credit_target(chat_request.user_prompt_text)
            min_courses = self._min_courses_for_credits(credit_target)

            if min_courses > 0:
                # Ensure final_suggestions exists and is prereq-safe (already true by your pipeline)
                if len(final_suggestions) < min_courses:
                    self.logger.info(
                        f"Credit target detected: {credit_target}. "
                        f"Need at least {min_courses} courses but have {len(final_suggestions)}. "
                        f"Attempting to pad from structured_suggestions."
                    )

                    # Use structured_suggestions as additional prereq-safe pool
                    pool = structured_suggestions or []
                    existing = {self._norm_code(r.get("course_code", "")) for r in (final_suggestions or [])}

                    for r in pool:
                        c = self._norm_code(r.get("course_code", ""))
                        if not c or c in existing:
                            continue
                        final_suggestions.append(r)
                        existing.add(c)
                        if len(final_suggestions) >= min_courses:
                            break

                # Rewrite the "Courses for next semester" table to match what we will return
                chat_response_content = self._rewrite_courses_table_in_response(
                    original_text=chat_response_content,
                    final_suggestions=final_suggestions[: max(min_courses, 1)]
                )

                # Add a clean note for user clarity (only if a target was detected)
                if credit_target > 0:
                    note = (
                        f"\n\n> **Note:** You requested a minimum of **{credit_target}** credits. "
                        f"I listed **{len(final_suggestions[: max(min_courses, 1)])}** course(s) "
                        f"(typically ~3 credits each) to help meet that target. "
                        f"Please confirm exact credit hours with your advisor/degree audit.\n"
                    )
                    # Avoid duplicating the note if user refreshes
                    if "You requested a minimum of" not in chat_response_content:
                        chat_response_content = chat_response_content.rstrip() + note


        # --- Build Markdown export for this prompt (for professor + frontend) ---
        from datetime import datetime

        ts_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        md_lines = [
            "# Niner Pathfinder – Prompt Run",
            "",
            f"- Timestamp: {ts_utc}",
            f"- Catalog Year: {chat_request.student_catalog_year}",
            f"- Degree Program: {chat_request.student_degree_program}",
            f"- Credits Earned: {chat_request.student_credits_earned}",
            "",
            "## Prompt",
            "",
            chat_request.user_prompt_text,
            "",
            "## LLM Answer",
            "",
            chat_response_content,
            "",
            "## Parsed course suggestions (after prerequisite checks)",
            "",
            "| Course Code | Course Name | Notes |",
            "| --- | --- | --- |",
        ]

        if final_suggestions:
            for row in final_suggestions:
                md_lines.append(
                    f"| {row.get('course_code','')} | "
                    f"{row.get('course_name','')} | "
                    f"{row.get('notes','')} |"
                )
        else:
            md_lines.append(
                "| — | — | No parsed suggestions for this prompt. |"
            )

        export_markdown = "\n".join(md_lines)

        # Also write out a file on the backend for professor inspection
        try:
            safe_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            export_dir = os.path.join("exports", "chat_runs")
            os.makedirs(export_dir, exist_ok=True)
            filename = f"chat_{safe_ts}.md"
            filepath = os.path.join(export_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(export_markdown)
            self.logger.info(f"Wrote per-prompt export to {filepath}")
        except Exception as e:
            self.logger.error(f"Failed to write export markdown file: {e}")


        # --- Timings ---
        planning_generation_time_required = planning_completed_time - started_processing
        retrieval_time_required = retrieval_completed_time - planning_completed_time
        chat_response_generation_time_required = (
            generation_completed_time - retrieval_completed_time
        )

        self.logger.info(
            f"Planning time: {planning_generation_time_required:.2f}s"
        )
        self.logger.info(f"Retrieval time: {retrieval_time_required:.2f}s")
        self.logger.info(
            f"Generation time: {chat_response_generation_time_required:.2f}s"
        )

        return ChatResponse(
            error_code=0,
            chat_response_content=chat_response_content,
            analytical_summary=analytical_summary,
            information_requests=information_requests,
            retrieved_context=retrieved_context,
            flattened_context=flattened_context,
            planning_attempts=planning_attempts,
            planning_generation_time_required=planning_generation_time_required,
            retrieval_time_required=retrieval_time_required,
            chat_response_generation_time_required=chat_response_generation_time_required,
            planning_input_tokens=planning_input_tokens,
            planning_output_tokens=planning_output_tokens,
            chat_response_input_tokens=chat_prompt_tokens,
            chat_response_output_tokens=chat_completion_tokens,
            suggested_courses=final_suggestions if needs_course_recs else [],
            # 🔹 NEW
            export_markdown=export_markdown,
        )
    
    

    
    def _rewrite_courses_table_in_response(
        self,
        original_text: str,
        final_suggestions: List[Dict[str, str]],
    ) -> str:
        """
        Replace the markdown table under '## Courses for next semester' with a table
        built from final_suggestions (already prereq-filtered).
        If the section doesn't exist, append it.
        """
        if not original_text:
            original_text = ""

        new_lines = []
        new_lines.append("## Courses for next semester")
        new_lines.append("")
        new_lines.append("| Course Code | Course Name | Notes |")
        new_lines.append("| --- | --- | --- |")

        if final_suggestions:
            for r in final_suggestions:
                code = (r.get("course_code") or "").strip() or "—"
                name = (r.get("course_name") or "").strip() or "—"
                notes = (r.get("notes") or "").strip()
                if not notes:
                    if "retake" in (r.get("notes","").lower()):
                        notes = "Retake recommended based on prior attempt."
                    else:
                        notes = "Eligible based on completed prerequisites (confirm credit hours with your advisor)."

                new_lines.append(f"| {code} | {name} | {notes} |")
        else:
            new_lines.append("| — | — | No eligible course recommendations based on prerequisites. |")

        new_table_block = "\n".join(new_lines)

        # Find and replace existing section
        pattern = re.compile(
            r"(?is)##\s*Courses\s*for\s*next\s*semester\s*\n.*?(?=\n##\s|\Z)"
        )

        if pattern.search(original_text):
            return pattern.sub(new_table_block + "\n", original_text)
        else:
            # Section missing: append at end
            return (original_text.rstrip() + "\n\n" + new_table_block + "\n")

    def _flatten_context(
        self,
        chat_request: ChatRequest,
        retrieved_context: Dict[str, List[Any]],
    ) -> str:
        student_info_from_ui_text = f"""# Student Information:
    The student has provided the following information via dropdowns in the user interface:
    Student catalog year: {chat_request.student_catalog_year}.
    Student degree program: {chat_request.student_degree_program}.
    Student has earned: {chat_request.student_credits_earned}.
    """

        flattened_context = student_info_from_ui_text

        for information_type_tag, information in retrieved_context.items():
            tag_name = str(information_type_tag).strip().strip("<>").strip("/")

            flattened_context += f"\n### Context: {tag_name}\n<{tag_name}>\n"

            for item in (information or []):
                if isinstance(item, str):
                    flattened_context += f"{item}\n\n"
                elif isinstance(item, dict):
                    flattened_context += f"{str(item)}\n\n"
                else:
                    flattened_context += f"{str(item)}\n\n"

            flattened_context += f"</{tag_name}>\n"

        return flattened_context
    
    # ------------------------------------------------
    # Context preparation (planning + retrieval)
    # ------------------------------------------------
    def prepare_context(self, chat_request: ChatRequest):
        # Student info for context
        student_info_from_ui_text = f"""# Student Information:     
            The student has provided the following information via dropdowns in the user interface:
            Student catalog year: {chat_request.student_catalog_year}.
            Student degree program: {chat_request.student_degree_program}.
            Student has earned: {chat_request.student_credits_earned}.
            """

        system_prompt_planning = (
            system_prompt_planning_common_component
            + system_prompt_planning_retrieval_mode_component[
                self.STORAGE_RETRIEVAL_MODE
            ]
            + student_info_from_ui_text
        )

        message_history_analysis = chat_request.conversation_history.copy()
        message_history_analysis.extend(
            [
                {"role": "system", "content": system_prompt_planning},
                {"role": "user", "content": chat_request.user_prompt_text},
            ]
        )

        # --- Safe planning call with retries ---
        planning_response = None
        planning_attempts = 0
        planning_response_content = ""

        while planning_attempts < 3:
            planning_attempts += 1
            try:
                planning_response = self.generation_client.chat.completions.create(
                    model=self.PLANNING_MODEL_ID,
                    messages=message_history_analysis,
                    temperature=0.0,
                    max_tokens=1000,
                    top_p=0.01,
                )
                planning_response_content = (
                    planning_response.choices[0].message.content or ""
                )
            except Exception as e:
                self.logger.error(f"Planning generation call error: {e}")
                planning_response = None
                planning_response_content = ""

            self.logger.info(
                f"Planning attempt {planning_attempts} of 3: "
                f"{planning_response_content[:300]}..."
            )

            pattern = planning_response_validation_pattern[
                self.STORAGE_RETRIEVAL_MODE
            ]
            planning_pattern_match = bool(
                re.search(pattern, planning_response_content, re.DOTALL)
            )

            if planning_pattern_match:
                self.logger.info(
                    f"Planning response matches expected format for mode {self.STORAGE_RETRIEVAL_MODE}."
                )
                break
            elif planning_attempts < 3:
                self.logger.error(
                    f"Planning response did not match expected format "
                    f"(mode {self.STORAGE_RETRIEVAL_MODE}). Retrying..."
                )
                planning_response_content = ""
            else:
                self.logger.error(
                    "Planning response did not match expected format after 3 attempts "
                    f"(mode {self.STORAGE_RETRIEVAL_MODE}). Continuing with empty retrieval."
                )
                planning_response_content = ""
                break


        m_sum = re.search(r"<Analytical_Summary>(.*?)</Analytical_Summary>", planning_response_content or "", re.DOTALL)
        m_ret = re.search(r"<Retrieval>(.*?)</Retrieval>", planning_response_content or "", re.DOTALL)

        analytical_summary = (m_sum.group(1).strip() if m_sum else "").strip()
        information_requests = (m_ret.group(1).strip() if m_ret else "").strip()

        # IMPORTANT: never allow empty analytical_summary (ChatResponse requires min_length=1)
        if not analytical_summary:
            analytical_summary = "No analytical summary (planner output missing or malformed)."


        if not planning_response_content or (not analytical_summary and not information_requests):
            self.logger.warning("Planner returned empty/malformed XML. Using empty retrieval requests.")
            analytical_summary = analytical_summary or ""
            information_requests = information_requests or ""

        usage = getattr(planning_response, "usage", None)
        planning_input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        planning_output_tokens = getattr(usage, "completion_tokens", 0) or 0


        planning_complete = time.time()

        self.logger.info(f"User prompt text: {chat_request.user_prompt_text}")
        self.logger.info(f"Analytical Summary: {analytical_summary}")
        self.logger.info(f"Information Requests: {information_requests}")

        embedding_tokens = 0
        if self.STORAGE_RETRIEVAL_MODE == "0":
            retrieved_context, embedding_tokens = self.retrieve_context_basic(
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                information_requests=information_requests,
            )
        elif self.STORAGE_RETRIEVAL_MODE == "1":
            # Placeholder: advanced retrieval mode
            retrieved_context = self.retrieve_context_next(
                user_prompt_text=chat_request.user_prompt_text,
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                student_credits_earned=chat_request.student_credits_earned,
                analytical_summary=analytical_summary,
                information_requests=information_requests,
            )
            embedding_tokens = 0
        else:
            retrieved_context = {}

        # Logging
        for context_type, context_items in retrieved_context.items():
            if context_items and isinstance(context_items, str):
                self.logger.info(
                    f"Retrieved context type: {context_type}, "
                    f"content length: {len(context_items)}"
                )
                self.logger.info(
                    f"Retrieved context item content: {context_items[:50]}..."
                )
                self.logger.error("Retrieved context item is a string, not a list!")
            elif (
                context_items
                and isinstance(context_items, list)
                and len(context_items) > 0
                and isinstance(context_items[0], str)
            ):
                self.logger.info(
                    f"Retrieved context type: {context_type}, "
                    f"number of items: {len(context_items)}"
                )
                for item in context_items:
                    self.logger.info(
                        f"Retrieved context item {len(item)}, content: {item[:50]}..."
                    )

        # Flatten the retrieved context into a single string

        flattened_context = self._flatten_context(
            chat_request=chat_request,
            retrieved_context=retrieved_context,
        )


        self.logger.info(
            f"\n  Flattened context characters: {len(flattened_context)}, "
            f"\n  Content: {flattened_context[:500]}..."
        )

        return (
            analytical_summary,
            information_requests,
            retrieved_context,
            flattened_context,
            planning_attempts,
            planning_complete,
            planning_input_tokens,
            planning_output_tokens,
            embedding_tokens,
        )

    # ------------------------------------------------
    # Retrieval
    # ------------------------------------------------
    def retrieve_context_basic(
        self,
        student_catalog_year: str,
        student_degree_program: str,
        information_requests: str,
    ):
        retrieved_context: Dict[str, List[Any]] = {}
        embedding_tokens = 0

        try:
            # Specific sample schedules
            if (
                information_requests.find(
                    "<Specific_Request_Current_Major_Sample_Schedules>"
                )
                != -1
            ):
                sample_schedule_file_path = os.path.join(
                    "rag_corpus",
                    "sample_schedules",
                    student_catalog_year,
                    f"{student_degree_program}.md"
                )

                if os.path.exists(sample_schedule_file_path):
                    with open(sample_schedule_file_path, "r") as file:
                        retrieved_context[
                            "<Specific_Request_Current_Major_Sample_Schedules>"
                        ] = [file.read()]
                else:
                    self.logger.warning(
                        "Sample schedule file not found for "
                        f"{student_degree_program} {student_catalog_year}."
                    )

            # Specific current major description
            if information_requests.find("<Specific_Request_Current_Major>") != -1:
                current_major_file_path = os.path.join(
                    "rag_corpus",
                    "ug_cat",
                    student_catalog_year,
                    "programs",
                    student_degree_program + ".md",
                )

                if os.path.exists(current_major_file_path):
                    with open(current_major_file_path, "r") as file:
                        retrieved_context["<Specific_Request_Current_Major>"] = [
                            file.read()
                        ]
                else:
                    self.logger.warning(
                        f"Major file not found at:  {current_major_file_path}"
                    )

            # Semantic: programs
            if information_requests.find("<Semantic_Request_Programs>") != -1:
                self.logger.info("Starting semantic search for programs")
                search_text = (
                    information_requests.split("</Semantic_Request_Programs>")[0]
                    .split("<Semantic_Request_Programs>")[1]
                    .strip()
                )
                self.logger.info(f"Search text parsed: {search_text}")

                embedding_response = self.embedding_client.embeddings.create(
                    model=self.EMBEDDING_MODEL_NAME,
                    input=search_text,
                    encoding_format=self.ENCODING_FORMAT,
                )

                prompt_embedding = embedding_response.data[0].embedding
                embedding_tokens += embedding_response.usage.prompt_tokens

                self.logger.info(
                    "Embedding vector created "
                    f"({embedding_response.usage.prompt_tokens} tokens) "
                    f"for search text: {search_text}"
                )

                retrieved_context["<Semantic_Request_Programs>"] = vector_query(
                    query_vector_embedding=prompt_embedding,
                    db_client=self.db_client,
                    db_collection_name=self.COLLECTION_NAME_DEGREE_PROGRAMS,
                    limit=4,
                )

            # Semantic: courses
            if information_requests.find("<Semantic_Request_Courses>") != -1:
                self.logger.info("Starting semantic search for courses")
                search_text = (
                    information_requests.split("</Semantic_Request_Courses>")[0]
                    .split("<Semantic_Request_Courses>")[1]
                    .strip()
                )
                self.logger.info(f"Search text parsed: {search_text}")

                embedding_response = self.embedding_client.embeddings.create(
                    model=self.EMBEDDING_MODEL_NAME,
                    input=search_text,
                    encoding_format=self.ENCODING_FORMAT,
                )

                prompt_embedding = embedding_response.data[0].embedding
                embedding_tokens += embedding_response.usage.prompt_tokens

                self.logger.info(
                    "Embedding vector created "
                    f"({embedding_response.usage.prompt_tokens} tokens) "
                    f"for search text: {search_text}"
                )

                retrieved_context["<Semantic_Request_Courses>"] = vector_query(
                    query_vector_embedding=prompt_embedding,
                    db_client=self.db_client,
                    db_collection_name=self.COLLECTION_NAME_COURSES,
                    limit=30,
                )

        except Exception as e:
            self.logger.error(f"Database query error: {e}")
            self.logger.exception("")

        return retrieved_context, embedding_tokens

    def retrieve_context_next(
        self,
        user_prompt_text: str,
        student_catalog_year: str,
        student_degree_program: str,
        student_credits_earned: str,
        analytical_summary: str,
        information_requests: str,
    ):
        # Placeholder for a more advanced retrieval mode
        retrieved_context: Dict[str, List[Any]] = {}
        return retrieved_context

    # ------------------------------------------------
    # Parse "Courses for next semester" table
    # ------------------------------------------------
    def _parse_suggested_courses_from_response(
        self, text: str
    ) -> List[Dict[str, str]]:
        """
        Parse the 'Courses for next semester' markdown table from the LLM response.
        Returns a list of dicts: {'course_code', 'course_name', 'notes'}.
        """
        rows: List[Dict[str, str]] = []
        if not text:
            return rows

        lines = text.splitlines()
        in_section = False
        in_table = False
        header_indices = {"course_code": 0, "course_name": 1, "notes": 2}

        for line in lines:
            stripped = line.strip()

            # Find the section heading first
            if not in_section:
                if "courses for next semester" in stripped.lower():
                    in_section = True
                continue

            # Once in section, look for table lines beginning with '|'
            if stripped.startswith("|"):
                # First '|' line is the header row
                if not in_table:
                    in_table = True
                    header_cells = [c.strip() for c in stripped.strip("|").split("|")]
                    lower = [h.lower() for h in header_cells]

                    def _idx(name, default):
                        for i, h in enumerate(lower):
                            if name in h:
                                return i
                        return default

                    header_indices["course_code"] = _idx("course code", 0)
                    header_indices["course_name"] = _idx("course name", 1)
                    header_indices["notes"] = _idx("notes", 2)
                    continue

                # Skip separator row
                if (
                    set(
                        stripped.replace("|", "")
                        .replace("-", "")
                        .replace(":", "")
                        .strip()
                    )
                    == set()
                ):
                    continue

                cells = [c.strip() for c in stripped.strip("|").split("|")]
                max_idx = max(header_indices.values())
                if len(cells) <= max_idx:
                    continue

                code = cells[header_indices["course_code"]]
                name = cells[header_indices["course_name"]]
                notes = (
                    cells[header_indices["notes"]]
                    if header_indices["notes"] < len(cells)
                    else ""
                )

                # Ignore empty/fake rows
                if code.strip() in {"", "-", "—"} and name.strip() in {"", "-", "—"}:
                    continue

                rows.append(
                    {
                        "course_code": code.strip(),
                        "course_name": name.strip(),
                        "notes": notes.strip(),
                    }
                )
            else:
                if in_table:
                    break

        return rows
    
    def _extract_credit_target(self, user_text: str) -> int:
        t = (user_text or "").lower()

        # Strong patterns: "at least 9 credits", "minimum 9 credits"
        m = re.search(r"\b(at\s*least|min(?:imum)?)\s*(\d{1,2})\s*credits?\b", t)
        if m:
            return int(m.group(2))

        # Standard: "9 credits"
        m2 = re.search(r"\b(\d{1,2})\s*credits?\b", t)
        if m2:
            return int(m2.group(1))

        # NEW: "minimum 9", "at least 12"  (no 'credits' word)
        m3 = re.search(r"\b(at\s*least|min(?:imum)?)\s*(\d{1,2})\b", t)
        if m3:
            return int(m3.group(2))

        # Optional: raw number if course-load context exists
        if any(k in t for k in ["next semester", "next term", "course load", "schedule", "enroll", "register"]):
            m4 = re.search(r"\b(\d{1,2})\b", t)
            if m4:
                v = int(m4.group(1))
                if 6 <= v <= 21:
                    return v

        return 0


    def _min_courses_for_credits(self, credit_target: int) -> int:
        """
        Conservative assumption: typical courses are 3 credits.
        0 => no enforcement.
        """
        if not credit_target or credit_target <= 0:
            return 0
        # ceil(credit_target / 3)
        return (credit_target + 2) // 3


    # ------------------------------------------------
    # Candidate discovery helpers (currently unused but kept)
    # ------------------------------------------------
    def _find_candidate_courses(
        self, query_text: str, retrieved_context: Dict, course_catalog: Dict
    ):
        """
        Returns list[dict] with at least:
        {
          'course_code': 'ITSC 2214',
          'title': 'Data Structures and Algorithms',
          'prerequisites': ['ITSC 1213'],
          'score': 0.0
        }
        """
        candidates: List[Dict[str, Any]] = []

        # 1) Try to parse from retrieved context
        try:
            items = retrieved_context.get("<Semantic_Request_Courses>", [])
            for it in items:
                if isinstance(it, dict):
                    code = (it.get("course_code") or it.get("code") or "").strip()
                    title = (it.get("title") or it.get("course_name") or "").strip()
                    prereqs = it.get("prerequisites") or it.get("prereqs") or []
                    score = float(it.get("score") or 0.0)
                else:
                    s = str(it)
                    code, title, prereqs, score = self._loose_parse_course_text(s)

                if code:
                    candidates.append(
                        {
                            "course_code": code.upper(),
                            "title": title,
                            "prerequisites": (
                                prereqs if isinstance(prereqs, list) else []
                            ),
                            "score": score,
                        }
                    )
        except Exception:
            pass

        # 2) Fallback: keyword match over local catalog
        if not candidates and isinstance(course_catalog, dict):
            q = (query_text or "").lower()
            for code, meta in course_catalog.items():
                title = (meta.get("title") or meta.get("course_name") or "").lower()
                desc = (meta.get("description") or "").lower()
                if any(
                    tok in (title + " " + desc) for tok in self._keywords_from_query(q)
                ):
                    candidates.append(
                        {
                            "course_code": code.upper(),
                            "title": (meta.get("title") or meta.get("course_name") or "")
                            .strip(),
                            "prerequisites": meta.get("prerequisites")
                            or meta.get("prereqs")
                            or [],
                            "score": 0.0,
                        }
                    )

        # Deduplicate by course_code, keep best score
        dedup: Dict[str, Dict[str, Any]] = {}
        for c in candidates:
            code = c["course_code"]
            if code not in dedup or c.get("score", 0) > dedup[code].get("score", 0):
                dedup[code] = c

        return list(dedup.values())
    
    # use the SAME regex as _norm_code
    COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6})\s*([0-9]{3,4})([A-Z]{0,2})\b", re.I)


    # 🔹 NEW: detect "what is the name of XXXX 1501" style questions
    COURSE_CODE_QUERY_RE = re.compile(
        r"\b(name|title)\b.*\b([A-Z]{2,6}\s*\d{3,4})\b"
        r"|\b([A-Z]{2,6}\s*\d{3,4})\b.*\b(name|title)\b",
        re.I
    )

    def _extract_course_names_from_program_markdown(self, markdown_text: str) -> dict[str, str]:
        """
        Extract course_code -> course_name from markdown tables like:
        | XXXX 1501 Global Social Science | 3 | C | ... |
        """
        mapping: dict[str, str] = {}
        if not markdown_text:
            return mapping

        for line in markdown_text.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue

            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue

            first_cell = cells[0]  # "XXXX 1501 Global Social Science"

            # Find a course code inside the first cell
            m = self.COURSE_CODE_RE.search(first_cell.upper())
            if not m:
                continue

            # Build normalized code like "XXXX 1501" (works for 2-6 letter departments too)
            dept = m.group(1).upper()
            num = m.group(2)
            code = f"{dept} {num}"

            # Everything AFTER the matched code is the course name
            name = first_cell[m.end():].strip()

            # Skip obvious header rows
            if not name or name.lower() in {"course", "course code"}:
                continue

            mapping[code] = name

        return mapping




    def _keywords_from_query(self, q: str):
        toks = [t for t in re.split(r"[^a-z0-9+]+", q) if len(t) >= 3]
        if not toks:
            toks = ["course", "elective", "systems", "machine", "data", "design"]
        return toks

    def _loose_parse_course_text(self, s: str):
        import re as _re

        code = ""
        title = ""
        prereqs: List[str] = []
        score = 0.0
        m = _re.search(r"([A-Z]{2,}\s*\d{3,4})\s*(?:-\s*(.*))?$", s)
        if m:
            code = m.group(1).strip()
            if m.group(2):
                title = m.group(2).strip()
        return code, title, prereqs, score

    # ---------------------------------------------------------------
    # ---------------------- PREREQ EVALUATION ----------------------
    def _missing_prereqs(self, course_code: str, passed_codes: set, catalog_year: str = "",) -> list[str]:
        target = self._norm_code(course_code)
        if not target:
            return []
        

        # 🔍 DEBUG: confirm correct inputs
        passed_list = sorted(passed_codes or set())
        self.logger.debug(
            f"_missing_prereqs: target={target}, catalog_year={catalog_year}, "
            f"passed_count={len(passed_list)}, passed_sample={passed_list[:10]}"
        )


        # 1) Manual overrides take precedence
        override = self.prereq_overrides.get(target)
        if override is not None:
            prereqs = [self._norm_code(p) for p in override if str(p).strip()]
            passed_norm = {self._norm_code(c) for c in (passed_codes or set())}
            return [p for p in prereqs if p and p not in passed_norm]

        # 2) Otherwise: read from catalog
        prereqs: list[str] = []
        try:
            for row in self._iter_catalog_courses(catalog_year=catalog_year):
                if not isinstance(row, dict):
                    continue
                code = self._norm_code(row.get("course_code") or "")
                if code != target:
                    continue

                raw = row.get("prerequisites") or row.get("prereqs") or []
                if isinstance(raw, str):
                    prereqs = [self._norm_code(p) for p in raw.split(",") if p.strip()]
                elif isinstance(raw, (list, tuple, set)):
                    prereqs = [self._norm_code(p) for p in raw if str(p).strip()]
                else:
                    prereqs = []
                break
        except Exception as e:
            self.logger.exception(f"_missing_prereqs failed for {course_code}: {e}")
            return []

        passed_norm = {self._norm_code(c) for c in (passed_codes or set())}
        return [p for p in prereqs if p and p not in passed_norm]

    
    def _build_canonical_course_name_map(self, catalog_year: str, flattened_context: str, uploaded_name_map: dict[str, str] | None = None) -> dict[str, str]:
        """
        Priority:
        1) Course catalog (JSON) via _iter_catalog_courses(catalog_year)
        2) Program markdown tables parsed from flattened_context (catches XXXX courses)
        """
        name_map: dict[str, str] = {}

        # 0) Uploaded file mapping should win (it is what the student actually uploaded)
        if uploaded_name_map:
            for code, name in uploaded_name_map.items():
                c = self._norm_code(code)
                n = (name or "").strip()
                if c and n:
                    name_map[c] = n

        # 1) From course catalog JSON (best)
        for row in self._iter_catalog_courses(catalog_year=catalog_year):
            code = self._norm_code(row.get("course_code") or "")
            name = (row.get("course_name") or "").strip()
            if code and name and code not in name_map:
                name_map[code] = name


        # 2) From program markdown tables present in flattened_context (fallback)
        # flattened_context contains the program markdown you read from rag_corpus/.../programs/*.md
        prog_map = self._extract_course_names_from_program_markdown(flattened_context or "")
        for code, name in prog_map.items():
            c = self._norm_code(code)
            if c not in name_map and name:
                name_map[c] = name

        return name_map
    


    def _norm_code(self, code: str) -> str:
        """
        Normalize course codes to a canonical format:
        SUBJECT + space + NUMBER(+optional letter), e.g.
        'math1241' -> 'MATH 1241'
        'PHYS 2101L' -> 'PHYS 2101L'
        """
        if not code:
            return ""

        s = str(code).strip().upper()
        s = re.sub(r"\s+", "", s)   # remove all internal spaces first

        m = re.match(r"^([A-Z]{2,4})(\d{3,4}[A-Z]?)$", s)
        if m:
            return f"{m.group(1)} {m.group(2)}"

        # fallback: collapse multiple spaces if format is unusual
        return re.sub(r"\s+", " ", str(code).strip().upper())
    
    

    def _format_reqs(self, raw) -> str:
        """
        Converts prereq/coreq fields (list[str] or comma-separated string) into
        a clean human-readable string of normalized course codes.
        """
        if not raw:
            return ""

        # If it's already a string like "ITSC 1213, MATH 1241"
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            normed = [self._norm_code(p) for p in parts]
            normed = [c for c in normed if c]
            return ", ".join(normed)

        # If it's list/tuple/set
        if isinstance(raw, (list, tuple, set)):
            normed = []
            for p in raw:
                c = self._norm_code(str(p))
                if c:
                    normed.append(c)
            return ", ".join(normed)

        # Unknown format
        return str(raw).strip()

    
    def _apply_names(self, rows: List[Dict[str, str]], name_map: Dict[str, str]) -> List[Dict[str, str]]:
        """
        Replace/normalize course names in `rows` using `name_map` keyed by normalized course code.
        Keeps rows clean: {course_code, course_name, notes?}
        """
        fixed: List[Dict[str, str]] = []

        for r in (rows or []):
            code = self._norm_code(r.get("course_code") or "")
            if not code:
                continue

            # prefer canonical name if present
            name = (r.get("course_name") or "").strip()
            canonical = (name_map or {}).get(code)
            if canonical:
                name = canonical.strip()

            fixed.append(
                {
                    "course_code": code,
                    "course_name": name,
                    "notes": (r.get("notes") or "").strip(),  # safe even if missing
                }
            )

        return fixed

    
    def _is_pass_grade(self, grade: str) -> bool:
        if not grade:
            return False
        g = str(grade).strip().upper()
        return g in {"A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "P", "S"}

    def _grade_status(self, grade: str) -> str:
        g = str(grade or "").strip().upper()
        g = re.sub(r"[+-]$", "", g)

        if not g:
            return "unknown"

        if g in {"A", "B", "C", "D", "P", "S", "H"}:
            return "passed"

        if g in {"F", "I", "IP", "W", "WE", "U", "NR", "N", "AU"}:
            return "failed"

        return "unknown"

    def _lookup_course_name_fast(self, code: str, uploaded_name_map: Dict[str, str], catalog_year: str) -> str:
        c = self._norm_code(code)
        if not c:
            return ""

        # 1) Uploaded mapping wins
        if uploaded_name_map and c in uploaded_name_map:
            return (uploaded_name_map[c] or "").strip()

        # 2) Local catalog fallback (fast, no retrieval)
        for row in self._iter_catalog_courses(catalog_year=catalog_year):
            if self._norm_code(row.get("course_code") or "") == c:
                return (row.get("course_name") or "").strip()

        return ""
    
    def _lookup_course_details_fast(self, code: str, catalog_year: str) -> CourseDetails | None:
        target = self._norm_code(code)
        if not target:
            return None

        # Walk local catalog data (self.courses) for this catalog year
        for row in self._iter_catalog_courses(catalog_year=catalog_year):
            row_code = self._norm_code(row.get("course_code") or "")
            if row_code != target:
                continue

            # Title / name
            title = (row.get("course_name") or row.get("title") or "").strip()

            # Credits: handle common shapes (string/int/float)
            raw_credits = row.get("credits") or row.get("credit_hours") or ""
            if isinstance(raw_credits, (int, float)):
                credits = str(raw_credits)
            else:
                credits = str(raw_credits).strip()

            # Description
            description = (row.get("description") or row.get("course_description") or "").strip()

            # Prereqs/coreqs: normalize list or string into nice display text
            prereqs_list = row.get("prerequisites") or row.get("prereqs") or []
            coreqs_list  = row.get("corequisites") or row.get("coreqs") or []

            prereqs = self._format_reqs(prereqs_list)
            coreqs  = self._format_reqs(coreqs_list)

            return CourseDetails(
                code=target,
                title=title,
                credits=credits,
                description=description,
                prereqs=prereqs,
                coreqs=coreqs,
            )

        return None




    def _filter_suggestions_by_prereqs(
        self,
        suggestions: List[Dict[str, str]],
        passed_codes: Set[str],
        failed_codes: Set[str],
        catalog_year: str = "",
    ) -> List[Dict[str, str]]:

        filtered: List[Dict[str, str]] = []

        passed_norm = {self._norm_code(c) for c in (passed_codes or set())}
        failed_norm = {self._norm_code(c) for c in (failed_codes or set())}

        for row in suggestions:
            code = self._norm_code(row.get("course_code") or "")
            if not code:
                continue

            # ✅ NEW: never recommend already-passed courses
            if code in passed_norm:
                self.logger.info(f"Dropping {code}; already passed.")
                continue

            # Retake is allowed
            if code in failed_norm:
                filtered.append({**row, "course_code": code})
                continue

            missing = self._missing_prereqs(code, passed_norm, catalog_year)
            if missing:
                self.logger.info(f"Dropping {code}; missing prereqs: {missing}")
                continue

            filtered.append({**row, "course_code": code})

        return filtered

    
    def _filter_to_known_courses(
        self,
        rows: List[Dict[str, str]],
        known_codes: Set[str],
        failed_codes: Set[str],
    ) -> List[Dict[str, str]]:
        failed_norm = {self._norm_code(c) for c in (failed_codes or set())}
        out = []
        for r in rows or []:
            code = self._norm_code(r.get("course_code") or "")
            if not code:
                continue
            if code in failed_norm or code in known_codes:
                out.append({**r, "course_code": code})
            else:
                self.logger.info(f"Dropping unknown course suggestion: {code}")
        return out


    # ------------------------------------------------
    # Catalog iterator (with catalog_year filtering)
    # ------------------------------------------------
    

    # inside class Chatbot
    def _iter_catalog_courses(self, catalog_year: str = ""):
        """
        Yield normalized catalog entries with keys:
          course_code: str
          course_name: str
          prerequisites: list[str]
          catalog_year: str (if available)

        Assumes self.courses is the object returned by load_courses(logger).

        If catalog_year is provided and the row has a matching "catalog_year"
        field, we only yield rows for that year. Otherwise, all rows are yielded.
        """

        if not self.courses:
            return

        # ---- Case 1: Pandas DataFrame (future-proof) ----
        try:
            import pandas as _pd

            if isinstance(self.courses, _pd.DataFrame):
                df = self.courses

                # Optional filter by catalog_year if the column exists
                if catalog_year and "catalog_year" in df.columns:
                    df = df[
                        df["catalog_year"].astype(str).str.strip()
                        == str(catalog_year).strip()
                    ]

                for _, row in df.iterrows():
                    code = self._norm_code(row.get("course_code",""))
                    name = str(row.get("course_name", "")).strip()

                    raw = row.get("prerequisites", []) or row.get("prereqs", [])
                    if isinstance(raw, str):
                        prereqs = [self._norm_code(str(p)) for p in raw.split(",") if str(p).strip()]
                    elif isinstance(raw, (list, tuple, set)):
                        prereqs = [self._norm_code(str(p)) for p in raw if str(p).strip()]
                    else:
                        prereqs = []
                       
                    out = dict(row)  # keep everything
                    out["course_code"] = code
                    out["course_name"] = name
                    out["prerequisites"] = prereqs
                    out["catalog_year"] = row.get("catalog_year", "")
                    yield out

                return

        except Exception:
            # If pandas is not available or something goes wrong, fall through
            pass
        

        # ---- Case 2: dict[str, dict] (actual current load_courses format) ----
        if isinstance(self.courses, dict):
            for _, row in self.courses.items():
                if not isinstance(row, dict):
                    continue

                # Allow slight flexibility in catalog year
                def _normalize_year(y: str) -> str:
                    return str(y).replace("–", "-").strip()

                if catalog_year:
                    row_year = _normalize_year(row.get("catalog_year", ""))
                    req_year = _normalize_year(catalog_year)

                    # Only filter if row actually has a year
                    if row_year and row_year != req_year:
                        continue

                code = self._norm_code(row.get("course_code") or "")
                name = str(row.get("course_name", "")).strip()

                raw = row.get("prerequisites", []) or row.get("prereqs", [])
                if isinstance(raw, str):
                    prereqs = [self._norm_code(str(p)) for p in raw.split(",") if str(p).strip()]
                elif isinstance(raw, (list, tuple, set)):
                    prereqs = [self._norm_code(str(p)) for p in raw if str(p).strip()]
                else:
                    prereqs = []

                yield {
                    "course_code": code,
                    "course_name": name,
                    "prerequisites": prereqs,
                    "catalog_year": row.get("catalog_year", ""),
                    **row,
                }
            return
        

        # ---- Case 3: list[dict] (this is your actual load_courses format) ----
        if isinstance(self.courses, list):
            for row in self.courses:
                if not isinstance(row, dict):
                    continue

                def _normalize_year(y: str) -> str:
                    return str(y).replace("–", "-").strip()

                if catalog_year:
                    row_year = _normalize_year(row.get("catalog_year", ""))
                    req_year = _normalize_year(catalog_year)

                    if row_year and row_year != req_year:
                        continue

                code = self._norm_code(row.get("course_code") or "")
                name = str(row.get("course_name", "")).strip()

                raw = row.get("prerequisites", []) or row.get("prereqs", [])
                if isinstance(raw, str):
                    prereqs = [self._norm_code(str(p)) for p in raw.split(",") if str(p).strip()]
                elif isinstance(raw, (list, tuple, set)):
                    prereqs = [self._norm_code(str(p)) for p in raw if str(p).strip()]
                else:
                    prereqs = []


                yield {
                    "course_code": code,
                    "course_name": name,
                    "prerequisites": prereqs,
                    "catalog_year": row.get("catalog_year", ""),
                    **row,
                }

        # (If self.courses is some other unexpected type, we just yield nothing.)



    # ------------------------------------------------
    # Structured suggestion engine
    # ------------------------------------------------
    
    def _suggest_courses_structured(
        self,
        passed_codes: Set[str],
        failed_codes: Set[str],
        catalog_year: str = "",
        max_results: int = 12,
        preferred_codes: List[str] | None = None,
    ) -> List[Dict[str, str]]:
        """
        Ranked rule-based suggestion engine.

        Priority:
        1. Retakes
        2. Fully eligible, high-value major courses
        3. Near-eligible courses
        """

        passed_norm = {self._norm_code(c) for c in (passed_codes or set()) if c}
        failed_norm = {self._norm_code(c) for c in (failed_codes or set()) if c}

        preferred_set = {self._norm_code(c) for c in (preferred_codes or []) if c}

        ranked_rows: List[Dict[str, Any]] = []
    

        def score_course(code: str, name: str, missing: List[str], is_retake: bool) -> int:
            score = 0
            name_lower = (name or "").lower()

            if code in preferred_set:
                score += 80

            # 1) Retakes are highest priority
            if is_retake:
                score += 100

            # 2) Fully eligible vs near-eligible
            if not missing:
                score += 50
            elif len(missing) == 1:
                score += 20
            elif len(missing) == 2:
                score += 10
            else:
                score -= 50

            # 3) Prefer major-relevant subjects
            if code.startswith("MEGR"):
                score += 25
            elif code.startswith("ECGR"):
                score += 15
            elif code.startswith("MATH") or code.startswith("PHYS"):
                score += 8

            # 4) Boost known “unlocker” / progression courses
            important_keywords = [
                "thermodynamics",
                "materials",
                "computational methods",
                "motorsports",
                "design",
                "dynamics",
                "fluids",
                "heat transfer",
            ]
            for kw in important_keywords:
                if kw in name_lower:
                    score += 10

            # 5) Small penalty for missing prerequisites
            score -= len(missing) * 5

            return score

        # ---- Retakes first ----
        for code in sorted(failed_norm):
            if code:
                ranked_rows.append({
                    "course_code": code,
                    "course_name": "",
                    "notes": "Retake recommended",
                    "_score": score_course(code, "", [], True),
                })

        
        # ---- Walk catalog ----
        for row in self._iter_catalog_courses(catalog_year):
            code = self._norm_code(row.get("course_code") or "")
            name = (row.get("course_name") or "").strip()

            raw_prereqs = row.get("prerequisites") or []
            prereqs = [self._norm_code(p) for p in raw_prereqs if str(p).strip()]
            prereqs = list(dict.fromkeys(p for p in prereqs if p))

            if not code:
                continue

            if code in passed_norm:
                continue

            if code in failed_norm:
                continue

            missing = [p for p in prereqs if p not in passed_norm]

            if len(missing) > 1:
                continue

            if not missing:
                notes = "Eligible now"
            else:
                notes = f"Missing prerequisite: {missing[0]} (plan for upcoming semesters)"

            ranked_rows.append({
                "course_code": code,
                "course_name": name,
                "notes": notes,
                "_score": score_course(code, name, missing, False),
            })

            self.logger.info(
                f"Candidate={code} | prereqs={prereqs} | missing={missing} | score={score_course(code, name, missing, False)} | catalog_year={catalog_year}"
            )

        # ---- Sort by score descending, then code ----
        ranked_rows.sort(key=lambda r: (-r["_score"], r.get("course_code", "")))

        # ---- De-dupe by course_code preserving highest ranked ----
        seen = set()
        deduped = []
        for r in ranked_rows:
            c = r.get("course_code", "")
            if c and c not in seen:
                seen.add(c)
                deduped.append({
                    "course_code": c,
                    "course_name": r.get("course_name", ""),
                    "notes": r.get("notes", ""),
                })

        self.logger.info(
            f"ranked_suggestions total={len(deduped)} top={[r['course_code'] for r in deduped[:10]]}"
        )



        return deduped[:max_results]
    
    def _extract_degree_plan_codes(self, text: str) -> List[str]:
        if not text:
            return []

        codes = []
        for m in self.COURSE_CODE_RE.finditer(text.upper()):
            code = self._norm_code(m.group(0))
            if code and code not in codes:
                codes.append(code)

        return codes

    # ------------------------------------------------
    # (Optional) candidate summary for LLM (unused now)
    # ------------------------------------------------
    def _summarize_candidates_for_llm(
        self, candidates: List[Dict[str, Any]], passed_codes: Set[str], failed_codes: Set[str], catalog_year: str = "",
    ) -> str:
        """
        Bucket & rank candidates, then build a compact text summary.
        """
        retake: List[Dict[str, Any]] = []
        eligible: List[Dict[str, Any]] = []
        needs: List[Dict[str, Any]] = []

        for c in candidates:
            code = c["course_code"].upper()
            if not code:
                continue

            if code in failed_codes:
                retake.append({**c, "reason": "previously failed/withdrawn"})
                continue

            if code in passed_codes:
                continue

            missing = self._missing_prereqs(code, passed_codes, catalog_year)
            if missing:
                needs.append({**c, "missing_prereqs": missing})
            else:
                eligible.append(c)

        def _level(code: str) -> int:
            import re as _re

            m = _re.search(r"(\d{3,4})", code)
            return int(m.group(1)) if m else 0

        retake.sort(key=lambda x: -x.get("score", 0))
        eligible.sort(key=lambda x: (_level(x["course_code"]), -x.get("score", 0)))
        needs.sort(key=lambda x: -x.get("score", 0))

        retake = retake[:5]
        eligible = eligible[:8]
        needs = needs[:6]

        lines: List[str] = []
        if retake:
            lines.append("Retake candidates (previously failed/withdrawn):")
            for c in retake:
                lines.append(
                    f"- {c['course_code']} {('— ' + c.get('title','')) if c.get('title') else ''}"
                )
            lines.append("")

        lines.append("Eligible next-course candidates:")
        if eligible:
            for c in eligible:
                lines.append(
                    f"- {c['course_code']} {('— ' + c.get('title','')) if c.get('title') else ''}"
                )
        else:
            lines.append("- (none)")
        lines.append("")

        if needs:
            lines.append("Courses that require missing prerequisites:")
            for c in needs:
                missing = ", ".join(c.get("missing_prereqs", []))
                lines.append(
                    f"- {c['course_code']} "
                    f"{('— ' + c.get('title','')) if c.get('title') else ''}: "
                    f"missing {missing}"
                )
            lines.append("")

        lines.append(
            "Advising rules: prefer retakes first if timely; otherwise pick eligible "
            "courses that progress the degree, respect prerequisites, and avoid duplicates."
        )

        return "\n".join(lines)
=======
import os
import time
import re
from typing import List, Dict, Set, Any

from pydantic import BaseModel
from pydantic import Field

from src.retrieval import openai_extract_vector


from src.retrieval import vector_query, load_courses
from src.retrieval import rerank_and_filter_candidates  # currently unused, but keep for future work


# TODO differentiate information requests based on the storage retrieval mode
system_prompt_planning_common_component = """
# Your Role
You are a prompt analyst for a chatbot system that provides information about academic programs and courses and helps students navigate their academic journey. 
You do not respond directly to user prompts. 
Instead, you analyze user prompts to summarize and clarify the intent behind user prompts, then determine what information (if any) will be requested to help another agent 
generate the best possible responses.

# Your Response Format
Your entire response is contained within two sections delimited by XML tags: 
(1) an analytical summary of the user prompt between <Analytical_Summary> and </Analytical_Summary> 
and (2) a list of information requests between <Retrieval> and </Retrieval> tags.

## Analytical Summary of Prompt:
First, provide a summary of the intent of the user prompt in the <Analytical_Summary> section of your response, taking into account previous prompts and responses.
Include any important keywords whether the user prompt did so or not. Provide this analytical summary between  tags.
In this section, consider whether the user is asking about a specific course, a program of study, a general question about the catalog,  a question about potential careers, or something else.  
"""

system_prompt_planning_retrieval_mode_component: Dict[str, str] = {}
planning_response_validation_pattern: Dict[str, str] = {}

# System prompt for most advanced storage and retrieval mode 
system_prompt_planning_retrieval_mode_component["0"] = """
## Information Retrieval Requests
Between <Retrieval> and </Retrieval> tags, submit up to three pairs of the following tags to request additional information from internal repositories.  Don't request information unless it will be useful to generate a better response.  

<Specific_Request_Current_Major> Request information about the student's current degree program, including requirements, courses, and other relevant details, for the student's catalog year. Place no characters between these tags. </Specific_Request_Current_Major>
<Specific_Request_Current_Major_Sample_Schedules> Request example schedules for the student's major and catalog year.  Request this when the user prompt is about scheduling courses or planning a semester or to understand how the program usually flows. Place no characters between these tags.  </Specific_Request_Current_Major_Sample_Schedules>
<Semantic_Request_Programs> Semantic search for degree programs related to the request, such as majors and concentrations, minors, and early graduate programs. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags.  </Semantic_Request_Programs>
<Semantic_Request_Courses> - Semantic search for courses related to user prompt; includes course names, codes, descriptions, and prerequesites and corequisites. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags. </Semantic_Request_Courses>
<Semantic_Request_Support_Resources> - Semantic search for support resources related to user prompt, such as advising, tutoring, career services, and mental health resources. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags. </Semantic_Request_Support_Resources>

Each XML tag that is used must be reproduced verbatim, and the text between the tags must be replaced as directed.

"""

planning_response_validation_pattern["0"] = r"<Analytical_Summary>.*</Analytical_Summary>.*<Retrieval>.*</Retrieval>"


system_prompt_generation_common_component = """# Your Role
You generate responses as part of a chatbot system that provides information to undergraduate university students about academic programs and courses and helps those students navigate their academic journey.

# Your Response Style and Priorities
Your response should be informative, friendly, helpful, and concise yet thorough using good word economy.
If the user does not provide enough information to provide an accurate, relevant, and complete response, you ask follow-up questions to clarify their request before answering questions.
Unless the question is very simple and the response can be found in the provided context, encourage the student to speak with their advisor to help ensure their academic success.  
You never provide specific facts about a UNC Charlotte degree program, course, or university policy unless the information is provided in the provided contextual information.

# Prerequisite and Corequisite Rules (IMPORTANT – FOLLOW STRICTLY)

You must apply these rules whenever referring to prerequisites and course eligibility:

1. Prerequisites:
   - A prerequisite must be fully completed and passed BEFORE taking the next course.
   - A student may NOT take a course in the same semester as its prerequisite.
   - Do NOT suggest or imply that a student can take a course “along with” or “in parallel with” its prerequisite.
   - If a course has an unmet prerequisite, label it as NOT eligible and do not recommend it.

2. Corequisites:
   - A corequisite may be taken in the SAME semester as its partner course.
   - You may say “take alongside _____” only when the catalog explicitly states the requirement is a corequisite.

3. If the catalog context does not clearly specify a corequisite:
   - Assume it is a prerequisite only.
   - Treat it as NOT allowed to take concurrently.

Always follow these rules strictly in all recommendations, explanations, and Notes fields in the final course table.

# Required Final Section – "Courses for next semester"
Whenever you recommend specific courses for the student to take in an upcoming semester, you MUST finish your answer with a separate section titled exactly:

## Courses for next semester

Immediately under that heading, include a markdown table with **three columns** in this order:

| Course Code | Course Name | Notes |

The first row must be the header row shown above, the second row must be the separator row (using ---), and subsequent rows list one course per line. Example:

## Courses for next semester

| Course Code | Course Name | Notes |
| --- | --- | --- |
| MATH 1241 | Calculus I | Required foundation; take as soon as possible. |
| PHYS 2101 | Physics I | Take after or with Calculus I. |

Notes should briefly explain *why* the course is recommended (e.g., “required for major”, “prerequisite for X”, “good technical elective”, “retake due to previous F”, etc.).

If, for a particular user question, you **do not** want to recommend any specific courses, still include the heading and table header, but add one row like:

| Course Code | Course Name | Notes |
| --- | --- | --- |
| — | — | No specific course recommendations for this question. |

Do NOT invent fake codes. Always use real course codes that appear in the provided context, or use “—” if you cannot safely recommend a code.

Important constraints for the table:

- Only include a course in the "Courses for next semester" table if EITHER
  (a) the student has already completed all of its prerequisites with a passing grade, OR
  (b) it is a retake of a course the student previously failed/withdrew from.
- If a course requires a prerequisite the student has not yet passed (for example,
  PHYS 2102 requiring MATH 1242, when MATH 1242 is currently failed), then you MUST
  NOT list that course in the "Courses for next semester" table. You may discuss it
  in narrative text as a future option, but not in the table.

"""


# ----------------------------------------------------
# API Models for Chatbot Requests and Responses
# ----------------------------------------------------
class ChatRequest(BaseModel):
    conversation_history : List = Field(default_factory=list)
    user_prompt_text: str = Field(..., min_length=1, max_length=1000)
    student_catalog_year: str = Field(..., min_length=1, max_length=9)
    student_degree_program: str = Field(..., min_length=1, max_length=120)
    # allow empty string:
    student_credits_earned: str = Field("", min_length=0, max_length=40)
    pursued_courses: List[str] = Field(default_factory=list)
    pursued_courses_detailed: List[Dict[str, str]] = Field(default_factory=list)  # {course_code, course_name, grade}


class ChatResponse(BaseModel):
    error_code: int = 0
    chat_response_content: str = Field(..., min_length=1, max_length=10000)
    analytical_summary: str = Field(..., min_length=1, max_length=10000)
    information_requests: str = Field(..., min_length=0, max_length=1000)
    retrieved_context: Dict[str, List] = Field(default_factory=dict)
    flattened_context: str = Field(..., min_length=0, max_length=120000)
    planning_generation_time_required: float = Field(..., ge=0)
    retrieval_time_required: float = Field(..., ge=0)
    chat_response_generation_time_required: float = Field(..., ge=0)
    planning_attempts: int = Field(..., ge=0)
    planning_input_tokens: int = Field(..., ge=0)
    planning_output_tokens: int = Field(..., ge=0)
    chat_response_input_tokens: int = Field(..., ge=0)
    chat_response_output_tokens: int = Field(..., ge=0)
    # Clean structured suggestions for frontend/export
    suggested_courses: List[Dict[str, str]] = Field(default_factory=list)   # [{"course_code": "...", "course_name": "...", "notes": "..."}]

    # 🔹 NEW: markdown export for this single prompt/answer
    export_markdown: str = ""


# ----------------------------------------------------
# Chat request handler
# ----------------------------------------------------
class Chatbot:
    def __init__(
        self,
        STORAGE_RETRIEVAL_MODE,
        generation_client,
        PLANNING_MODEL_ID,
        GENERATION_MODEL_ID,
        embedding_client,
        EMBEDDING_MODEL_NAME,
        ENCODING_FORMAT,
        db_client,
        COLLECTION_NAME_DEGREE_PROGRAMS,
        COLLECTION_NAME_COURSES,
        logger,
    ):
        self.STORAGE_RETRIEVAL_MODE = STORAGE_RETRIEVAL_MODE
        self.generation_client = generation_client
        self.PLANNING_MODEL_ID = PLANNING_MODEL_ID
        self.GENERATION_MODEL_ID = GENERATION_MODEL_ID
        self.embedding_client = embedding_client
        self.EMBEDDING_MODEL_NAME = EMBEDDING_MODEL_NAME
        self.ENCODING_FORMAT = ENCODING_FORMAT
        self.db_client = db_client
        self.COLLECTION_NAME_DEGREE_PROGRAMS = COLLECTION_NAME_DEGREE_PROGRAMS
        self.COLLECTION_NAME_COURSES = COLLECTION_NAME_COURSES
        self.logger = logger

        # Load courses from local corpus
        self.courses = load_courses(logger)

        # Optional manual prerequisite overrides (codes must be UPPERCASE)
        # Example:
        # self.prereq_overrides = {
        #     "ENGR 1202": ["ENGR 1201", "MATH 1241"],
        # }
        self.prereq_overrides: Dict[str, List[str]] = {}

    # ------------------------------------------------
    # Main chat handler
    # ------------------------------------------------
    def chat(self, chat_request: ChatRequest) -> ChatResponse:
        # --- Build student context with grade-aware classification ---
        detailed = getattr(chat_request, "pursued_courses_detailed", []) or []
        legacy_list = chat_request.pursued_courses or []

        # Fallback: parse legacy string list if detailed is empty
        if not detailed and legacy_list:
            import re as _re

            patt = _re.compile(
                r"^\s*([A-Z]{2,}\s*\d{3,4})\s*(?:-\s*(.*?))?(?:\s*\(GRADE:\s*([A-Z+\-]+)\))?\s*$",
                _re.I,
            )
            parsed = []
            for s in legacy_list:
                m = patt.match(str(s))
                if m:
                    code = (m.group(1) or "").strip()
                    name = (m.group(2) or "").strip()
                    grade = (m.group(3) or "").strip().upper()
                    parsed.append(
                        {
                            "course_code": code,
                            "course_name": name,
                            "grade": grade,
                        }
                    )
                else:
                    parsed.append(
                        {
                            "course_code": str(s).strip(),
                            "course_name": "",
                            "grade": "",
                        }
                    )
            detailed = parsed

        # Decide pass/fail (retake when failed/withdrawn)
        def _is_pass(grade: str) -> bool:
            if not grade:
                # Unknown grade → treat as passed to avoid false retake suggestions
                return True
            g = grade.upper()
            if g in {"A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "P", "S"}:
                return True
            if g in {"F", "W", "WF", "U", "I", "NP", "NC"}:
                return False
            return True  # conservative

        passed_codes = {
            self._norm_code(r.get("course_code", ""))
            for r in detailed
            if _is_pass(r.get("grade", ""))
        }

        failed_codes = {
            self._norm_code(r.get("course_code", ""))
            for r in detailed
            if not _is_pass(r.get("grade", ""))
        }


        # Prepare system context for the LLM
        student_context: List[str] = []
        if chat_request.student_degree_program:
            student_context.append(f"Program: {chat_request.student_degree_program}")
        if chat_request.student_catalog_year:
            student_context.append(f"Catalog Year: {chat_request.student_catalog_year}")
        if chat_request.student_credits_earned:
            student_context.append(f"Credits: {chat_request.student_credits_earned}")
        if passed_codes:
            student_context.append(
                "Completed (passed): " + ", ".join(sorted(c for c in passed_codes if c))
            )
        if failed_codes:
            student_context.append(
                "Failed/Withdrawn (needs retake): "
                + ", ".join(sorted(c for c in failed_codes if c))
            )

        system_hint = (
            "You are an academic advisor. Recommend courses the student has NOT passed. "
            "If a course was failed/withdrawn, prioritize suggesting an appropriate retake "
            "when it fits prerequisites and program flow. Respect prerequisites and avoid duplicates."
        )

        system_messages = [
            {"role": "system", "content": system_hint},
            {"role": "system", "content": "\n".join(student_context)},
        ]

        started_processing = time.time()
        self.logger.info(f"Processing prompt request: {chat_request.user_prompt_text}")
        self.logger.info(f"Conversation history: {chat_request.conversation_history}")
        self.logger.info(
            f"Student degree program: {chat_request.student_degree_program}"
        )
        self.logger.info(
            f"Student catalog year: {chat_request.student_catalog_year}"
        )
        self.logger.info(
            f"Student credits earned: {chat_request.student_credits_earned}"
        )

        # --- PREPARE CONTEXT (planning + retrieval) ---
        (
            analytical_summary,
            information_requests,
            retrieved_context,
            flattened_context,
            planning_attempts,
            planning_completed_time,
            planning_input_tokens,
            planning_output_tokens,
            embedding_tokens,
        ) = self.prepare_context(chat_request=chat_request)

        retrieval_completed_time = time.time()

        # --- Build structured suggestions from catalog + grades + catalog year ---
        structured_suggestions = self._suggest_courses_structured(
            passed_codes=passed_codes,
            failed_codes=failed_codes,
            catalog_year=chat_request.student_catalog_year,
            max_results=12,
        )

        # --- Grade-aware helper summary for the LLM ---
        summary_lines: List[str] = []

        if failed_codes:
            summary_lines.append(
                "Retake opportunities detected (student previously failed/withdrew):"
            )
            for code in sorted(c for c in failed_codes if c):
                summary_lines.append(f"- {code}")
            summary_lines.append("")

        summary_lines.append(
            "Eligible next-course candidates (already prereq-filtered from catalog):"
        )
        if structured_suggestions:
            for row in structured_suggestions:
                summary_lines.append(
                    f"- {row['course_code']} ({row.get('course_name', '')})"
                )
        else:
            summary_lines.append("- (none found)")

        summary_for_llm = "\n".join(summary_lines)
        self.logger.info("LLM summary hint:\n" + summary_for_llm)

        # --- Final message stack for generation ---
        messages = system_messages + [
            {
                "role": "system",
                "content": system_prompt_generation_common_component + flattened_context,
            },
            {"role": "system", "content": summary_for_llm},
            {"role": "user", "content": chat_request.user_prompt_text},
        ]

        # --- GENERATE CHAT RESPONSE ---
        try:
            chat_response = self.generation_client.chat.completions.create(
                model=self.GENERATION_MODEL_ID,
                messages=messages,
            )
            chat_response_content = chat_response.choices[0].message.content
            chat_prompt_tokens = (
                getattr(getattr(chat_response, "usage", None), "prompt_tokens", 0) or 0
            )
            chat_completion_tokens = (
                getattr(getattr(chat_response, "usage", None), "completion_tokens", 0)
                or 0
            )
        except Exception as e:
            self.logger.error(f"Error generating response: {e}")
            raise

        generation_completed_time = time.time()
        self.logger.info(
            f"Generated response obtained: {chat_response_content[:500]}..."
        )

        # --- Parse suggested courses table from the LLM response ---
        try:
            parsed_from_llm = self._parse_suggested_courses_from_response(chat_response_content)
            self.logger.info(f"Parsed {len(parsed_from_llm)} suggested courses from response table.")
        except Exception:
            self.logger.exception("Failed to parse suggested courses table.")
            parsed_from_llm = []

        # 1) Filter LLM table suggestions by prerequisites (retakes allowed)
        filtered_llm = self._filter_suggestions_by_prereqs(
            parsed_from_llm,
            passed_codes=passed_codes,
            failed_codes=failed_codes,
        )

        # 2) Build canonical course-name map (catalog JSON first, program markdown fallback)
        name_map = self._build_canonical_course_name_map(
            catalog_year=chat_request.student_catalog_year,
            flattened_context=flattened_context,
        )

        def _apply_names(rows):
            fixed = []
            for r in (rows or []):
                code = self._norm_code(r.get("course_code") or "")
                name = (r.get("course_name") or "").strip()
                canonical = name_map.get(code)
                if canonical:
                    name = canonical

                fixed.append({
                    "course_code": code,
                    "course_name": name,
                    "notes": (r.get("notes") or "").strip(),
                })
            return fixed



        # 3) Apply canonical names
        filtered_llm = _apply_names(filtered_llm)
        structured_suggestions = _apply_names(structured_suggestions)

        # 4) Final fallback decision
        final_suggestions = filtered_llm or structured_suggestions




        # --- Build Markdown export for this prompt (for professor + frontend) ---
        from datetime import datetime

        ts_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        md_lines = [
            "# Niner Pathfinder – Prompt Run",
            "",
            f"- Timestamp: {ts_utc}",
            f"- Catalog Year: {chat_request.student_catalog_year}",
            f"- Degree Program: {chat_request.student_degree_program}",
            f"- Credits Earned: {chat_request.student_credits_earned}",
            "",
            "## Prompt",
            "",
            chat_request.user_prompt_text,
            "",
            "## LLM Answer",
            "",
            chat_response_content,
            "",
            "## Parsed course suggestions (after prerequisite checks)",
            "",
            "| Course Code | Course Name | Notes |",
            "| --- | --- | --- |",
        ]

        if final_suggestions:
            for row in final_suggestions:
                md_lines.append(
                    f"| {row.get('course_code','')} | "
                    f"{row.get('course_name','')} | "
                    f"{row.get('notes','')} |"
                )
        else:
            md_lines.append(
                "| — | — | No parsed suggestions for this prompt. |"
            )

        export_markdown = "\n".join(md_lines)

        # Also write out a file on the backend for professor inspection
        try:
            safe_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            export_dir = os.path.join("exports", "chat_runs")
            os.makedirs(export_dir, exist_ok=True)
            filename = f"chat_{safe_ts}.md"
            filepath = os.path.join(export_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(export_markdown)
            self.logger.info(f"Wrote per-prompt export to {filepath}")
        except Exception as e:
            self.logger.error(f"Failed to write export markdown file: {e}")


        # --- Timings ---
        planning_generation_time_required = planning_completed_time - started_processing
        retrieval_time_required = retrieval_completed_time - planning_completed_time
        chat_response_generation_time_required = (
            generation_completed_time - retrieval_completed_time
        )

        self.logger.info(
            f"Planning time: {planning_generation_time_required:.2f}s"
        )
        self.logger.info(f"Retrieval time: {retrieval_time_required:.2f}s")
        self.logger.info(
            f"Generation time: {chat_response_generation_time_required:.2f}s"
        )

        return ChatResponse(
            error_code=0,
            chat_response_content=chat_response_content,
            analytical_summary=analytical_summary,
            information_requests=information_requests,
            retrieved_context=retrieved_context,
            flattened_context=flattened_context,
            planning_attempts=planning_attempts,
            planning_generation_time_required=planning_generation_time_required,
            retrieval_time_required=retrieval_time_required,
            chat_response_generation_time_required=chat_response_generation_time_required,
            planning_input_tokens=planning_input_tokens,
            planning_output_tokens=planning_output_tokens,
            chat_response_input_tokens=chat_prompt_tokens,
            chat_response_output_tokens=chat_completion_tokens,
            suggested_courses=final_suggestions,
            # 🔹 NEW
            export_markdown=export_markdown,
        )

    # ------------------------------------------------
    # Context preparation (planning + retrieval)
    # ------------------------------------------------
    def prepare_context(self, chat_request: ChatRequest):
        # Student info for context
        student_info_from_ui_text = f"""# Student Information:     
            The student has provided the following information via dropdowns in the user interface:
            Student catalog year: {chat_request.student_catalog_year}.
            Student degree program: {chat_request.student_degree_program}.
            Student has earned: {chat_request.student_credits_earned}.
            """

        system_prompt_planning = (
            system_prompt_planning_common_component
            + system_prompt_planning_retrieval_mode_component[
                self.STORAGE_RETRIEVAL_MODE
            ]
            + student_info_from_ui_text
        )

        message_history_analysis = chat_request.conversation_history.copy()
        message_history_analysis.extend(
            [
                {"role": "system", "content": system_prompt_planning},
                {"role": "user", "content": chat_request.user_prompt_text},
            ]
        )

        # --- Safe planning call with retries ---
        planning_response = None
        planning_attempts = 0
        planning_response_content = ""

        while planning_attempts < 3:
            planning_attempts += 1
            try:
                planning_response = self.generation_client.chat.completions.create(
                    model=self.PLANNING_MODEL_ID,
                    messages=message_history_analysis,
                    temperature=0.0,
                    max_tokens=1000,
                    top_p=0.01,
                )
                planning_response_content = (
                    planning_response.choices[0].message.content or ""
                )
            except Exception as e:
                self.logger.error(f"Planning generation call error: {e}")
                planning_response = None
                planning_response_content = ""

            self.logger.info(
                f"Planning attempt {planning_attempts} of 3: "
                f"{planning_response_content[:300]}..."
            )

            pattern = planning_response_validation_pattern[
                self.STORAGE_RETRIEVAL_MODE
            ]
            planning_pattern_match = bool(
                re.search(pattern, planning_response_content, re.DOTALL)
            )

            if planning_pattern_match:
                self.logger.info(
                    f"Planning response matches expected format for mode {self.STORAGE_RETRIEVAL_MODE}."
                )
                break
            elif planning_attempts < 3:
                self.logger.error(
                    f"Planning response did not match expected format "
                    f"(mode {self.STORAGE_RETRIEVAL_MODE}). Retrying..."
                )
                planning_response_content = ""
            else:
                raise ValueError(
                    "Planning response did not match expected format after 3 attempts "
                    f"(mode {self.STORAGE_RETRIEVAL_MODE})."
                )

        analytical_summary = (
            planning_response_content.split("</Analytical_Summary>")[0]
            .split("<Analytical_Summary>")[1]
            .strip()
        )
        information_requests = (
            planning_response_content.split("</Retrieval>")[0]
            .split("<Retrieval>")[1]
            .strip()
        )

        planning_input_tokens = (
            getattr(getattr(planning_response, "usage", None), "prompt_tokens", 0) or 0
        )
        planning_output_tokens = (
            getattr(getattr(planning_response, "usage", None), "completion_tokens", 0)
            or 0
        )

        planning_complete = time.time()

        self.logger.info(f"User prompt text: {chat_request.user_prompt_text}")
        self.logger.info(f"Analytical Summary: {analytical_summary}")
        self.logger.info(f"Information Requests: {information_requests}")

        embedding_tokens = 0
        if self.STORAGE_RETRIEVAL_MODE == "0":
            retrieved_context, embedding_tokens = self.retrieve_context_basic(
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                information_requests=information_requests,
            )
        elif self.STORAGE_RETRIEVAL_MODE == "1":
            # Placeholder: advanced retrieval mode
            retrieved_context = self.retrieve_context_next(
                user_prompt_text=chat_request.user_prompt_text,
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                student_credits_earned=chat_request.student_credits_earned,
                analytical_summary=analytical_summary,
                information_requests=information_requests,
            )
            embedding_tokens = 0
        else:
            retrieved_context = {}

        # Logging
        for context_type, context_items in retrieved_context.items():
            if context_items and isinstance(context_items, str):
                self.logger.info(
                    f"Retrieved context type: {context_type}, "
                    f"content length: {len(context_items)}"
                )
                self.logger.info(
                    f"Retrieved context item content: {context_items[:50]}..."
                )
                self.logger.error("Retrieved context item is a string, not a list!")
            elif (
                context_items
                and isinstance(context_items, list)
                and len(context_items) > 0
                and isinstance(context_items[0], str)
            ):
                self.logger.info(
                    f"Retrieved context type: {context_type}, "
                    f"number of items: {len(context_items)}"
                )
                for item in context_items:
                    self.logger.info(
                        f"Retrieved context item {len(item)}, content: {item[:50]}..."
                    )

        # Flatten the retrieved context into a single string
        flattened_context = student_info_from_ui_text

        for information_type_tag, information in retrieved_context.items():
            # information_type_tag is something like "<Semantic_Request_Courses>"
            tag_name = str(information_type_tag).strip().strip("<>").strip("/")

            # Proper open tag
            flattened_context += f"\n### Context: {tag_name}\n<{tag_name}>\n"

            # Content
            for item in (information or []):
                if isinstance(item, str):
                    flattened_context += f"{item}\n\n"
                elif isinstance(item, dict):
                    flattened_context += f"{str(item)}\n\n"
                else:
                    flattened_context += f"{str(item)}\n\n"

            # Proper close tag
            flattened_context += f"</{tag_name}>\n"


        self.logger.info(
            f"\n  Flattened context characters: {len(flattened_context)}, "
            f"\n  Content: {flattened_context[:500]}..."
        )

        return (
            analytical_summary,
            information_requests,
            retrieved_context,
            flattened_context,
            planning_attempts,
            planning_complete,
            planning_input_tokens,
            planning_output_tokens,
            embedding_tokens,
        )

    # ------------------------------------------------
    # Retrieval
    # ------------------------------------------------
    def retrieve_context_basic(
        self,
        student_catalog_year: str,
        student_degree_program: str,
        information_requests: str,
    ):
        retrieved_context: Dict[str, List[Any]] = {}
        embedding_tokens = 0

        try:
            # Specific sample schedules
            if (
                information_requests.find(
                    "<Specific_Request_Current_Major_Sample_Schedules>"
                )
                != -1
            ):
                sample_schedule_file_path = os.path.join(
                    "rag_corpus",
                    "sample_schedules",
                    student_catalog_year,
                    f"{student_degree_program}.md"
                )

                if os.path.exists(sample_schedule_file_path):
                    with open(sample_schedule_file_path, "r") as file:
                        retrieved_context[
                            "<Specific_Request_Current_Major_Sample_Schedules>"
                        ] = [file.read()]
                else:
                    self.logger.warning(
                        "Sample schedule file not found for "
                        f"{student_degree_program} {student_catalog_year}."
                    )

            # Specific current major description
            if information_requests.find("<Specific_Request_Current_Major>") != -1:
                current_major_file_path = os.path.join(
                    "rag_corpus",
                    "ug_cat",
                    student_catalog_year,
                    "programs",
                    student_degree_program + ".md",
                )

                if os.path.exists(current_major_file_path):
                    with open(current_major_file_path, "r") as file:
                        retrieved_context["<Specific_Request_Current_Major>"] = [
                            file.read()
                        ]
                else:
                    self.logger.warning(
                        f"Major file not found at:  {current_major_file_path}"
                    )

            # Semantic: programs
            if information_requests.find("<Semantic_Request_Programs>") != -1:
                self.logger.info("Starting semantic search for programs")
                search_text = (
                    information_requests.split("</Semantic_Request_Programs>")[0]
                    .split("<Semantic_Request_Programs>")[1]
                    .strip()
                )
                self.logger.info(f"Search text parsed: {search_text}")

                embedding_response = self.embedding_client.embeddings.create(
                    model=self.EMBEDDING_MODEL_NAME,
                    input=search_text,
                    encoding_format=self.ENCODING_FORMAT,
                )

                prompt_embedding = embedding_response.data[0].embedding
                embedding_tokens += embedding_response.usage.prompt_tokens

                self.logger.info(
                    "Embedding vector created "
                    f"({embedding_response.usage.prompt_tokens} tokens) "
                    f"for search text: {search_text}"
                )

                retrieved_context["<Semantic_Request_Programs>"] = vector_query(
                    query_vector_embedding=prompt_embedding,
                    db_client=self.db_client,
                    db_collection_name=self.COLLECTION_NAME_DEGREE_PROGRAMS,
                    limit=4,
                )

            # Semantic: courses
            if information_requests.find("<Semantic_Request_Courses>") != -1:
                self.logger.info("Starting semantic search for courses")
                search_text = (
                    information_requests.split("</Semantic_Request_Courses>")[0]
                    .split("<Semantic_Request_Courses>")[1]
                    .strip()
                )
                self.logger.info(f"Search text parsed: {search_text}")

                embedding_response = self.embedding_client.embeddings.create(
                    model=self.EMBEDDING_MODEL_NAME,
                    input=search_text,
                    encoding_format=self.ENCODING_FORMAT,
                )

                prompt_embedding = embedding_response.data[0].embedding
                embedding_tokens += embedding_response.usage.prompt_tokens

                self.logger.info(
                    "Embedding vector created "
                    f"({embedding_response.usage.prompt_tokens} tokens) "
                    f"for search text: {search_text}"
                )

                retrieved_context["<Semantic_Request_Courses>"] = vector_query(
                    query_vector_embedding=prompt_embedding,
                    db_client=self.db_client,
                    db_collection_name=self.COLLECTION_NAME_COURSES,
                    limit=30,
                )

        except Exception as e:
            self.logger.error(f"Database query error: {e}")
            self.logger.exception("")

        return retrieved_context, embedding_tokens

    def retrieve_context_next(
        self,
        user_prompt_text: str,
        student_catalog_year: str,
        student_degree_program: str,
        student_credits_earned: str,
        analytical_summary: str,
        information_requests: str,
    ):
        # Placeholder for a more advanced retrieval mode
        retrieved_context: Dict[str, List[Any]] = {}
        return retrieved_context

    # ------------------------------------------------
    # Parse "Courses for next semester" table
    # ------------------------------------------------
    def _parse_suggested_courses_from_response(
        self, text: str
    ) -> List[Dict[str, str]]:
        """
        Parse the 'Courses for next semester' markdown table from the LLM response.
        Returns a list of dicts: {'course_code', 'course_name', 'notes'}.
        """
        rows: List[Dict[str, str]] = []
        if not text:
            return rows

        lines = text.splitlines()
        in_section = False
        in_table = False
        header_indices = {"course_code": 0, "course_name": 1, "notes": 2}

        for line in lines:
            stripped = line.strip()

            # Find the section heading first
            if not in_section:
                if "courses for next semester" in stripped.lower():
                    in_section = True
                continue

            # Once in section, look for table lines beginning with '|'
            if stripped.startswith("|"):
                # First '|' line is the header row
                if not in_table:
                    in_table = True
                    header_cells = [c.strip() for c in stripped.strip("|").split("|")]
                    lower = [h.lower() for h in header_cells]

                    def _idx(name, default):
                        for i, h in enumerate(lower):
                            if name in h:
                                return i
                        return default

                    header_indices["course_code"] = _idx("course code", 0)
                    header_indices["course_name"] = _idx("course name", 1)
                    header_indices["notes"] = _idx("notes", 2)
                    continue

                # Skip separator row
                if (
                    set(
                        stripped.replace("|", "")
                        .replace("-", "")
                        .replace(":", "")
                        .strip()
                    )
                    == set()
                ):
                    continue

                cells = [c.strip() for c in stripped.strip("|").split("|")]
                max_idx = max(header_indices.values())
                if len(cells) <= max_idx:
                    continue

                code = cells[header_indices["course_code"]]
                name = cells[header_indices["course_name"]]
                notes = (
                    cells[header_indices["notes"]]
                    if header_indices["notes"] < len(cells)
                    else ""
                )

                # Ignore empty/fake rows
                if code.strip() in {"", "-", "—"} and name.strip() in {"", "-", "—"}:
                    continue

                rows.append(
                    {
                        "course_code": code.strip(),
                        "course_name": name.strip(),
                        "notes": notes.strip(),
                    }
                )
            else:
                if in_table:
                    break

        return rows

    # ------------------------------------------------
    # Candidate discovery helpers (currently unused but kept)
    # ------------------------------------------------
    def _find_candidate_courses(
        self, query_text: str, retrieved_context: Dict, course_catalog: Dict
    ):
        """
        Returns list[dict] with at least:
        {
          'course_code': 'ITSC 2214',
          'title': 'Data Structures and Algorithms',
          'prerequisites': ['ITSC 1213'],
          'score': 0.0
        }
        """
        candidates: List[Dict[str, Any]] = []

        # 1) Try to parse from retrieved context
        try:
            items = retrieved_context.get("<Semantic_Request_Courses>", [])
            for it in items:
                if isinstance(it, dict):
                    code = (it.get("course_code") or it.get("code") or "").strip()
                    title = (it.get("title") or it.get("course_name") or "").strip()
                    prereqs = it.get("prerequisites") or it.get("prereqs") or []
                    score = float(it.get("score") or 0.0)
                else:
                    s = str(it)
                    code, title, prereqs, score = self._loose_parse_course_text(s)

                if code:
                    candidates.append(
                        {
                            "course_code": code.upper(),
                            "title": title,
                            "prerequisites": (
                                prereqs if isinstance(prereqs, list) else []
                            ),
                            "score": score,
                        }
                    )
        except Exception:
            pass

        # 2) Fallback: keyword match over local catalog
        if not candidates and isinstance(course_catalog, dict):
            q = (query_text or "").lower()
            for code, meta in course_catalog.items():
                title = (meta.get("title") or meta.get("course_name") or "").lower()
                desc = (meta.get("description") or "").lower()
                if any(
                    tok in (title + " " + desc) for tok in self._keywords_from_query(q)
                ):
                    candidates.append(
                        {
                            "course_code": code.upper(),
                            "title": (meta.get("title") or meta.get("course_name") or "")
                            .strip(),
                            "prerequisites": meta.get("prerequisites")
                            or meta.get("prereqs")
                            or [],
                            "score": 0.0,
                        }
                    )

        # Deduplicate by course_code, keep best score
        dedup: Dict[str, Dict[str, Any]] = {}
        for c in candidates:
            code = c["course_code"]
            if code not in dedup or c.get("score", 0) > dedup[code].get("score", 0):
                dedup[code] = c

        return list(dedup.values())
    
    # use the SAME regex as _norm_code
    COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,6})\s*([0-9]{3,4})\b", re.I)

    def _extract_course_names_from_program_markdown(self, markdown_text: str) -> dict[str, str]:
        """
        Extract course_code -> course_name from markdown tables like:
        | XXXX 1501 Global Social Science | 3 | C | ... |
        """
        mapping: dict[str, str] = {}
        if not markdown_text:
            return mapping

        for line in markdown_text.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue

            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue

            first_cell = cells[0]  # "XXXX 1501 Global Social Science"

            # Find a course code inside the first cell
            m = self.COURSE_CODE_RE.search(first_cell.upper())
            if not m:
                continue

            # Build normalized code like "XXXX 1501" (works for 2-6 letter departments too)
            dept = m.group(1).upper()
            num = m.group(2)
            code = f"{dept} {num}"

            # Everything AFTER the matched code is the course name
            name = first_cell[m.end():].strip()

            # Skip obvious header rows
            if not name or name.lower() in {"course", "course code"}:
                continue

            mapping[code] = name

        return mapping




    def _keywords_from_query(self, q: str):
        toks = [t for t in re.split(r"[^a-z0-9+]+", q) if len(t) >= 3]
        if not toks:
            toks = ["course", "elective", "systems", "machine", "data", "design"]
        return toks

    def _loose_parse_course_text(self, s: str):
        import re as _re

        code = ""
        title = ""
        prereqs: List[str] = []
        score = 0.0
        m = _re.search(r"([A-Z]{2,}\s*\d{3,4})\s*(?:-\s*(.*))?$", s)
        if m:
            code = m.group(1).strip()
            if m.group(2):
                title = m.group(2).strip()
        return code, title, prereqs, score

    # ---------------------------------------------------------------
    # ---------------------- PREREQ EVALUATION ----------------------
    def _missing_prereqs(self, course_code: str, passed_codes: set) -> list[str]:
        target = self._norm_code(course_code)
        if not target:
            return []

        # 1) Manual overrides take precedence
        override = self.prereq_overrides.get(target)
        if override is not None:
            prereqs = [self._norm_code(p) for p in override if str(p).strip()]
            passed_norm = {self._norm_code(c) for c in (passed_codes or set())}
            return [p for p in prereqs if p and p not in passed_norm]

        # 2) Otherwise: read from catalog
        prereqs: list[str] = []
        try:
            for row in self._iter_catalog_courses():
                if not isinstance(row, dict):
                    continue
                code = self._norm_code(row.get("course_code") or "")
                if code != target:
                    continue

                raw = row.get("prerequisites") or row.get("prereqs") or []
                if isinstance(raw, str):
                    prereqs = [self._norm_code(p) for p in raw.split(",") if p.strip()]
                elif isinstance(raw, (list, tuple, set)):
                    prereqs = [self._norm_code(p) for p in raw if str(p).strip()]
                else:
                    prereqs = []
                break
        except Exception as e:
            self.logger.exception(f"_missing_prereqs failed for {course_code}: {e}")
            return []

        passed_norm = {self._norm_code(c) for c in (passed_codes or set())}
        return [p for p in prereqs if p and p not in passed_norm]

    
    def _build_canonical_course_name_map(self, catalog_year: str, flattened_context: str) -> dict[str, str]:
        """
        Priority:
        1) Course catalog (JSON) via _iter_catalog_courses(catalog_year)
        2) Program markdown tables parsed from flattened_context (catches XXXX courses)
        """
        name_map: dict[str, str] = {}

        # 1) From course catalog JSON (best)
        for row in self._iter_catalog_courses(catalog_year=catalog_year):
            code = self._norm_code(row.get("course_code") or "")
            name = (row.get("course_name") or "").strip()
            if code and name:
                name_map[code] = name


        # 2) From program markdown tables present in flattened_context (fallback)
        # flattened_context contains the program markdown you read from rag_corpus/.../programs/*.md
        prog_map = self._extract_course_names_from_program_markdown(flattened_context or "")
        for code, name in prog_map.items():
            # Only fill if missing, so catalog JSON wins
            if code not in name_map and name:
                name_map[code] = name

        return name_map
    


    def _norm_code(self, code: str) -> str:
        s = (code or "").upper().strip()
        m = self.COURSE_CODE_RE.search(s)
        if not m:
            return ""
        dept, num = m.group(1).upper(), m.group(2)
        return f"{dept} {num}"





    def _filter_suggestions_by_prereqs(
        self,
        suggestions: List[Dict[str, str]],
        passed_codes: Set[str],
        failed_codes: Set[str],
    ) -> List[Dict[str, str]]:
        """
        Take the 'Courses for next semester' rows parsed from the LLM
        and drop any course whose prerequisites are NOT fully satisfied.

        Rules:
        - If a course code is in failed_codes -> treat it as a retake candidate (allowed).
        - Otherwise, require _missing_prereqs(...) == [] to keep it.
        """
        filtered: List[Dict[str, str]] = []

        passed_norm = {self._norm_code(c) for c in (passed_codes or set())}
        failed_norm = {self._norm_code(c) for c in (failed_codes or set())}

        for row in suggestions:
            code = self._norm_code(row.get("course_code") or "")
            if not code:
                continue

            if code in failed_norm:
                filtered.append({**row, "course_code": code})
                continue

            missing = self._missing_prereqs(code, passed_norm)
            if missing:
                self.logger.info(
                    f"Dropping {code} from suggested list; missing prerequisites: {missing}"
                )
                continue

            filtered.append({**row, "course_code": code})

        return filtered

    # ------------------------------------------------
    # Catalog iterator (with catalog_year filtering)
    # ------------------------------------------------
    

    # inside class Chatbot
    def _iter_catalog_courses(self, catalog_year: str = ""):
        """
        Yield normalized catalog entries with keys:
          course_code: str
          course_name: str
          prerequisites: list[str]
          catalog_year: str (if available)

        Assumes self.courses is the object returned by load_courses(logger).

        If catalog_year is provided and the row has a matching "catalog_year"
        field, we only yield rows for that year. Otherwise, all rows are yielded.
        """

        if not self.courses:
            return

        # ---- Case 1: Pandas DataFrame (future-proof) ----
        try:
            import pandas as _pd

            if isinstance(self.courses, _pd.DataFrame):
                df = self.courses

                # Optional filter by catalog_year if the column exists
                if catalog_year and "catalog_year" in df.columns:
                    df = df[
                        df["catalog_year"].astype(str).str.strip()
                        == str(catalog_year).strip()
                    ]

                for _, row in df.iterrows():
                    code = self._norm_code(row.get("course_code",""))
                    name = str(row.get("course_name", "")).strip()

                    raw = row.get("prerequisites", []) or row.get("prereqs", [])
                    if isinstance(raw, str):
                        prereqs = [self._norm_code(p) for p in raw.split(",") if p.strip()]
                    elif isinstance(raw, (list, tuple, set)):
                        prereqs = [self._norm_code(str(p)) for p in raw if str(p).strip()]
                    else:
                        prereqs = []
                       
                    yield {
                        "course_code": code,
                        "course_name": name,
                        "prerequisites": prereqs,
                        "catalog_year": str(row.get("catalog_year", "")).strip(),
                    }
                return

        except Exception:
            # If pandas is not available or something goes wrong, fall through
            pass

        # ---- Case 2: list[dict] (this is your actual load_courses format) ----
        if isinstance(self.courses, list):
            for row in self.courses:
                if not isinstance(row, dict):
                    continue

                # Optional filter by catalog_year
                if catalog_year:
                    row_year = str(row.get("catalog_year", "")).strip()
                    if row_year and row_year != str(catalog_year).strip():
                        continue

                code = self._norm_code(row.get("course_code") or "")
                name = str(row.get("course_name", "")).strip()

                raw = row.get("prerequisites", []) or row.get("prereqs", [])
                if isinstance(raw, str):
                    prereqs = [self._norm_code(p) for p in raw.split(",") if p.strip()]
                elif isinstance(raw, (list, tuple, set)):
                    prereqs = [self._norm_code(p) for p in raw if str(p).strip()]
                else:
                    prereqs = []


                yield {
                    "course_code": code,
                    "course_name": name,
                    "prerequisites": prereqs,
                    "catalog_year": row.get("catalog_year", ""),
                }

        # (If self.courses is some other unexpected type, we just yield nothing.)



    # ------------------------------------------------
    # Structured suggestion engine
    # ------------------------------------------------
    def _suggest_courses_structured(
        self,
        passed_codes: Set[str],
        failed_codes: Set[str],
        catalog_year: str = "",
        max_results: int = 12,
    ) -> List[Dict[str, str]]:
        """
        Very simple rule-based suggestion engine:

        - only courses the student has NOT passed
        - prerequisites must be a subset of passed_codes
        - ignores failed_codes (retake vs new is handled by the LLM)
        - only uses the selected catalog_year if the catalog data supports it
        """
        failed_norm = {self._norm_code(c) for c in (failed_codes or set())}
        retake_rows = []

        for code in sorted(failed_norm):
            if code:
                # Name resolution will happen later via name_map anyway
                retake_rows.append({"course_code": code, "course_name": ""})

        suggestions: List[Dict[str, str]] = []

        # Iterate over catalog entries for this catalog_year (if filter is available)
        passed_norm = {self._norm_code(c) for c in (passed_codes or set())}

        for row in self._iter_catalog_courses(catalog_year):
            code = self._norm_code(row.get("course_code") or "")
            name = (row.get("course_name") or "").strip()
            prereqs = [self._norm_code(p) for p in (row.get("prerequisites") or []) if str(p).strip()]

            if not code:
                continue
            if code in passed_norm:
                continue
            if prereqs and not set(prereqs).issubset(passed_norm):
                continue

            suggestions.append({"course_code": code, "course_name": name})

        suggestions = retake_rows + suggestions
        # de-dupe by course_code preserving order
        seen = set()
        deduped = []
        for r in suggestions:
            c = r["course_code"]
            if c and c not in seen:
                seen.add(c)
                deduped.append(r)
        return deduped[:max_results]


        # simple deterministic ordering: alphabetic by code
        suggestions.sort(key=lambda r: r["course_code"])
        return suggestions[:max_results]


    # ------------------------------------------------
    # (Optional) candidate summary for LLM (unused now)
    # ------------------------------------------------
    def _summarize_candidates_for_llm(
        self, candidates: List[Dict[str, Any]], passed_codes: Set[str], failed_codes: Set[str]
    ) -> str:
        """
        Bucket & rank candidates, then build a compact text summary.
        """
        retake: List[Dict[str, Any]] = []
        eligible: List[Dict[str, Any]] = []
        needs: List[Dict[str, Any]] = []

        for c in candidates:
            code = c["course_code"].upper()
            if not code:
                continue

            if code in failed_codes:
                retake.append({**c, "reason": "previously failed/withdrawn"})
                continue

            if code in passed_codes:
                continue

            missing = self._missing_prereqs(code, passed_codes)
            if missing:
                needs.append({**c, "missing_prereqs": missing})
            else:
                eligible.append(c)

        def _level(code: str) -> int:
            import re as _re

            m = _re.search(r"(\d{3,4})", code)
            return int(m.group(1)) if m else 0

        retake.sort(key=lambda x: -x.get("score", 0))
        eligible.sort(key=lambda x: (_level(x["course_code"]), -x.get("score", 0)))
        needs.sort(key=lambda x: -x.get("score", 0))

        retake = retake[:5]
        eligible = eligible[:8]
        needs = needs[:6]

        lines: List[str] = []
        if retake:
            lines.append("Retake candidates (previously failed/withdrawn):")
            for c in retake:
                lines.append(
                    f"- {c['course_code']} {('— ' + c.get('title','')) if c.get('title') else ''}"
                )
            lines.append("")

        lines.append("Eligible next-course candidates:")
        if eligible:
            for c in eligible:
                lines.append(
                    f"- {c['course_code']} {('— ' + c.get('title','')) if c.get('title') else ''}"
                )
        else:
            lines.append("- (none)")
        lines.append("")

        if needs:
            lines.append("Courses that require missing prerequisites:")
            for c in needs:
                missing = ", ".join(c.get("missing_prereqs", []))
                lines.append(
                    f"- {c['course_code']} "
                    f"{('— ' + c.get('title','')) if c.get('title') else ''}: "
                    f"missing {missing}"
                )
            lines.append("")

        lines.append(
            "Advising rules: prefer retakes first if timely; otherwise pick eligible "
            "courses that progress the degree, respect prerequisites, and avoid duplicates."
        )

        return "\n".join(lines)
>>>>>>> 78649f519af039264048d2e2e17f8f354f3c13c6
