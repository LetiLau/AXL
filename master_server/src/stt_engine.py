"""
A-X-L Speech-to-Text (STT) Engine supporting:
- Groq Cloud Whisper (`whisper-large-v3-turbo`) for 0-RAM lightweight deployments
- Local `faster-whisper` (CTranslate2 INT8 AVX2) for offline workstations
"""

import asyncio
import io
import logging
import os
import time
import wave
from pathlib import Path
from typing import Optional, Union

import numpy as np
import requests

from tts_engine import get_default_language

logger = logging.getLogger("AXL-STT")

DEFAULT_STT_BACKEND = os.getenv("AXL_STT_BACKEND", "local").strip().lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo").strip()
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

DEFAULT_MODEL_SIZE = os.getenv("AXL_STT_MODEL", "base")
DEFAULT_DEVICE = os.getenv("AXL_STT_DEVICE", "cpu")
DEFAULT_COMPUTE_TYPE = os.getenv("AXL_STT_COMPUTE_TYPE", "int8")
DEFAULT_LANGUAGE = os.getenv("AXL_STT_LANGUAGE", "").strip().lower() or get_default_language()
DEFAULT_CPU_THREADS = int(os.getenv("AXL_STT_CPU_THREADS", "4"))
DEFAULT_BEAM_SIZE = int(os.getenv("AXL_STT_BEAM_SIZE", "5"))
DEFAULT_REPETITION_PENALTY = float(os.getenv("AXL_STT_REPETITION_PENALTY", "1.0"))
INITIAL_PROMPT_IT = (
    "A-X-L, riproduci una canzone su Spotify, metti musica, metti un timer di 5 minuti, "
    "imposta una sveglia alle 7, che ore sono, che giorno è, alza il volume, abbassa il volume, "
    "pausa, stop, muto, telemetria, meteo."
)
INITIAL_PROMPT_EN = (
    "A-X-L, play a song on Spotify, play music, set a 5 minute timer, "
    "set an alarm for 7, what time is it, volume up, volume down, "
    "pause, stop, mute, telemetry, weather."
)


class STTBackendError(RuntimeError):
    """Raised when an external STT backend fails (network, auth, quota, HTTP error)."""


class STTEngine:
    """
    Asynchronous, non-blocking Speech-to-Text engine supporting Groq Cloud Whisper
    and local faster-whisper WhisperModel.
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        cpu_threads: int = DEFAULT_CPU_THREADS,
        language: str = DEFAULT_LANGUAGE,
        download_root: Optional[Union[str, Path]] = None,
        backend: str = DEFAULT_STT_BACKEND,
    ):
        self.backend = (backend or "local").strip().lower()
        self.groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo").strip()
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.language = language
        self.download_root = str(download_root) if download_root else None
        self.model = None

        if self.backend == "groq":
            logger.info(
                f"[STT INIT] Groq cloud STT ready (model: {self.groq_model}, 0 MB local Whisper RAM)."
            )
            if not self.groq_api_key or self.groq_api_key.startswith("your_"):
                logger.warning("[STT INIT] GROQ_API_KEY is not yet configured in .env.")
        else:
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

    @staticmethod
    def _apply_highpass_filter(
        audio_float: np.ndarray, sample_rate: int = 16000, cutoff_hz: float = 85.0
    ) -> np.ndarray:
        """
        Zero-phase FFT high-pass filter with a smooth raised-cosine roll-off around cutoff_hz.
        Removes low-frequency room rumble, AC hum, and desk vibrations before RMS gating & gain.
        """
        n = len(audio_float)
        if n < 64:
            return audio_float
        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
        low = cutoff_hz * 0.7
        high = cutoff_hz * 1.3
        gain = np.ones_like(freqs, dtype=np.float32)
        gain[freqs <= low] = 0.0
        trans = (freqs > low) & (freqs < high)
        gain[trans] = 0.5 * (1.0 - np.cos(np.pi * (freqs[trans] - low) / (high - low)))
        filtered = np.fft.irfft(np.fft.rfft(audio_float) * gain, n=n)
        return filtered.astype(np.float32)

    @staticmethod
    def _float_to_wav_bytes(audio_float: np.ndarray, sample_rate: int = 16000) -> bytes:
        """Encodes normalized float32 [-1.0, 1.0] mono audio to 16-bit PCM WAV bytes in memory."""
        pcm_int16 = np.clip(audio_float * 32767.0, -32768, 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_int16.tobytes())
        return buf.getvalue()

    @staticmethod
    def _filter_hallucinated_rate(text: str, duration_sec: float) -> str:
        """Drops transcripts exceeding physically plausible human speech rate (6.0 words/sec)."""
        words = text.split()
        if len(words) / duration_sec > 6.0:
            logger.warning(
                f"[STT HALLUCINATION FILTER] Dropped utterance exceeding 6 words/sec "
                f"({len(words)} words in {duration_sec:.2f}s: \"{text[:60]}...\")"
            )
            return ""
        return text

    def _transcribe_groq_sync(self, audio_float: np.ndarray, rms: float, gain: float) -> str:
        """Transcribes preprocessed 16kHz float32 audio via Groq Cloud Whisper API."""
        if not self.groq_api_key or self.groq_api_key.startswith("your_"):
            raise STTBackendError("GROQ_API_KEY is not configured.")

        t0 = time.perf_counter()
        duration_sec = max(0.1, len(audio_float) / 16000.0)
        wav_bytes = self._float_to_wav_bytes(audio_float, sample_rate=16000)

        use_en_prompt = self.language == "en" or (self.language == "auto" and get_default_language() == "en")
        prompt = INITIAL_PROMPT_EN if use_en_prompt else INITIAL_PROMPT_IT

        data = {
            "model": self.groq_model,
            "temperature": "0",
            "response_format": "json",
            "prompt": prompt,
        }
        if self.language in ("it", "en"):
            data["language"] = self.language

        try:
            resp = requests.post(
                GROQ_STT_URL,
                headers={"Authorization": f"Bearer {self.groq_api_key}"},
                files={"file": ("utterance.wav", wav_bytes, "audio/wav")},
                data=data,
                timeout=10.0,
            )
        except requests.RequestException as exc:
            raise STTBackendError(f"Groq STT request failed: {exc}") from exc

        if resp.status_code != 200:
            raise STTBackendError(f"Groq STT HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            result_text = (resp.json().get("text") or "").strip()
        except ValueError as exc:
            raise STTBackendError(f"Groq STT invalid JSON response: {exc}") from exc

        result_text = self._filter_hallucinated_rate(result_text, duration_sec)
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[STT GROQ DONE] Transcribed {duration_sec:.2f}s audio in {elapsed:.1f} ms "
            f"(rms={rms:.4f}, gain={gain:.1f}x, model={self.groq_model}) | Text: \"{result_text}\""
        )
        return result_text

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

        # Remove DC offset and sub-85Hz room rumble before energy gating & peak normalization
        audio_float = audio_float - float(np.mean(audio_float))
        audio_float = self._apply_highpass_filter(audio_float, sample_rate=16000, cutoff_hz=85.0)

        # Fast energy gate: discard near-silence/static hiss before feeding to neural decoder
        rms = float(np.sqrt(np.mean(audio_float ** 2)))
        if rms < 0.0015:
            logger.info(f"[STT SILENCE] Audio energy too low (RMS={rms:.5f}), ignoring.")
            return ""

        # Peak-normalize quiet far-field kiosk speech into Whisper's optimal dynamic range
        # while capping max gain so room noise is never blown up into distortion.
        peak = float(np.max(np.abs(audio_float)))
        gain = 1.0
        if 1e-6 < peak < 0.85:
            gain = min(0.90 / peak, 15.0)
            audio_float = np.clip(audio_float * gain, -1.0, 1.0)

        if self.backend == "groq":
            return self._transcribe_groq_sync(audio_float, rms, gain)

        t0 = time.perf_counter()
        duration_sec = max(0.1, len(audio_float) / 16000.0)
        target_lang = self._resolve_language(audio_float)
        prompt = INITIAL_PROMPT_EN if target_lang == "en" else INITIAL_PROMPT_IT

        # Transcribe with beam search for accurate Italian phrasing and anti-hallucination constraints
        segments, info = self.model.transcribe(
            audio_float,
            language=target_lang,
            beam_size=DEFAULT_BEAM_SIZE,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            repetition_penalty=DEFAULT_REPETITION_PENALTY,
            hallucination_silence_threshold=0.5,
            vad_filter=False,  # Audio has already been segmented by Silero VAD
            initial_prompt=prompt,
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

        result_text = self._filter_hallucinated_rate(" ".join(texts).strip(), duration_sec)

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[STT DONE] Transcribed {duration_sec:.2f}s audio in {elapsed:.1f} ms "
            f"(rms={rms:.4f}, gain={gain:.1f}x, beam={DEFAULT_BEAM_SIZE}) | Text: \"{result_text}\""
        )
        return result_text

    async def transcribe(self, audio_data: np.ndarray) -> str:
        """Asynchronously transcribes audio without blocking the event loop."""
        return await asyncio.to_thread(self._transcribe_sync, audio_data)
