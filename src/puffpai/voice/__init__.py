"""PuffPAI Voice pipeline — wake word, STT, TTS, orchestration.

Sprint 2: interfaces and stubs.
Sprint 3: VoskSTT, PiperTTS, threaded audio capture.
"""

from .wake_word import WakeWordDetector
from .stt import STTEngine, VoskSTT
from .tts import TTSEngine, PiperTTS
from .pipeline import VoicePipeline
from .audio_devices import AudioDeviceManager, DeviceInfo, MicTestResult

__all__ = [
    "WakeWordDetector", "STTEngine", "VoskSTT",
    "TTSEngine", "PiperTTS", "VoicePipeline",
    "AudioDeviceManager", "DeviceInfo", "MicTestResult",
]
