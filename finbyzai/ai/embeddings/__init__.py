"""
Import embedding providers so their @register_embedding decorators execute
when this package (or any submodule like .registry) is imported.
"""

# Register built-in providers
from . import openai_embedding  # noqa: F401
from . import google_embedding  # noqa: F401


