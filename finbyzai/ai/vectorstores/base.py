from typing import List, Dict, Any,Callable

class BaseVectorStore:
    """Standard interface that all vector store adapters must implement."""

    def __init__(self, kb_name: str,description:str, embedding_func: Callable, api_key: str = None, **kwargs):
        self.kb_name = kb_name
        self.embedding = embedding_func
        self.api_key = api_key
        self.description =description
        # other kwargs may include url, persist_directory, etc.

    def upsert(self, texts: List[str], metadatas: List[Dict[str, Any]], ids: List[str]) -> int:
        """Insert or update vectors. Return number of items upserted."""
        raise NotImplementedError

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Return list of {id, score, metadata} results."""
        raise NotImplementedError

    def delete(self, filter: Dict[str, Any]) -> None:
        """Delete all vectors matching the given metadata filter."""
        raise NotImplementedError

    def as_tool(self):
        raise NotImplementedError
