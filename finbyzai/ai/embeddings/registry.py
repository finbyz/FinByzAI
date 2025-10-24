from typing import Dict, Type
from .base import BaseEmbedding

_registry: Dict[str, Type[BaseEmbedding]] = {}

def register_embedding(provider_name: str):
    """Decorator to register an embedding provider class"""
    def decorator(cls: Type[BaseEmbedding]):
        _registry[provider_name.lower()] = cls
        return cls
    return decorator

def create_embedding(provider_name: str, **kwargs) -> BaseEmbedding:
    provider = provider_name.lower()
    if provider not in _registry:
        raise ValueError(f"Embedding provider '{provider_name}' not registered. Available: {list(_registry.keys())}")
    return _registry[provider](**kwargs)

def available_embeddings():
    return list(_registry.keys())
