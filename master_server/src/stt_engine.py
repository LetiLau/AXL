"""
A-X-L Local Speech-to-Text (STT) Engine powered by faster-whisper (CTranslate2).
Optimized for low-latency CPU inference (INT8 AVX2) on ThinkPad (Haswell i5) and modern workstations.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional, Union

import numpy as np

logger = logging.getLogger("AXL-STT")

DEFAULT_MODEL_SIZE = os.getenv("AXL_STT_MODEL", "base")
DEFAULT_DEVICE = os.getenv("AXL_STT_DEVICE", "cpu")
DEFAULT_COMPUTE_TYPE = os.getenv("AXL_STT_COMPUTE_TYPE", "int8")
DEFAULT_LANGUAGE = os.getenv("AXL_STT_LANGUAGE", "it")
DEFAULT_CPU_THREADS = int(os.getenv("AXL_STT_CPU_THREADS", "4"))
INITIAL_PROMPT = "A-X-L, Jarvis, timer, sveglia, Spotify, telemetria, volume, muto, stop."


class STTEngine:
    """
    Asynchronous, non-blocking wrapper around faster-whisper WhisperModel.
    Runs transcription inside an executor thread to preserve FastAPI's event loop.
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        cpu_threads: int = DEFAULT_CPU_THREADS,
        language: str = DEFAULT_LANGUAGE,
        download_root: Optional[Union[str, Path]] = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.language = language
        self.download_root = str(download_root) if download_root else None
        self.model = None

        logger.info(
            f"[STT INIT] Loading faster-whisper model '{self.model_size}' "
            f"(device: {self.device}, compute: {self.compute_type}, threads: {self.cpu_threads})..."
        )
        self._load_model()
        self._warmup()

    def _load_model(self):
        from faster_whisper import WhisperModel

        t0 = time.perf_counter()
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            download_root=self.download_root,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(f"[STT LOADED] faster-whisper '{self.model_size}' ready in {elapsed:.1f} ms.")

    def _warmup(self):
        """Warm up CTranslate2 GEMM kernels to eliminate cold-start latency on the first utterance."""
        try:
            silence = np.zeros(16000, dtype=np.float32)  # 1 second of silence
            list(
                self.model.transcribe(
                    silence,
                    language=self.language,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                )[0]
            )
            logger.info("[STT WARMUP] Model pre-warmed successfully.")
        except Exception as exc:
            logger.warning(f"[STT WARMUP] Pre-warmup skipped: {exc}")

    def _transcribe_sync(self, audio_data: np.ndarray) -> str:
        """
        Synchronous transcription executed within thread executor.
        Expects 16kHz mono float32 array in [-1.0, 1.0].
        """
        if len(audio_data) == 0:
            return ""

        # Normalize int16 -> float32 if needed
        if audio_data.dtype == np.int16 or np.max(np.abs(audio_data)) > 1.0:
            audio_float = audio_data.astype(np.float32) / 32768.0
        else:
            audio_float = audio_data.astype(np.float32)

        t0 = time.perf_counter()
        segments, info = self.model.transcribe(
            audio_float,
            language=self.language if self.language != "auto" else None,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,  # Audio has already been segmented by Silero VAD
            initial_prompt=INITIAL_PROMPT,
        )

        texts = []
        for segment in segments:
            clean_text = segment.text.strip()
            if clean_text:
                texts.append(clean_text)

        result_text = " ".join(texts).strip()
        elapsed = (time.perf_counter() - t0) * 1000
        duration_sec = len(audio_float) / 16000.0
        logger.info(
            f"[STT DONE] Transcribed {duration_sec:.2f}s audio in {elapsed:.1f} ms | Text: \"{result_text}\""
        )
        return result_text

    async def transcribe(self, audio_data: np.ndarray) -> str:
        """Asynchronously transcribes audio without blocking the event loop."""
        return await asyncio.to_thread(self._transcribe_sync, audio_data)
