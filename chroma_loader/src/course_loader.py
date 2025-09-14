import os
import json


def load_chroma_courses(
    chroma_client,
    COLLECTION_NAME_COURSES,
    logger):

    ### LOAD COURSE DESCRIPTIONS DATA ###
    
    # Create a new collection for courses data
    logger.info(f'Loading course descriptions into ChromaDB collection: {COLLECTION_NAME_COURSES}')
    collection = chroma_client.create_collection(COLLECTION_NAME_COURSES)

    # Load the course descriptions and embeddings from the JSON file
    courses_base_path = "rag_corpus/2024-2025/courses/"

    description_contents = []
    embeddings = []
    course_ids = []

    for i in range(1,9):
        course_file_path = os.path.join(courses_base_path, f"ug_cat_2024-2025_courses_chunk_{i}.json")
        if not os.path.exists(course_file_path):
            logger.error(f'Course file {course_file_path} does not exist. Skipping.')
            continue
        
        logger.info(f'Loading course descriptions from {course_file_path}')

        courses_file_contents = json.load(open(course_file_path, "r"))
        logger.info(f'Loaded course descriptions from {course_file_path}\n')

        for course in courses_file_contents:
            course_ids.append(course['id'])
            embeddings.append(course['embedding'])
            description_contents.append(course['content'])
        
        logger.info(f'Parsed {len(description_contents)} course descriptions in chunk {i}\n')
    
    logger.info(f'Parsed {len(description_contents)} course descriptions\n')

    # Add the course descriptions to the ChromaDB collection in batches
    batch_size = 100
    total_docs = len(description_contents)
    total_added = 0
    
    for i in range(0, total_docs, batch_size):
        end_idx = min(i + batch_size, total_docs)
        
        batch_descriptions = description_contents[i:end_idx]
        batch_embeddings = embeddings[i:end_idx]
        batch_ids = course_ids[i:end_idx]
        
        try:
            collection.add(
                documents=batch_descriptions,  # content of each doc
                ids=batch_ids,  # unique for each doc
                # metadatas=,  # TODO add course IDs as metadata?
                embeddings=batch_embeddings
            )
            
            batch_count = end_idx - i
            total_added += batch_count
            logger.info(f'Added batch {i//batch_size + 1}: {batch_count} course descriptions (total: {total_added}/{total_docs})')
            
        except Exception as e:
            logger.error(f'Error adding batch {i//batch_size + 1} (documents {i+1}-{end_idx}): {e}')
            # You can choose to continue with next batch or break here
            # break  # Uncomment this if you want to stop on first error
            continue

    logger.info(f'Successfully added {total_added} course descriptions to ChromaDB collection: {COLLECTION_NAME_COURSES}\n')