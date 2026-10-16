"""
PuffPAI Inference Package
Abstraction layer for LLM backends.

Development:  OllamaBackend (HTTP to local Ollama server)
Production:   LlamaCppBackend (direct llama.cpp via llama-cpp-python)
"""

from .base import LLMBackend
from .ollama_backend import OllamaBackend
from .llamacpp_backend import LlamaCppBackend

__all__ = ["LLMBackend", "OllamaBackend", "LlamaCppBackend"]
