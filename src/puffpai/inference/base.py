"""
Abstract base class for LLM backends.
Allows swapping between Ollama (dev) and llama.cpp (production).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generator, TypedDict


class ChatMessage(TypedDict):
    """A single chat message in the OpenAI-compatible format."""
    role: str
    content: str


class LLMBackend(ABC):
    """Abstract LLM backend interface.

    All PuffPAI code should use this interface for LLM access.
    Never call Ollama/llama.cpp directly -- always go through a backend.
    """

    @abstractmethod
    def chat(self, messages: list[ChatMessage], temperature: float = 0.7,
             max_tokens: int = 2048) -> str:
        """Send a chat completion request.

        Args:
            messages: List of ChatMessage dicts with ``role`` and ``content``.
            temperature: Sampling temperature (0.0-1.0).
            max_tokens: Maximum response tokens.

        Returns:
            The assistant's response text.
        """
        ...

    def chat_stream(self, messages: list[ChatMessage], temperature: float = 0.7,
                     max_tokens: int = 2048) -> Generator[str, None, None]:
        """Stream chat completion tokens as they arrive.

        Default implementation falls back to non-streaming chat().
        Backends should override this for real token-by-token streaming.

        Yields:
            Individual token strings as they are generated.
        """
        yield self.chat(messages, temperature, max_tokens)

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for text.

        Args:
            text: The text to embed.

        Returns:
            List of floats representing the embedding.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the backend is reachable and ready."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the name of the currently loaded model."""
        ...
