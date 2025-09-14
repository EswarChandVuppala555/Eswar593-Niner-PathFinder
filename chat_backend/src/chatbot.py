import os
from pydantic import BaseModel, Field
from src.retrieval import openai_extract_vector, vector_query, load_courses
from typing import List, Dict
import time
import re



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

system_prompt_planning_retrieval_mode_component = {}
planning_response_validation_pattern = {}

# System prompt for most advanced storage and retrieval mode 
system_prompt_planning_retrieval_mode_component['0'] = """
## Information Retrieval Requests
Between <Retrieval> and </Retrieval> tags, submit up to three pairs of the following tags to request additional information from internal repositories.  Don't request information unless it will be useful to generate a better response.  

<Specific_Request_Current_Major> Request information about the student's current degree program, including requirements, courses, and other relevant details, for the student's catalog year. Place no characters between these tags. </Specific_Request_Current_Major>
<Specific_Request_Current_Major_Sample_Schedules> Request example schedules for the student's major and catalog year.  Request this when the user prompt is about scheduling courses or planning a semester or to understand how the program usually flows. Place no characters between these tags.  </Specific_Request_Current_Major_Sample_Schedules>
<Semantic_Request_Programs> Semantic search for degree programs related to the request, such as majors and concentrations, minors, and early graduate programs. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags.  </Semantic_Request_Programs>
<Semantic_Request_Courses> - Semantic search for courses related to user prompt; includes course names, codes, descriptions, and prerequesites and corequisites. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags. </Semantic_Request_Courses>
<Semantic_Request_Support_Resources> - Semantic search for support resources related to user prompt, such as advising, tutoring, career services, and mental health resources. Include a 1-3 sentence description of the information sought between these tags.  Include any important keywords or concepts in the sentences or immedately after them, but between the tags. </Semantic_Request_Support_Resources>

Each XML tag that is used must be reproduced verbatim, and the text between the tags must be replaced as directed.

"""

planning_response_validation_pattern['0'] = r"<Analytical_Summary>.*</Analytical_Summary>.*<Retrieval>.*</Retrieval>"




system_prompt_generation_common_component = """# Your Role
You generate responses as part of a chatbot system that provides information to undergraduate university students about academic programs and course and helps those students navigate their academic journey.

# Your Response Style and Priorities
Your response should be informative, friendly, helpful, and concise yet thorough using good word economy.
If the user does not provide enough information to provide an accurate, relevant, and complete response, you ask follow-up questions to clarify their request before answering questions.
Unless the question is very simple and the response can be found in the provided context, encourage the student to speak with their advisor to help ensure their academic success.  
You never provide specific facts about a UNC Charlotte degree program, course, or university policy unless the information is provided in the provided contextual information.

"""




# API Models for Chatbot Requests and Responses
class ChatRequest(BaseModel):
    conversation_history : List
    user_prompt_text: str = Field(..., min_length=1, max_length=1000)
    student_catalog_year: str = Field(..., min_length=1, max_length=9)
    student_degree_program: str = Field(..., min_length=1, max_length=120)
    student_credits_earned: str = Field(..., min_length=1, max_length=20)
    
class ChatResponse(BaseModel):
    error_code: int = 0
    chat_response_content: str = Field(..., min_length=1, max_length=10000)
    analytical_summary: str = Field(..., min_length=1, max_length=10000)  # Analysis response if applicable
    information_requests : str = Field(..., min_length=0, max_length=1000) # Context -> decision 
    retrieved_context: Dict[str,List]  # Context type -> (Doc name-> Doc content)
    flattened_context: str = Field(..., min_length=0, max_length=120000)  # Flattened context for response generation
    planning_generation_time_required : float = Field(..., ge=0)  # Time taken for planning
    retrieval_time_required : float = Field(..., ge=0)  # Time taken for retrieval
    chat_response_generation_time_required : float = Field(..., ge=0)  # Time taken for generation
    planning_attempts: int = Field(..., ge=0)  # Number of planning attempts made
    planning_input_tokens: int = Field(..., ge=0)  # Number of input tokens processed
    planning_output_tokens: int = Field(..., ge=0)  # Number of output tokens
    chat_response_input_tokens: int = Field(..., ge=0)  # Number of input tokens processed for response generation
    chat_response_output_tokens: int = Field(..., ge=0)  # Number of output tokens generated

    # keywords: List[str] = []

# Chat request handler
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
        logger):

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

        # Load courses from local corpos - for looking up specific courses
        # TODO change this to a database for SQL-style query?
        self.courses = load_courses(logger)

    # Overall handling of the chat response process
    def chat(
        self,
        chat_request: ChatRequest) -> ChatResponse:     

        # Start timing the request processing
        started_processing = time.time()  

        # Log the request as it is received
        self.logger.info(f"Processing prompt request: {chat_request.user_prompt_text}")
        self.logger.info(f"Conversation history: {chat_request.conversation_history}")
        self.logger.info(f"Student degree program: {chat_request.student_degree_program}")
        self.logger.info(f"Student catalog year: {chat_request.student_catalog_year}")
        self.logger.info(f"Student credits earned: {chat_request.student_credits_earned}")                

        # PREPARE CONTEXT FOR RESPONSE GENERATION
        analytical_summary, information_requests, retrieved_context, flattened_context, \
        planning_attempts, planning_completed_time, planning_input_tokens, planning_output_tokens, embedding_tokens = \
        self.prepare_context(chat_request=chat_request)

        retrieval_completed_time = time.time()


        # GENERATE CHAT RESPONSE
        system_prompt_generation = system_prompt_generation_common_component + flattened_context
        
        message_history_chat_response = chat_request.conversation_history.copy()
        message_history_chat_response.extend([
            {"role": "system", "content": system_prompt_generation},
            {"role": "user", "content": chat_request.user_prompt_text}])

        try: 
            chat_response = self.generation_client.chat.completions.create(
                model = self.GENERATION_MODEL_ID,
                messages = message_history_chat_response)
        
            chat_response_content = chat_response.choices[0].message.content

        except Exception as e:
            self.logger.error(f"Error generating response: {e}")


        generation_completed_time = time.time()
        self.logger.info(f"Generated response obtained: {chat_response_content[:500]}...")  # Log only the first 500 characters for brevity


        # QUALITY / SAFETY CHECK

        # Check the generated response for inappropriate content
        # Skip for now
        # response_check = self.check_system_response_appropriateness(prompt_request.user_prompt_text)
        # if response_check.split(maxsplit=1)[0] == 'inappropriate':
        #     return PromptResponse(generated_response = 'inappropriate response detected')


        # Calculate times for each step and log them
        planning_generation_time_required = planning_completed_time - started_processing
        retrieval_time_required = retrieval_completed_time - planning_completed_time
        chat_response_generation_time_required = generation_completed_time - retrieval_completed_time
        self.logger.info(f"Planning time: {planning_generation_time_required:.2f} seconds")
        self.logger.info(f"Retrieval time: {retrieval_time_required:.2f} seconds")
        self.logger.info(f"Generation time: {chat_response_generation_time_required:.2f} seconds")

        return ChatResponse(
                error_code=0,
                chat_response_content = chat_response_content,
                analytical_summary = analytical_summary,
                information_requests = information_requests,
                retrieved_context = retrieved_context, 
                flattened_context = flattened_context,
                planning_attempts = planning_attempts,
                planning_generation_time_required = planning_generation_time_required,
                retrieval_time_required = retrieval_time_required,
                chat_response_generation_time_required = chat_response_generation_time_required,
                planning_input_tokens = planning_input_tokens,
                planning_output_tokens = planning_output_tokens,
                chat_response_input_tokens = chat_response.usage.prompt_tokens,
                chat_response_output_tokens = chat_response.usage.completion_tokens,
                embedding_tokens = embedding_tokens)


    # Consists of planning, retrieval, and context preparation steps
    def prepare_context(
        self,
        chat_request):

        # PREPARE SYSTEM PROMPT FOR ANALYSIS

        # Determine intent of the user prompt and identify required information
        student_info_from_ui_text = f"""# Student Information:     
            The student has provided the following information via dropdowns in the user interface:
            Student catalog year: {chat_request.student_catalog_year}.
            Student degree program: {chat_request.student_degree_program}.
            Student has earned: {chat_request.student_credits_earned}.
            """

        # System prompt is comprised of:
        # 1: common component, indicating that the model should not respond directly but provide analysis
        # 2: information request information, depending on storage retrieval mode
        # 3: critical student information from the UI, including catalog year, degree program, and credits earned 
        system_prompt_planning = system_prompt_planning_common_component + system_prompt_planning_retrieval_mode_component[self.STORAGE_RETRIEVAL_MODE] + student_info_from_ui_text

        # Prepare the message history for the planning generation call
        message_history_analysis = chat_request.conversation_history.copy()
        message_history_analysis.extend([
                    {"role": "system", "content": system_prompt_planning},
                    {"role": "user", "content": chat_request.user_prompt_text}])

        # Get the planning response from the generation client
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
                    top_p=0.01,)
                planning_response_content = planning_response.choices[0].message.content
            except Exception as e:
                self.logger.error(f"Planning generation call error: {e}")

            self.logger.info(f"Planning attempt {planning_attempts} of 3: {planning_response_content}")

            # Validate the response by matching against the system prompt validation pattern
            planning_pattern_match = re.match(planning_response_validation_pattern[self.STORAGE_RETRIEVAL_MODE], planning_response_content, re.DOTALL)
            
            if planning_pattern_match:
                self.logger.info(f"Planning response matches the expected format for storage retrieval mode {self.STORAGE_RETRIEVAL_MODE}.")
                break
            elif not planning_pattern_match and planning_attempts < 3:
                self.logger.error(f"Planning response does not match the expected format for storage retrieval mode {self.STORAGE_RETRIEVAL_MODE}. Retrying...")
                planning_response_content = ""
            else :
                self.logger.error(f"Planning response does not match the expected format for storage retrieval mode {self.STORAGE_RETRIEVAL_MODE} after 3 attempts. Exiting.")
                raise ValueError(f"Planning response does not match the expected format for storage retrieval mode {self.STORAGE_RETRIEVAL_MODE} after 3 attempts.")

        
        # Extract the analytical summary and information request tags from the response    
        analytical_summary = planning_response_content.split('</Analytical_Summary>')[0].split('<Analytical_Summary>')[1].strip()
        information_requests = planning_response_content.split('</Retrieval>')[0].split('<Retrieval>')[1].strip()  
        
        # Log performance metrics
        planning_input_tokens = planning_response.usage.prompt_tokens
        planning_output_tokens = planning_response.usage.completion_tokens
        planning_complete = time.time()

        self.logger.info(f"User prompt text: {chat_request.user_prompt_text}")
        self.logger.info(f"Analytical Summary: {analytical_summary}")
        self.logger.info(f"Information Requests: {information_requests}")

        
        if self.STORAGE_RETRIEVAL_MODE == '0':
            retrieved_context, embedding_tokens = self.retrieve_context_basic(
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                information_requests=information_requests)


        elif self.STORAGE_RETRIEVAL_MODE == '1':
            retrieved_context = self.retrieve_context_next(
                user_prompt_text=chat_request.user_prompt_text,
                student_catalog_year=chat_request.student_catalog_year,
                student_degree_program=chat_request.student_degree_program,
                student_credits_earned=chat_request.student_credits_earned,
                analytical_summary=chat_request.analytical_summary,
                information_requests=chat_request.information_requests)



        for context_type, context_items in retrieved_context.items():
            
            # if context_items is a string:
            if context_items and isinstance(context_items, str):
                self.logger.info(f"Retrieved context type: {context_type}, content length: {len(context_items)}")
                self.logger.info(f"Retrieved context item content: {context_items[:50]}...")  # Log only the first few characters for brevity
                # Should never get this
                self.logger.error(f"Retrieved context item is a string, not a list!")
            elif context_items and isinstance(context_items, list) and len(context_items) > 0 and isinstance(context_items[0], str):
                self.logger.info(f"Retrieved context type: {context_type}, number of items: {len(context_items)}")
                
                for item in context_items:
                    self.logger.info(f"Retrieved context item {len(item)}, content: {item[:50]}...")


        # Flatten the retrieved context into a single string
        flattened_context = student_info_from_ui_text
        for information_type_tag, information in retrieved_context.items():
            flattened_context += f"#{information_type_tag} \n"

            for i in range(len(information)):
                if isinstance(information[i], str):
                    flattened_context += f"{information[i]}\n\n"
                elif isinstance(information[i], dict):
                    # If the information is a dictionary, convert it to a string representation
                    flattened_context += f"{str(information[i])}\n\n"
                else:
                    flattened_context += f"{str(information[i])}\n\n"

            # # if dict of dicts
            # for document_id, document_content in information.items():
            #     flattened_context += f"<Start Document ID: {document_id}>\n"
            #     flattened_context += f"{document_content}\n\n"
            #     flattened_context += f"<End Document ID: {document_id}>\n"
            
            flattened_context += f"#</{information_type_tag}>\n\n"

        self.logger.info(f"\n  Flattened context characters: {len(flattened_context)}, \n  Content: {flattened_context[:500]}...")  # Log only the first 500 characters for brevity

        return analytical_summary, information_requests, retrieved_context, flattened_context, planning_attempts, planning_complete, planning_input_tokens, planning_output_tokens, embedding_tokens



    def retrieve_context_basic(
        self,
        student_catalog_year,
        student_degree_program,
        information_requests):

        retrieved_context = {}

        try:
            # TODO parallelize context requests to improve response speed?
            # TODO add support for keyword and hybrid search
            # TODO add support for reranking search results
            # TODO add support for metadata queries
            # TODO Have a generative service create queries based on the user prompt and search for additional context in the database
            
            ### REQUESTS FOR SPECIFIC FILES ###
            # TODO - acquire additional sample scheudles?
            # TODO - fail over to vector search?
            if information_requests.find("<Specific_Request_Current_Major_Sample_Schedules>") != -1:
                
                sample_schedule_file_path = os.path.join('rag_corpus', 'sample_schedules', student_catalog_year, student_degree_program, '.md')

                # Check if the sample schedule file exists for the given degree program and catalog year
                if os.path.exists(sample_schedule_file_path):
                    with open(sample_schedule_file_path, "r") as file:
                        retrieved_context['<Specific_Request_Current_Major_Sample_Schedules>'] = []
                        retrieved_context['<Specific_Request_Current_Major_Sample_Schedules>'].append(file.read())
                else:
                    # Log the missing file
                    self.logger.warning(f"Sample schedule file not found for {student_degree_program} {student_catalog_year}.")


            # TODO - add these to the rag_corpus
            if information_requests.find("<Specific_Request_Current_Major>") != -1:
                # Check if the sample schedule file exists for the given degree program and catalog year
                current_major_file_path = os.path.join('rag_corpus', 'ug_cat', student_catalog_year, 'programs', student_degree_program+'.md')

                if os.path.exists(current_major_file_path):
                    with open(current_major_file_path, "r") as file:
                        retrieved_context['<Specific_Request_Current_Major>'] = []
                        retrieved_context['<Specific_Request_Current_Major>'].append(file.read())
                else:
                    # Log the missing file
                    self.logger.warning(f"Major file not found at:  {current_major_file_path}")

            embedding_tokens = 0

            ### SEMANTIC SEARCH REQUESTS ###
            # TODO simplify this code by using a single function to handle semantic searches
            # Next, conduct any necessary semantic searches
            if information_requests.find("<Semantic_Request_Programs>") != -1:
                self.logger.info(f"Starting semantic search for programs")
                # Parse the information request to extract the search text
                search_text = information_requests.split('</Semantic_Request_Programs>')[0].split('<Semantic_Request_Programs>')[1].strip()
                self.logger.info(f"Search text parsed: {search_text}")

                embedding_response = self.embedding_client.embeddings.create(
                    model = self.EMBEDDING_MODEL_NAME,
                    input = search_text,
                    encoding_format = self.ENCODING_FORMAT)

                prompt_embedding = embedding_response.data[0].embedding
                embedding_tokens += embedding_response.usage.prompt_tokens

                self.logger.info(f"Embedding vector created ({embedding_response.usage.prompt_tokens} tokens) for search text: {search_text}")           

                retrieved_context["<Semantic_Request_Programs>"] = vector_query(
                        query_vector_embedding = prompt_embedding,
                        db_client = self.db_client,
                        db_collection_name = self.COLLECTION_NAME_DEGREE_PROGRAMS,
                        limit= 4)
            

            # TODO - include critical context about courses whenever retrieving course descriptions
            if information_requests.find("<Semantic_Request_Courses>") != -1:
                self.logger.info(f"Starting semantic search for courses")
                search_text = information_requests.split('</Semantic_Request_Courses>')[0].split('<Semantic_Request_Courses>')[1].strip()
                self.logger.info(f"Search text parsed: {search_text}")

                # Vectorize the query
                embedding_response = self.embedding_client.embeddings.create(
                    model = self.EMBEDDING_MODEL_NAME,
                    input = search_text,
                    encoding_format = self.ENCODING_FORMAT)

                prompt_embedding = embedding_response.data[0].embedding
                embedding_tokens += embedding_response.usage.prompt_tokens

                self.logger.info(f"Embedding vector created ({embedding_response.usage.prompt_tokens} tokens) for search text: {search_text}")                         

                retrieved_context['<Semantic_Request_Courses>'] = vector_query(
                        query_vector_embedding = prompt_embedding,
                        db_client = self.db_client,
                        db_collection_name = self.COLLECTION_NAME_COURSES,
                        limit= 30)
            
        except Exception as e:
            self.logger.error(f"Database query error: {e}")
            self.logger.exception('')
            
        return retrieved_context, embedding_tokens
    
    def retrieve_context_next(
        user_prompt_text,
        student_catalog_year,
        student_degree_program,
        student_credits_earned,
        analytical_summary,
        information_requests,
        embedding_client,
        EMBEDDING_MODEL_NAME,
        ENCODING_FORMAT,
        db_client,
        logger):

        retrieved_context = {}

        return retrieved_context