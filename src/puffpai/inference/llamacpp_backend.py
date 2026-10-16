"""
llama.cpp LLM Backend for PuffPAI (Production)

Direct local inference via llama-cpp-python (MIT license).
Used on edge hardware (RK3588, Jetson, x86) without Ollama.
Loads GGUF models from local filesystem.
"""

from __future__ import annotations

import os
import logging

from .base import LLMBackend

logger = logging.getLogger(__name__)

# Graceful import — llama-cpp-python is an optional dependency
try:
    from llama_cpp import Llama
    _HAS_LLAMA_CPP = True
except ImportError:
    _HAS_LLAMA_CPP = False
    Llama = None  # type: ignore[assignment,misc]


class LlamaCppBackend(LLMBackend):
    """llama.cpp backend for production edge inference.

    Loads a GGUF model directly via llama-cpp-python.
    Falls back gracefully if the library is not installed.

    Args:
        model_path: Absolute path to a .gguf model file.
        n_ctx: Context window size in tokens (default 2048).
        n_gpu_layers: Number of layers to offload to GPU (0 = CPU only).
        verbose: Whether llama.cpp prints progress to stderr.
    """

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 2048,
        n_gpu_layers: int = 0,
        verbose: bool = False,
    ):
        self._model_path = model_path
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._verbose = verbose
        self._llm: "Llama | None" = None
        self._loaded = False
        self._load_error: str | None = None

        if _HAS_LLAMA_CPP and os.path.isfile(model_path):
            try:
                self._llm = Llama(
                    model_path=model_path,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
                self._loaded = True
                logger.info("Loaded model: %s (ctx=%d, gpu_layers=%d)",
                            model_path, n_ctx, n_gpu_layers)
            except Exception as e:
                self._load_error = str(e)
                logger.error("Failed to load model %s: %s", model_path, e)
        elif not _HAS_LLAMA_CPP:
            self._load_error = "llama-cpp-python not installed"
            logger.warning("llama-cpp-python not installed; LlamaCppBackend unavailable")
        elif not os.path.isfile(model_path):
            self._load_error = f"Model file not found: {model_path}"
            logger.warning("Model file not found: %s", model_path)

    def chat(self, messages: list, temperature: float = 0.7,
             max_tokens: int = 2048) -> str:
        """Run chat completion via llama.cpp."""
        if not self._loaded or self._llm is None:
            return f"[Error] LlamaCpp not available: {self._load_error}"

        try:
            response = self._llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("LlamaCpp chat error: %s", e)
            return f"[Error] LlamaCpp inference failed: {e}"

    def embed(self, text: str) -> list:
        """Generate embedding via llama.cpp."""
        if not self._loaded or self._llm is None:
            return []

        try:
            result = self._llm.create_embedding(input=text)
            # llama-cpp-python returns {"data": [{"embedding": [...]}], ...}
            data = result.get("data", [{}])
            if data:
                return data[0].get("embedding", [])
            return []
        except Exception as e:
            logger.error("LlamaCpp embed error: %s", e)
            return []

    def is_available(self) -> bool:
        """Check if a model is loaded and ready."""
        return self._loaded and self._llm is not None

    @property
    def model_name(self) -> str:
        """Return the model filename (without directory)."""
        return os.path.basename(self._model_path)
