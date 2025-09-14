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