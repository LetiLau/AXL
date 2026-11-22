import io
import wave
from typing import Tuple
import numpy as np


def normalize_pcm_for_stt(
    pcm_samples: np.ndarray,
    silence_rms_threshold: float = 0.0015,
    max_gain: float = 15.0,
) -> Tuple[np.ndarray, float, float, bool]:
    """
    Removes DC offset, checks RMS silence gate, and peak-normalizes audio for far-field STT.
    Returns (normalized_float32, rms, applied_gain, is_silence).
    """
    if len(pcm_samples) == 0:
        return np.array([], dtype=np.float32), 0.0, 1.0, True

    if pcm_samples.dtype == np.int16 or np.max(np.abs(pcm_samples)) > 1.0:
        audio_float = pcm_samples.astype(np.float32) / 32768.0
    else:
        audio_float = pcm_samples.astype(np.float32)

    audio_float = audio_float - float(np.mean(audio_float))
    rms = float(np.sqrt(np.mean(audio_float ** 2)))
    if rms < silence_rms_threshold:
        return audio_float, rms, 1.0, True

    peak = float(np.max(np.abs(audio_float)))
    gain = 1.0
    if 1e-6 < peak < 0.85:
        gain = min(0.90 / peak, max_gain)
        audio_float = np.clip(audio_float * gain, -1.0, 1.0)

    return audio_float, rms, gain, False


def pcm_to_wav_bytes(pcm_samples: np.ndarray, sample_rate: int = 16000) -> bytes:
    """
    Encapsulates raw int16 PCM samples into a standard in-memory RIFF/WAV byte stream.
    Eliminates disk I/O latency and storage wear.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_samples.tobytes())
    return buffer.getvalue()