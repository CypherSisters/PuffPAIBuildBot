"""Text-to-speech engines for PuffPAI.

Implements:
  - TTSEngine (ABC): common interface
  - StubTTS: placeholder for dev/testing
  - PiperTTS: offline speech synthesis via Piper (Apache 2.0)
"""

from __future__ import annotations

import io
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Graceful import — piper-tts is an optional dependency
try:
    import piper
    _HAS_PIPER = True
except ImportError:
    piper = None  # type: ignore[assignment]
    _HAS_PIPER = False


class TTSEngine(ABC):
    """Abstract text-to-speech interface.

    Subclasses implement ``speak()`` for a specific backend
    (Piper, eSpeak, etc.).
    """

    @abstractmethod
    def speak(self, text: str) -> bytes | None:
        """Convert text to audio.

        Parameters
        ----------
        text : str
            The text to synthesise.

        Returns
        -------
        bytes | None
            Raw PCM audio data (16-bit, 22050 Hz mono) or None if
            the engine plays directly to the speaker.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when the backend model is loaded and ready."""
        ...


class StubTTS(TTSEngine):
    """Placeholder TTS that prints to console.

    Used during development when no audio hardware / model is present.
    """

    def speak(self, text: str) -> bytes | None:
        logger.debug("[TTS stub] %s", text)
        return None

    def is_available(self) -> bool:
        return False


class PiperTTS(TTSEngine):
    """Offline text-to-speech using Piper (Apache 2.0).

    Requires:
        - ``piper-tts`` Python package (``pip install piper-tts``)
        - An ONNX voice model (e.g. ``models/en_US-lessac-medium.onnx``)

    Args:
        model_path: Path to the Piper ONNX model file.
    """

    def __init__(self, model_path: str | None = None):
        global piper, _HAS_PIPER
        if model_path is None:
            try:
                from puffpai.config import get_voice_models
                model_path = get_voice_models()["piper_model_path"]
            except Exception:
                model_path = "models/en_US-lessac-medium.onnx"
        self._model_path = model_path
        self._voice = None
        self._loaded = False
        self._load_error: str | None = None
        self._sample_rate: int = 22050

        if not _HAS_PIPER:
            # The module-level import can fail transiently during a service
            # restart (the dying process still holds onnxruntime's DLLs on
            # Windows). Retry here instead of declaring TTS dead for the
            # whole process lifetime on a voice-first device.
            try:
                import importlib
                piper = importlib.import_module("piper")
                _HAS_PIPER = True
            except ImportError as exc:
                self._load_error = f"piper import failed: {exc}"
                logger.warning(
                    "piper import failed; PiperTTS unavailable (%s)", exc)
                return

        if not os.path.isfile(model_path):
            self._load_error = f"Model file not found: {model_path}"
            logger.warning("Piper model not found: %s", model_path)
            return

        try:
            self._voice = piper.PiperVoice.load(model_path)
            self._loaded = True
            # Real sample rate lives in the voice config (22050 for medium/
            # high voices, but don't assume)
            try:
                self._sample_rate = int(self._voice.config.sample_rate)
            except Exception:
                pass
            logger.info("Piper voice loaded: %s (%d Hz)",
                        model_path, self._sample_rate)
        except Exception as e:
            self._load_error = str(e)
            logger.error("Failed to load Piper model %s: %s", model_path, e)

    def speak(self, text: str) -> bytes | None:
        """Synthesise text into PCM 16-bit audio bytes.

        Supports both piper-tts APIs (found via diagnose_voice.py):
          - old (<= 1.2): ``synthesize_stream_raw(text)`` yields raw PCM
            bytes chunks
          - new (>= 1.3): ``synthesize(text)`` yields AudioChunk objects
            with ``.audio_int16_bytes``
        The installed package is the new API, so every call raised
        AttributeError and the dashboard silently fell back to the browser
        voice (no lipsync possible).
        """
        if not self._loaded or self._voice is None:
            return None

        try:
            audio_buf = io.BytesIO()
            if hasattr(self._voice, "synthesize_stream_raw"):
                # Old API: raw PCM byte chunks
                for chunk in self._voice.synthesize_stream_raw(text):
                    audio_buf.write(chunk)
            else:
                # New API: AudioChunk objects
                for chunk in self._voice.synthesize(text):
                    data = getattr(chunk, "audio_int16_bytes", None)
                    if data is None:
                        # Very old/odd builds may yield raw bytes here
                        data = chunk if isinstance(chunk, (bytes, bytearray)) else b""
                    audio_buf.write(data)
                    rate = getattr(chunk, "sample_rate", None)
                    if rate:
                        self._sample_rate = int(rate)
            audio = audio_buf.getvalue()
            return audio or None
        except Exception as e:
            logger.error("Piper TTS error: %s", e)
            return None

    def is_available(self) -> bool:
        return self._loaded and self._voice is not None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate
