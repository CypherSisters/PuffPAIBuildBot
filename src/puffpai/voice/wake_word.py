"""Wake word detector for PuffPAI.

Loads the existing ``models/hey_puffpai.tflite`` Conv1D model
(63.8 KB, trained with Porcupine-style architecture) and provides a
``detect()`` interface.

Sprint 2 skeleton — the real audio capture loop will be added in
the Linux/ARM phase when microphone hardware is available.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MODEL_PATH = _REPO_ROOT / "models" / "hey_puffpai.tflite"


def _default_wake_word_path() -> Path:
    """Resolve the wake word model path from config or fallback."""
    try:
        from puffpai.config import get_voice_models
        return Path(get_voice_models()["wake_word_model_path"])
    except Exception:
        return _MODEL_PATH


class WakeWordDetector:
    """Detect the 'Hey PuffPAI' wake word from an audio stream.

    Parameters
    ----------
    model_path : str | Path | None
        Path to the TFLite model file.  Defaults to the value
        from config (``models.wake_word_model_path``), falling back
        to ``models/hey_puffpai.tflite`` relative to repo root.
    threshold : float
        Confidence threshold for a positive detection (0-1).
    """

    def __init__(self, model_path=None, threshold: float = 0.5):
        self.model_path = Path(model_path) if model_path else _default_wake_word_path()
        self.threshold = threshold
        self._interpreter = None

        if self.model_path.exists():
            self._load_model()

    def _load_model(self):
        """Attempt to load the TFLite model. Graceful fail if no runtime."""
        try:
            import tflite_runtime.interpreter as tflite
            self._interpreter = tflite.Interpreter(
                model_path=str(self.model_path))
            self._interpreter.allocate_tensors()
        except ImportError:
            try:
                import tensorflow as tf
                self._interpreter = tf.lite.Interpreter(
                    model_path=str(self.model_path))
                self._interpreter.allocate_tensors()
            except ImportError:
                # No TFLite runtime — detector will be non-functional
                self._interpreter = None

    @property
    def is_loaded(self) -> bool:
        """True when the wake-word model is loaded and ready."""
        return self._interpreter is not None

    def detect(self, audio_chunk: bytes) -> bool:
        """Run detection on a raw audio chunk.

        Parameters
        ----------
        audio_chunk : bytes
            Raw PCM audio data (16-bit, 16 kHz mono).

        Returns
        -------
        bool
            True if the wake word was detected with confidence ≥ threshold.

        Note
        ----
        Stub implementation — always returns False until the audio
        preprocessing pipeline is connected.
        """
        if not self.is_loaded:
            return False

        # TODO: Preprocess audio_chunk → mel spectrogram → model input
        # TODO: Run inference, compare output to self.threshold
        return False
