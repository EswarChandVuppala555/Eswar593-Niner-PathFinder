import chromadb
import os
import logging
import time
from src.load_chroma_basic import load_chroma_basic
from src.load_chroma_next import load_chroma_next 

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Starting ChromaDB loader...")

time.sleep(3) # Wait for ChromaDB to start up

logger.info("ChromaDB loader started.")

# Load corpus and storage-retrieval mode
STORAGE_RETRIEVAL_MODE = os.getenv('STORAGE_RETRIEVAL_MODE')

logger.info(f"Storage-retrieval mode: {STORAGE_RETRIEVAL_MODE}")

CHROMA_HOST = os.getenv("CHROMA_HOST")
CHROMA_PORT = os.getenv("CHROMA_PORT")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME")
EMBEDDING_MODEL_FORMAT = os.getenv("EMBEDDING_MODEL_FORMAT")

client = chromadb.HttpClient(
    host='chroma', 
    port=CHROMA_PORT)

if STORAGE_RETRIEVAL_MODE == '0':
    # Load the corpus and structure for advanced storage and retrieval
    load_chroma_basic(client, logger)

elif STORAGE_RETRIEVAL_MODE == '1':
    # Load the corpus and structure for intermediate storage and retrieval
    load_chroma_next(client, logger)


