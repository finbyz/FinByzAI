from typing import Type, Dict, Any, List
from .base import BaseVectorStore

# Registry map: store_name (str) → adapter class (subclass of BaseVectorStore)
_VECTOR_STORE_REGISTRY: Dict[str, Type[BaseVectorStore]] = {}

def register_vector_store(name: str):
    """
    Class decorator to register a vector store adapter.

    Usage:

        @register_vector_store("pinecone")
        class PineconeAdapter(BaseVectorStore):
            ...
    """
    def decorator(adapter_cls: Type[BaseVectorStore]):
        key = name.lower()
        if key in _VECTOR_STORE_REGISTRY:
            raise ValueError(f"Vector store adapter '{key}' already registered")
        if not issubclass(adapter_cls, BaseVectorStore):
            raise TypeError(f"Adapter class {adapter_cls.__name__} must subclass BaseVectorStore")
        _VECTOR_STORE_REGISTRY[key] = adapter_cls
        return adapter_cls
    return decorator

def get_adapter(store_name: str) -> Type[BaseVectorStore]:
    key = store_name.lower()
    adapter_cls = _VECTOR_STORE_REGISTRY.get(key)
    if adapter_cls is None:
        raise ValueError(f"No adapter registered for vector store '{store_name}'")
    return adapter_cls

def available_vector_stores() -> List[str]:
    """Return list of registered store names."""
    return list(_VECTOR_STORE_REGISTRY.keys())


def create_vector_store(store_name: str, kb_name: str, description: str, embeddings: Any, api_key: str = None, **kwargs) -> BaseVectorStore:
    """
    Factory function to create a vector store adapter by name.
    """
    adapter_cls = get_adapter(store_name)
    # instantiate
    return adapter_cls(kb_name=kb_name, description=description, embeddings=embeddings, api_key=api_key, **kwargs)
