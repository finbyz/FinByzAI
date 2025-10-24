from typing import Optional


VECTOR_STORE_REGISTRY = {}

def register_vector_store(name: str, constructor):
    """
    Register a new vector store.

    name: store identifier (e.g., "weaviate")
    constructor: callable that returns an instance of LangChain store
                 signature: constructor(kb_name, embeddings, api_key, **kwargs)
    """
    VECTOR_STORE_REGISTRY[name.lower()] = constructor



from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain_chroma import Chroma

# Pinecone
register_vector_store("pinecone", lambda kb_name, embeddings, api_key=None, **kwargs: 
                      PineconeVectorStore(index_name=kb_name, embedding=embeddings, client=Pinecone(api_key=api_key)))

# Qdrant
register_vector_store("qdrant", lambda kb_name, embeddings, api_key=None, **kwargs:
                      QdrantVectorStore(client=QdrantClient(url=kwargs.get("url", "http://localhost:6333"), api_key=api_key),
                                        collection_name=kb_name, embedding=embeddings))

# Chroma
register_vector_store("chroma", lambda kb_name, embeddings, api_key=None, **kwargs:
                      Chroma(collection_name=kb_name, embedding_function=embeddings, 
                             persist_directory=kwargs.get("persist_directory", "/tmp/chroma")))
