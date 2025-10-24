from typing import List, Union
from abc import ABC, abstractmethod

class BaseEmbedding(ABC):
    """Standard interface for embedding providers"""

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """Return embedding vector for a single query string."""
        raise NotImplementedError

    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Return embedding vectors for multiple documents."""
        raise NotImplementedError

    def __call__(self, inputs: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        if isinstance(inputs, str):
            return self.embed_query(inputs)
        return self.embed_documents(inputs)
