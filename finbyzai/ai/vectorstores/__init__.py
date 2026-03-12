"""
Import vector store adapters so their @register_vector_store decorators execute
when this package (or any submodule like .registry) is imported.
"""

# Register built-in adapters
from . import chroma_adapter  # noqa: F401
# from . import pinecone_adapter  # noqa: F401
# from . import qdrant_adapter  # noqa: F401
from . import pinecone_adapter  # noqa: F401

