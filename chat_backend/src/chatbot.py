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
