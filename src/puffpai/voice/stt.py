"""Speech-to-text engines for PuffPAI.

Implements:
  - STTEngine (ABC): common interface
  - StubSTT: placeholder for dev/testing
  - VoskSTT: offline speech recognition via Vosk (Apache 2.0)
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Graceful import — vosk is an optional dependency
try:
    import vosk
    _HAS_VOSK = True
except ImportError:
    vosk = None  # type: ignore[assignment]
    _HAS_VOSK = False


class STTEngine(ABC):
    """Abstract speech-to-text interface.

    Subclasses implement ``transcribe()`` for a specific backend
    (Whisper.cpp, Vosk, etc.).
    """

    @abstractmethod
    def transcribe(self, audio: bytes) -> str:
        """Convert raw audio to text.

        Parameters
        ----------
        audio : bytes
            Raw PCM audio data (16-bit, 16 kHz mono).

        Returns
        -------
        str
            Transcribed text.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when the backend model is loaded and ready."""
        ...


class StubSTT(STTEngine):
    """Placeholder STT that always returns empty string.

    Used during development when no audio hardware / model is present.
    """

    def transcribe(self, audio: bytes) -> str:
        return ""

    def is_available(self) -> bool:
        return False


class VoskSTT(STTEngine):
    """Offline speech-to-text using Vosk (Apache 2.0).

    Requires:
        - ``vosk`` Python package (``pip install vosk``)
        - A Vosk model directory (e.g. ``models/vosk-model-small-en-us``)

    Args:
        model_path: Path to the Vosk model directory.
        sample_rate: Audio sample rate in Hz (default 16000).
    """

    def __init__(self, model_path: str | None = None,
                 sample_rate: int = 16000):
        if model_path is None:
            try:
                from puffpai.config import get_voice_models
                model_path = get_voice_models()["vosk_model_dir"]
            except Exception:
                model_path = "models/vosk-model-small-en-us"
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._model = None
        self._loaded = False
        self._load_error: str | None = None

        if not _HAS_VOSK:
            self._load_error = "vosk not installed"
            logger.warning("vosk not installed; VoskSTT unavailable")
            return

        if not os.path.isdir(model_path):
            self._load_error = f"Model directory not found: {model_path}"
            logger.warning("Vosk model not found: %s", model_path)
            return

        try:
            # Suppress Vosk's own logging during model load
            vosk.SetLogLevel(-1)
            self._model = vosk.Model(model_path)
            self._loaded = True
            logger.info("Vosk model loaded: %s", model_path)
        except Exception as e:
            self._load_error = str(e)
            logger.error("Failed to load Vosk model %s: %s", model_path, e)

    def transcribe(self, audio: bytes) -> str:
        """Transcribe PCM 16-bit 16kHz mono audio to text."""
        if not self._loaded or self._model is None:
            return ""

        try:
            recognizer = vosk.KaldiRecognizer(self._model, self._sample_rate)
            recognizer.AcceptWaveform(audio)
            result = json.loads(recognizer.FinalResult())
            return result.get("text", "").strip()
        except Exception as e:
            logger.error("Vosk transcription error: %s", e)
            return ""

    def is_available(self) -> bool:
        return self._loaded and self._model is not None
