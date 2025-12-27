import os
import logging
from typing import Optional, Dict, Any
from typing import List
import csv
from datetime import datetime
from fastapi import Request, UploadFile, File

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import chromadb
from chromadb import Client

from src.chatbot import Chatbot, ChatRequest, ChatResponse

# from chromadb.config import Settings
# from chromadb.utils import embedding_functions
# from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

from openai import OpenAI

import ssl
import time

# from rake_nltk import Rake
# import nltk

# try:
#     _create_unverified_https_context = ssl._create_unverified_context
# except AttributeError:
#     pass
# else:
#     ssl._create_default_https_context = _create_unverified_https_context

# try:
#     nltk.download('punkt')
#     nltk.download('punkt_tab')
#     nltk.download('stopwords')
# except Exception as e:
#     print(f"Error downloading NLTK resources: {e}")


# API Models for Feedback Requests and Responses
class FeedbackRequest(BaseModel):
    conversation_history: List = Field(Dict[str, str])
    feedback_type: str = Field(..., min_length=1, max_length=10)
    feedback_text: str = Field(..., min_length=0, max_length=1000)
    student_catalog_year: str = Field("", max_length=9)
    student_degree_program: str = Field("", max_length=100)
    student_credits_earned: str = Field("", min_length=0, max_length=30)
    
class FeedbackResponse(BaseModel):
    error_code: int = 0
    status: str
    message: str
    
# SET UP LOGGING
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FEEDBACK_FILE = "/app/data/feedback_log.csv"  # matches your docker volume: ./data:/app/data

FEEDBACK_FIELDS = [
    "timestamp",
    "feedback_type",
    "feedback_reason",
    "feedback_text",
    "student_catalog_year",
    "student_degree_program",
    "student_credits_earned",
    "pursued_courses",
    "last_chat_response",
    "conversation_history",
]

os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)

def _ensure_feedback_file():
    if not os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=FEEDBACK_FIELDS,
                quoting=csv.QUOTE_ALL,           # <-- add this
            )
            writer.writeheader()

_ensure_feedback_file()


# Initialize the FastAPI app
app = FastAPI(
    title="Niner Pathfinder API",
    description="API for the Niner Pathfinder chat system",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

#EMBEDDING AND GENERATION CLIENT CONNECTIONS
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_EMBEDDING_URL = os.getenv('OPENAI_EMBEDDING_URL')
OPENAI_GENERATION_URL = os.getenv('OPENAI_GENERATION_URL')
EMBEDDING_MODEL_NAME = os.getenv('EMBEDDING_MODEL_NAME')
ENCODING_FORMAT = os.getenv('ENCODING_FORMAT')
PLANNING_MODEL_ID = os.getenv('PLANNING_MODEL_ID')
GENERATION_MODEL_ID = os.getenv('GENERATION_MODEL_ID')

try:
    embedding_client = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logger.exception(f"Embedding client connection error: {e}")

try:     
    generation_client = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logger.exception(f"Generative client connection error: {e}")

# CHROMA DATABASE CONNECTION
time.sleep(8)  # Wait for ChromaDB to start up
CHROMA_PORT = os.getenv('CHROMA_PORT')
CHROMA_HOST = os.getenv('CHROMA_HOST')
try:
    chromadb_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
except Exception as e:
    logger.exception(f"ChromaDB connection error: {e}")
    logger.exception()


# STORAGE AND RETRIEVAL CONFIGURATION - TODO move this elsewhere
STORAGE_RETRIEVAL_MODE = os.getenv('STORAGE_RETRIEVAL_MODE') # 0,1,2
COLLECTION_NAME_DEGREE_PROGRAMS = os.getenv("COLLECTION_NAME_DEGREE_PROGRAMS")
COLLECTION_NAME_COURSES = os.getenv("COLLECTION_NAME_COURSES")


# Initialzize chatbot instance
chatbot = Chatbot(
    STORAGE_RETRIEVAL_MODE=STORAGE_RETRIEVAL_MODE,
    generation_client=generation_client,
    PLANNING_MODEL_ID=PLANNING_MODEL_ID,
    GENERATION_MODEL_ID=GENERATION_MODEL_ID,
    embedding_client=embedding_client,
    EMBEDDING_MODEL_NAME=EMBEDDING_MODEL_NAME,
    ENCODING_FORMAT=ENCODING_FORMAT,
    db_client=chromadb_client,
    COLLECTION_NAME_DEGREE_PROGRAMS=COLLECTION_NAME_DEGREE_PROGRAMS,
    COLLECTION_NAME_COURSES=COLLECTION_NAME_COURSES,
    logger=logger
)


# def process_feedback(feedback_request: FeedbackRequest) -> FeedbackResponse:
#     """Process user feedback and store it in Weaviate using the existing client"""
#     try:
#         # Log the feedback
#         logger.info(f"Received feedback - Type: {feedback_request.feedback_type}")
#         logger.info(f"Feedback text: {feedback_request.feedback_text}")
#         logger.info(f"Student program: {feedback_request.student_degree_program}")
#         logger.info(f"Student catalog year: {feedback_request.student_catalog_year}")
        
#         # Get the Feedback collection from Weaviate using the existing client
#         feedback_collection = weaviate_client.collections.get("Feedback")
        
#         # Prepare conversation history as text
#         conversation_text = ""
#         for message in feedback_request.conversation_history:
#             role = message.get("role", "unknown")
#             content = message.get("content", "")
#             conversation_text += f"{role}: {content}\n\n"
        
#         # Create feedback object
#         from datetime import datetime
#         feedback_object = {
#             "feedback_type": feedback_request.feedback_type,
#             "feedback_text": feedback_request.feedback_text,
#             "student_degree_program": feedback_request.student_degree_program,
#             "student_catalog_year": feedback_request.student_catalog_year,
#             "conversation_history": conversation_text,
#             "timestamp": datetime.now().isoformat()
#         }
        
#         # Add to Weaviate
#         result = feedback_collection.data.insert(feedback_object)
#         logger.info(f"Feedback stored in Weaviate with ID: {result}")
        
#         return FeedbackResponse(
#             status="success",
#             message="Feedback stored successfully in database",
#             error_code=0
#         )
#     except Exception as e:
#         logger.error(f"Error processing feedback: {e}")
#         return FeedbackResponse(
#             status="error",
#             message=f"An error occurred while processing feedback: {e}",
#             error_code=500
#         )

# Define API Endpoints

@app.post("/chat-request", response_model=ChatResponse)
async def chat_request(chat_request: ChatRequest):
    """Process user prompt and return response"""
    return chatbot.chat(chat_request)

@app.post("/submit-feedback", response_model=FeedbackResponse)
async def submit_feedback_endpoint(request: Request):
    """Submit user feedback about the chatbot"""
    try:
        _ensure_feedback_file()

        data = await request.json()

        feedback_type = data.get("feedback_type", "")
        feedback_reason = data.get("feedback_reason", "")
        feedback_text = (data.get("feedback_text", "") or "").replace("\r\n", " ").replace("\n", " ")
        catalog_year = data.get("student_catalog_year", "")
        degree_program = data.get("student_degree_program", "")
        credits = data.get("student_credits_earned", "")
        pursued_courses = str(data.get("pursued_courses", []))

        # Extract last assistant reply (if available)
        last_chat_response = ""
        conv_hist = data.get("conversation_history", [])
        if isinstance(conv_hist, list):
            for msg in reversed(conv_hist):
                if msg.get("role") == "assistant":
                    last_chat_response = (msg.get("content", "") or "").replace("\r\n", " ").replace("\n", " ")
                    break

        # Serialize conversation history compactly (single line)
        conversation_history = ""
        if isinstance(conv_hist, list):
            try:
                conversation_history = "; ".join(
                    f"{m.get('role','')}: {(m.get('content','') or '').replace(chr(10), ' ').replace(chr(13), ' ')}"
                    for m in conv_hist
                )
            except Exception:
                conversation_history = str(conv_hist)

        row = {
            "timestamp": datetime.utcnow().isoformat(),
            "feedback_type": feedback_type,
            "feedback_reason": feedback_reason,
            "feedback_text": feedback_text,
            "student_catalog_year": catalog_year,
            "student_degree_program": degree_program,
            "student_credits_earned": credits,
            "pursued_courses": pursued_courses,
            "last_chat_response": last_chat_response,
            "conversation_history": conversation_history,
        }

        with open(FEEDBACK_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FEEDBACK_FIELDS,quoting=csv.QUOTE_ALL,)
            # header already ensured; just write the row
            writer.writerow(row)

        return FeedbackResponse(
            status="success",
            message="Feedback logged successfully",
            error_code=0,
        )
    except Exception as e:
        logger.exception(f"Error logging feedback: {e}")
        return FeedbackResponse(
            status="error",
            message=f"Error logging feedback: {e}",
            error_code=500,
        )

@app.post("/upload-courses")
async def upload_courses(file: UploadFile = File(...)):
    """Handle uploaded course file"""
    try:
        logger.info(f"📂 Received upload: {file.filename}")
        content = await file.read()
        logger.info(f"File size: {len(content)} bytes")

        # Save file locally (temporary)
        save_path = f"/app/data/uploads/{file.filename}"
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(content)

        logger.info(f"✅ Saved uploaded file to {save_path}")
        return {"status": "success", "message": f"File '{file.filename}' uploaded successfully."}
    except Exception as e:
        logger.exception(f"❌ Upload failed: {e}")
        return {"status": "error", "message": str(e)}

#     """Submit user feedback about the chatbot"""
#     return process_feedback(feedback_request)

# Cleanup on shutdown
# @app.on_event("shutdown")
# async def shutdown_event():
