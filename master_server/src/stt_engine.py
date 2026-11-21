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

from tts_engine import get_default_language

logger = logging.getLogger("AXL-STT")

DEFAULT_MODEL_SIZE = os.getenv("AXL_STT_MODEL", "base")
DEFAULT_DEVICE = os.getenv("AXL_STT_DEVICE", "cpu")
DEFAULT_COMPUTE_TYPE = os.getenv("AXL_STT_COMPUTE_TYPE", "int8")
DEFAULT_LANGUAGE = os.getenv("AXL_STT_LANGUAGE", "").strip().lower() or get_default_language()
DEFAULT_CPU_THREADS = int(os.getenv("AXL_STT_CPU_THREADS", "4"))
INITIAL_PROMPT = (
    "A-X-L, Hey Jarvis, timer, sveglia, metti un timer di 5 minuti, "
    "imposta una sveglia alle 7, che ore sono, che giorno è, Spotify, musica, volume, muto, stop, telemetria."
)


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

    def _resolve_language(self, audio_float: np.ndarray) -> str:
        """
        Returns the target STT language.
        When AXL_STT_LANGUAGE='auto', restricts detection strictly to Italian ('it') or
        English ('en') so short commands cannot be misclassified as Spanish/French/Latin.
        """
        if self.language != "auto":
            return self.language

        try:
            _, _, all_probs = self.model.detect_language(audio_float)
            probs_map = dict(all_probs)
            it_prob = probs_map.get("it", 0.0)
            en_prob = probs_map.get("en", 0.0)
            default_lang = get_default_language()
            if it_prob == en_prob:
                chosen = default_lang
            else:
                chosen = "en" if en_prob > it_prob else "it"
            logger.debug(f"[STT LANG] Auto-detected '{chosen}' (it={it_prob:.2f}, en={en_prob:.2f})")
            return chosen
        except Exception as exc:
            default_lang = get_default_language()
            logger.warning(f"[STT LANG] Language detection failed ({exc}), defaulting to '{default_lang}'.")
            return default_lang

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

        # Fast energy gate: discard near-silence/static hiss before feeding to neural decoder
        rms = float(np.sqrt(np.mean(audio_float ** 2)))
        if rms < 0.003:
            logger.info(f"[STT SILENCE] Audio energy too low (RMS={rms:.5f}), ignoring.")
            return ""

        t0 = time.perf_counter()
        duration_sec = max(0.1, len(audio_float) / 16000.0)
        target_lang = self._resolve_language(audio_float)

        # Transcribe with anti-hallucination and repetition suppression constraints
        segments, info = self.model.transcribe(
            audio_float,
            language=target_lang,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
            hallucination_silence_threshold=0.5,
            vad_filter=False,  # Audio has already been segmented by Silero VAD
            initial_prompt=INITIAL_PROMPT,
        )

        texts = []
        for segment in segments:
            # 1. Filter segments marked as pure noise/silence
            if segment.no_speech_prob > 0.65:
                logger.debug(f"[STT FILTER] Dropped segment with no_speech_prob={segment.no_speech_prob:.2f}")
                continue

            # 2. Filter repetitive hallucination loops (abnormally high compression ratio)
            if segment.compression_ratio > 2.4:
                logger.warning(
                    f"[STT HALLUCINATION FILTER] Dropped repetitive segment "
                    f"(compression_ratio={segment.compression_ratio:.2f}, text=\"{segment.text.strip()[:60]}...\")"
                )
                continue

            clean_text = segment.text.strip()
            if clean_text:
                texts.append(clean_text)

        result_text = " ".join(texts).strip()

        # 3. Sanity check: humans cannot physically speak more than 6.0 words per second
        words = result_text.split()
        if len(words) / duration_sec > 6.0:
            logger.warning(
                f"[STT HALLUCINATION FILTER] Dropped utterance exceeding 6 words/sec "
                f"({len(words)} words in {duration_sec:.2f}s: \"{result_text[:60]}...\")"
            )
            result_text = ""

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[STT DONE] Transcribed {duration_sec:.2f}s audio in {elapsed:.1f} ms | Text: \"{result_text}\""
        )
        return result_text

    async def transcribe(self, audio_data: np.ndarray) -> str:
        """Asynchronously transcribes audio without blocking the event loop."""
        return await asyncio.to_thread(self._transcribe_sync, audio_data)
