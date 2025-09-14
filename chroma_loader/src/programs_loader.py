import os
import json
import chardet
import pandas as pd

def load_chroma_degree_programs(
    chroma_client,
    COLLECTION_NAME_DEGREE_PROGRAMS,
    EMBEDDING_MODEL_NAME,  # TODO add support for different embedding models
    logger):
    
    # Create a new collection for degree programs data
    logger.info(f'Loading programs into ChromaDB collection: {COLLECTION_NAME_DEGREE_PROGRAMS}')
    collection = chroma_client.create_collection(COLLECTION_NAME_DEGREE_PROGRAMS)

    data_base_path = 'rag_corpus/ug_cat/'
    metadata_file_path = 'rag_corpus/ug_cat/ug_cat_metadata.csv'

    # Detect encoding and read csv
    with open(metadata_file_path, 'rb') as f:
        result = chardet.detect(f.read(10000))  # Read first 10KB to detect
        encoding = result['encoding']
        logger.info(f"Detected encoding: {encoding}")
    df = pd.read_csv(metadata_file_path, encoding=encoding)

    # Identify unique catalog years
    catalog_years = df['catalog_year'].unique()
    logger.info(f'Found {len(catalog_years)} unique catalog years: {catalog_years}')
    if len(catalog_years) != 5:
        logger.info(f'Warning: Expected 5 unique catalog years, found {len(catalog_years)}. Please check the metadata file.')

    number_of_files = 0

    for catalog_year in catalog_years:
        
        df_year = df[df['catalog_year'] == catalog_year]
        logger.info(f'Processing catalog year: {catalog_year}')

        year_base_path = os.path.join(data_base_path, catalog_year, 'programs')
        year_base_path_embeddings = os.path.join(year_base_path, 'embeddings', EMBEDDING_MODEL_NAME)

        for index, row in df_year.iterrows():
            # Construct the input file path for each row
            file_name = row['file_name']

            content_file_path = os.path.join(year_base_path, file_name + '.md')
            embeddings_file_path = os.path.join(year_base_path_embeddings, file_name + '.json')

            # logger.info(f'Processing content file {content_file_path} and embeddings file {embeddings_file_path}')
            
            # Open content and embeddings files
            with open(content_file_path, 'r', encoding='utf-8') as content_file:
                content = content_file.read()

            with open(embeddings_file_path, 'r', encoding='utf-8') as embeddings_file:
                embeddings_json = json.load(embeddings_file)
            
            # Add the file to the ChromaDB collection
            collection.add(
                documents = [content],  # content of each doc
                ids = [catalog_year + '_' + file_name],  # unique for each doc
                metadatas=[{"catalog_year": catalog_year}],  # metadata for each doc
                embeddings = embeddings_json.get('embedding')  # embedding for the doc
            )

            # Log the addition of the file
            logger.info(f'Added file {file_name} for catalog year {catalog_year} to ChromaDB collection\n')
            number_of_files += 1        
        
        logger.info(f'Added {number_of_files} files to ChromaDB collection\n')
