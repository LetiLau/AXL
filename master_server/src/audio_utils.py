import io
import wave
import numpy as np


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