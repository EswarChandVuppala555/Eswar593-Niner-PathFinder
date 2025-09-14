import os
import json
import chardet
from src.course_loader import load_chroma_courses
from src.programs_loader import load_chroma_degree_programs



def load_chroma_basic(
    chroma_client,
    logger):


    EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME") # Only for loading the right embeddings

    ### LOAD DEGREE PROGRAMS DATA ###
    COLLECTION_NAME_DEGREE_PROGRAMS = os.getenv("COLLECTION_NAME_DEGREE_PROGRAMS")

    load_chroma_degree_programs(
        chroma_client=chroma_client,
        COLLECTION_NAME_DEGREE_PROGRAMS=COLLECTION_NAME_DEGREE_PROGRAMS,
        EMBEDDING_MODEL_NAME=EMBEDDING_MODEL_NAME,
        logger=logger)
    

    ### LOAD COURSE DESCRIPTIONS DATA ###
    COLLECTION_NAME_COURSES = os.getenv("COLLECTION_NAME_COURSES")
    load_chroma_courses(
        chroma_client=chroma_client,
        COLLECTION_NAME_COURSES=COLLECTION_NAME_COURSES,
        logger=logger)