import threading
from typing import Optional
import numpy as np


class AudioRingBuffer:
    """
    Fixed-size thread-safe circular buffer for raw PCM16 audio stream.
    Zero dynamic re-allocations during continuous streaming ingestion.
    """

    def __init__(self, capacity_seconds: float = 10.0, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.capacity_samples = int(capacity_seconds * sample_rate)
        self.buffer = np.zeros(self.capacity_samples, dtype=np.int16)
        
        self._write_pos = 0
        self._total_samples_written = 0
        self._lock = threading.Lock()

    def write(self, pcm_bytes: bytes) -> int:
        """
        Appends raw PCM16 little-endian bytes to the circular buffer.
        Returns the number of audio samples written.
        """
        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        n_samples = len(samples)
        
        if n_samples == 0:
            return 0

        with self._lock:
            if n_samples >= self.capacity_samples:
                # Payload exceeds capacity: keep only newest samples that fit
                samples = samples[-self.capacity_samples:]
                self.buffer[:] = samples
                self._write_pos = 0
                self._total_samples_written += n_samples
                return n_samples

            # Linear append vs Wrap-around append
            end_pos = self._write_pos + n_samples
            if end_pos <= self.capacity_samples:
                self.buffer[self._write_pos:end_pos] = samples
            else:
                chunk1_len = self.capacity_samples - self._write_pos
                chunk2_len = n_samples - chunk1_len
                self.buffer[self._write_pos:] = samples[:chunk1_len]
                self.buffer[:chunk2_len] = samples[chunk1_len:]

            self._write_pos = (self._write_pos + n_samples) % self.capacity_samples
            self._total_samples_written += n_samples

        return n_samples

    def get_last_samples(self, n_samples: int) -> np.ndarray:
        """
        Retrieves the most recent n_samples as an int16 contiguous array.
        """
        with self._lock:
            n_samples = min(n_samples, self.capacity_samples, self._total_samples_written)
            if n_samples == 0:
                return np.empty(0, dtype=np.int16)

            start_pos = (self._write_pos - n_samples) % self.capacity_samples
            if start_pos + n_samples <= self.capacity_samples:
                return self.buffer[start_pos:start_pos + n_samples].copy()
            else:
                chunk1 = self.buffer[start_pos:]
                chunk2 = self.buffer[:(start_pos + n_samples) % self.capacity_samples]
                return np.concatenate((chunk1, chunk2))

    def get_last_float32(self, n_samples: int) -> np.ndarray:
        """
        Retrieves the most recent n_samples normalized to float32 [-1.0, 1.0].
        Directly ready for ONNX inference (Silero VAD / openWakeWord).
        """
        raw_int16 = self.get_last_samples(n_samples)
        return raw_int16.astype(np.float32) / 32768.0

    def clear(self) -> None:
        """Resets write pointers and zeroes internal storage."""
        with self._lock:
            self.buffer.fill(0)
            self._write_pos = 0
            self._total_samples_written = 0

    @property
    def total_samples_written(self) -> int:
        return self._total_samples_written