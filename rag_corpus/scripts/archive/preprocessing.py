# Remove all objects from a weaviate database collection
from weaviate.classes.query import Filter
import os
from dotenv import load_dotenv
from weaviate.classes.init import Auth, AdditionalConfig, Timeout
import weaviate
import openai
from openai import OpenAI

#One contiguous section of a corpus document
class Chunk:
    properties: dict[str, str]
    vector_embedding: list[float]

    def __init__(self) -> None:
        self.properties: dict[str, str] = {}
        self.vector_embedding: list[float] = []
    
    def populate_vector_embedding(
            self,
            embedding_client,
            model,
            encoding_format) -> None:

        self.vector_embedding = embedding_client.embeddings.create(
            input = [self.properties['chunk_text']],
            model = model,
            encoding_format = encoding_format).data[0].embedding


#One specific corpus document staged as markdown
# class Doc:
    
#     def __init__(self) -> None:
#         self.properties: dict[str, str] = {}

#     # Chunk document into pieces with a specific number of characters
#     def to_chunks_by_character(
#         self,
#         chunk_size = 2048,
#         chunk_offset = 1024) -> list[Chunk]:

#         doc_text = self.properties['doc_text']
#         base_properties = self.properties.copy()
#         base_properties.pop('doc_text')
#         chunks = []

#         chunk_number = 0
#         while len(doc_text) > 0:

#             #Create chunk & add it to this list
#             chunk = Chunk()
#             chunk.properties = base_properties.copy()
#             chunk.properties['chunk_number'] = chunk_number
#             chunk.properties['chunk_text'] = doc_text[0:chunk_size]
#             chunks.append(chunk)

#             # Move to next chunk window & iterate chunk number
#             doc_text = doc_text[chunk_offset:]
#             chunk_number = chunk_number + 1

#         return chunks
    
#     def chunk_embed_index(
#         doc : Doc,
#         chunk_size = 2048,
#         chunk_offset = 1024,
#         embedding_client = None,
#         embedding_model = None,
#         encoding_format = None,
#         weaviate_client = None,
#         collection_name = None) -> None:
#         """"
#         "Chunk, embed, and index a document in Weaviate."
#         """

#         # Chunk document into pieces with a specific number of characters
#         chunks = doc.to_chunks_by_character(
#             chunk_size=chunk_size,
#             chunk_offset=chunk_offset)
#         print(f"Number of chunks: {len(chunks)}")
#         # Get chunk embeddings

#         for chunk in chunks:
#             chunk.populate_vector_embedding(
#                 embedding_client=embedding_client,
#                 model=embedding_model,
#                 encoding_format=encoding_format)
            
#         for chunk in chunks:
#             print(len(chunk.properties['chunk_text']))
#             properties_ex_text = chunk.properties.copy()
#             properties_ex_text.pop('chunk_text')
#             print(properties_ex_text)
#             print(chunk.vector_embedding)
#             print(len(chunk.vector_embedding))
        
#         # Index chunks in vector database
#         collection = weaviate_client.collections.get(collection_name)
#         for chunk in chunks:
#             uuid = collection.data.insert(
#                 properties=chunk.properties,
#                 vector=chunk.vector_embedding)
#             print(f"Inserted chunk with UUID: {uuid}")


#     def index_chunk(
#         chunk : Chunk,
#         embedding_client = None,
#         embedding_model = None,
#         encoding_format = None,
#         weaviate_client = None,
#         collection_name = None) -> None:
#         """"
#         "embed and index a chunk in Weaviate."
#         """

#         # Get chunk embeddings

#         chunk.populate_vector_embedding(
#                 embedding_client=embedding_client,
#                 model=embedding_model,
#                 encoding_format=encoding_format)
            
#         properties_ex_text = chunk.properties.copy()
#         properties_ex_text.pop('chunk_text')
        
#         # Index chunks in vector database
#         collection = weaviate_client.collections.get(collection_name)

#         uuid = collection.data.insert(
#             properties=chunk.properties,
#             vector=chunk.vector_embedding)
#         print(f"Inserted chunk with UUID: {uuid}")


#     def import_doc_markdown(
#         path: str,
#         catalog_year_start: int,
#         section: str,
#         hyperlink: str):

#         file_text = ""
#         if path.endswith(".md"):
#             #Open file
#             file = open(path, 'r')
            
#             #Read lines
#             lines = file.readlines()

#             #Join lines into a single string
#             file_text = "".join(lines)

#             print(path)
#             print(len(lines))
#             print(len(file_text))

#             #Clean up by closing file
#             file.close()

#         doc = Doc()
#         doc.properties = {
#             'doc_text': file_text,
#             'catalog_year_start': catalog_year_start,
#             'section': section,
#             'hyperlink': hyperlink
#         }
#         return doc

def openai_extract_vector(
        response
    ) -> list[float]:

    return response.data[0].embedding

class PrepOpenAIWeaviate:

    def initialize(self):
        # Load environment variables
        load_dotenv()

        # Embedding
        self.EMBEDDING_MODEL_NAME = "text-embedding-ada-002"
        self.ENCODING_FORMAT = "float"

        # Models
        self.RESPONSE_GENERATIVE_MODEL_NAME = "gpt-4o"

        #Keys and URLs for Embedding and Generative Models
        self.OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
        self.OPENAI_EMBEDDING_URL = os.getenv('OPENAI_EMBEDDING_URL')
        self.OPENAI_GENERATION_URL = os.getenv('OPENAI_GENERATION_URL')

        #Keys and URLS for Vector Databases
        self.WEAVIATE_ENDPOINT_URL = os.getenv('WEAVIATE_ENDPOINT_URL')
        self.WEAVIATE_API_KEY = os.getenv('WEAVIATE_API_KEY')        

        self.weaviate_client = weaviate.connect_to_weaviate_cloud(
            cluster_url = self.WEAVIATE_ENDPOINT_URL,
            auth_credentials = Auth.api_key(self.WEAVIATE_API_KEY),
            additional_config=AdditionalConfig(timeout=Timeout(
                init=30,
                query=60,
                insert=120))  # Values in seconds
        )

        print('weavaite ready?' + self.weaviate_client.is_ready())

        self.embedding_client = OpenAI(api_key=self.OPENAI_API_KEY)

        self.generation_client = OpenAI(api_key=self.OPENAI_API_KEY)

    def __init__(self):
        self.initialize()
        


    def get_collections(self):
        # List all collections in Weaviate
        return self.weaviate_client.collections.list_all()
        




class Database:

    
    def remove_all_objects_from_weaviate(weaviate_client, collection_name):
        
        collection = weaviate_client.collections.get(collection_name)

        response = collection.query.fetch_objects(limit=500)
        ids = [o.uuid for o in response.objects] 

        if len(ids) == 0:
            return
        
        else:
            # Delete all retrieved objects
            collection.data.delete_many(
                where=Filter.by_id().contains_any(ids)
            )
            Database.remove_all_objects_from_weaviate(weaviate_client, collection_name)    


if __name__ == "__main__":
    print("This module is meant to be imported, not run directly.")



