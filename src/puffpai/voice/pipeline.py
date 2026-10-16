"""Voice pipeline orchestrator for PuffPAI.

Wires together wake-word detection, STT, the PuffPAI agent, and TTS
into a continuous listening loop on a background thread.

Audio capture uses ``sounddevice`` (optional dependency).
"""

from __future__ import annotations

import logging
import struct
import threading

from .wake_word import WakeWordDetector
from .stt import STTEngine, StubSTT
from .tts import TTSEngine, StubTTS

logger = logging.getLogger(__name__)

# Graceful import — sounddevice is an optional dependency
try:
    import sounddevice as sd
    _HAS_SOUNDDEVICE = True
except (ImportError, OSError):
    sd = None  # type: ignore[assignment]
    _HAS_SOUNDDEVICE = False

# Audio capture constants
_SAMPLE_RATE = 16000          # 16 kHz
_CHANNELS = 1                  # mono
_DTYPE = 'int16'               # 16-bit signed PCM
_CHUNK_DURATION_S = 0.5        # 500ms chunks
_CHUNK_FRAMES = int(_SAMPLE_RATE * _CHUNK_DURATION_S)

# Voice Activity Detection (VAD) energy threshold
# Silence is typically < 50; speech is typically 200+
_VAD_ENERGY_THRESHOLD = 100


def compute_rms_energy(pcm_bytes: bytes) -> float:
    """Compute RMS energy of 16-bit PCM audio.

    Returns a float ≥ 0.  Higher values = louder audio.
    """
    if len(pcm_bytes) < 2:
        return 0.0
    n_samples = len(pcm_bytes) // 2
    samples = struct.unpack(f'<{n_samples}h', pcm_bytes[:n_samples * 2])
    if not samples:
        return 0.0
    sumsq = sum(s * s for s in samples)
    return (sumsq / n_samples) ** 0.5


class VoicePipeline:
    """Wake word → STT → PuffPAI agent → TTS → speaker.

    Parameters
    ----------
    wake_word : WakeWordDetector | None
        Detector instance.  ``None`` disables wake-word (always-on).
    stt : STTEngine | None
        Speech-to-text engine.  Defaults to ``StubSTT``.
    tts : TTSEngine | None
        Text-to-speech engine.  Defaults to ``StubTTS``.
    agent_fn : callable | None
        A function ``(str) -> str`` that takes user text and returns
        an AI response.  This is the PuffPAI agent entry point.
    vad_threshold : float
        RMS energy threshold for voice activity detection.
    """

    def __init__(self, wake_word=None, stt=None, tts=None, agent_fn=None,
                 vad_threshold: float = _VAD_ENERGY_THRESHOLD):
        self.wake_word = wake_word or WakeWordDetector()
        self.stt = stt or StubSTT()
        self.tts = tts or StubTTS()
        self.agent_fn = agent_fn or (lambda text: f"Echo: {text}")
        self.vad_threshold = vad_threshold
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_ready(self) -> bool:
        """True when all components are available."""
        return self.stt.is_available() and self.tts.is_available()

    def process_utterance(self, audio: bytes) -> str | None:
        """Run the full pipeline on a single audio utterance.

        1. Transcribe audio → text
        2. Send text to agent
        3. Speak response via TTS
        4. Return the response text

        Returns None if transcription produces empty text.
        """
        text = self.stt.transcribe(audio)
        if not text.strip():
            return None

        response = self.agent_fn(text)
        self.tts.speak(response)
        return response

    def start(self):
        """Start the continuous listening loop on a daemon thread.

        If ``sounddevice`` is not installed, logs a warning and returns.
        """
        if self._running:
            logger.warning("VoicePipeline already running")
            return

        if not _HAS_SOUNDDEVICE:
            logger.warning(
                "sounddevice not installed; voice pipeline cannot capture audio. "
                "Install with: pip install sounddevice"
            )
            self._running = True  # Mark as "started" but no capture
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="PuffPAI-VoicePipeline"
        )
        self._thread.start()
        logger.info("VoicePipeline started (listening on mic)")

    def stop(self):
        """Stop the listening loop and join the thread."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("VoicePipeline stopped")

    def _listen_loop(self):
        """Main audio capture loop running on the background thread.

        Flow:
          1. Capture audio chunks (500ms at 16kHz)
          2. Compute RMS energy for voice activity detection
          3. Check wake word on each chunk
          4. After wake word detected, accumulate utterance until silence
          5. Process complete utterance through STT → agent → TTS
        """
        logger.info("Audio capture loop starting (rate=%d, chunk=%d frames)",
                     _SAMPLE_RATE, _CHUNK_FRAMES)

        wake_detected = False
        utterance_buffer = bytearray()
        silence_chunks = 0

        while not self._stop_event.is_set():
            try:
                # Record one chunk
                audio_data = sd.rec(
                    _CHUNK_FRAMES, samplerate=_SAMPLE_RATE,
                    channels=_CHANNELS, dtype=_DTYPE, blocking=True,
                )
                pcm_bytes = audio_data.tobytes()

                energy = compute_rms_energy(pcm_bytes)

                if not wake_detected:
                    # Listen for wake word
                    if self.wake_word.detect(pcm_bytes):
                        wake_detected = True
                        utterance_buffer.clear()
                        silence_chunks = 0
                        logger.info("Wake word detected — recording utterance")
                else:
                    # Recording utterance
                    utterance_buffer.extend(pcm_bytes)

                    if energy < self.vad_threshold:
                        silence_chunks += 1
                    else:
                        silence_chunks = 0

                    # 3 consecutive silent chunks (~1.5s) → end of utterance
                    if silence_chunks >= 3 and len(utterance_buffer) > 0:
                        logger.info("Silence detected — processing utterance (%d bytes)",
                                    len(utterance_buffer))
                        try:
                            self.process_utterance(bytes(utterance_buffer))
                        except Exception as e:
                            logger.error("Error processing utterance: %s", e)
                        wake_detected = False
                        utterance_buffer.clear()
                        silence_chunks = 0

            except Exception as e:
                if not self._stop_event.is_set():
                    logger.error("Audio capture error: %s", e)
                break

        logger.info("Audio capture loop ended")
