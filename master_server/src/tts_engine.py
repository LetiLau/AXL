"""
A-X-L Local TTS Engine powered by Piper ONNX.
Generates real-time 16kHz 16-bit Mono PCM audio streams from text.
Supports automatic bilingual switching (Italian / English) and clean text normalization.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

import numpy as np
from piper.voice import PiperVoice

logger = logging.getLogger("AXL-TTS")


class PiperTTSEngine:
    """
    On-device Neural Text-to-Speech synthesis using Piper.
    Loads ONNX voice models and generates PCM16 mono frames at 16000Hz.
    """

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self.voices: Dict[str, PiperVoice] = {}
        self.sample_rate = 16000
        self._load_voices()

    def _load_voices(self):
        """Loads available Piper ONNX models into memory."""
        it_model_path = self.models_dir / "it_IT-riccardo-x_low.onnx"
        en_model_path = self.models_dir / "en_GB-alan-low.onnx"

        if it_model_path.exists():
            logger.info(f"Loading Italian TTS model: {it_model_path.name}")
            try:
                self.voices["it"] = PiperVoice.load(str(it_model_path))
                logger.info("Italian voice (Riccardo) initialized.")
            except Exception as exc:
                logger.error(f"Failed to load Italian voice: {exc}", exc_info=True)
        else:
            logger.warning(f"Italian TTS model not found at {it_model_path}")

        if en_model_path.exists():
            logger.info(f"Loading English TTS model: {en_model_path.name}")
            try:
                self.voices["en"] = PiperVoice.load(str(en_model_path))
                logger.info("English voice (Alan) initialized.")
            except Exception as exc:
                logger.error(f"Failed to load English voice: {exc}", exc_info=True)
        else:
            logger.warning(f"English TTS model not found at {en_model_path}")

    @staticmethod
    def _detect_language(text: str) -> str:
        """Lightweight heuristic to select Italian vs English voice model."""
        text_lower = text.lower()
        it_tokens = {
            "il", "lo", "la", "i", "gli", "le", "è", "e", "un", "una",
            "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
            "ho", "sono", "sistemi", "fatto", "non", "che", "cosa", "chi", "come"
        }
        en_tokens = {
            "the", "is", "are", "a", "an", "of", "to", "in", "for", "with",
            "on", "at", "from", "by", "have", "all", "systems", "done", "not",
            "what", "who", "how", "ready", "sir", "boss"
        }
        words = set(re.findall(r"\b\w+\b", text_lower))
        it_matches = len(words.intersection(it_tokens))
        en_matches = len(words.intersection(en_tokens))
        return "en" if en_matches > it_matches else "it"

    @staticmethod
    def _normalize_text(text: str) -> str:
        """
        Strips markdown formatting, code ticks, and special symbols
        so the neural acoustic model generates smooth, natural speech.
        """
        # Remove bold, italics, backticks, URLs
        cleaned = re.sub(r"[*_`#~]", "", text)
        cleaned = re.sub(r"http\S+", "", cleaned)
        # Expand percent symbol to words based on likely content
        cleaned = cleaned.replace("%", " percento")
        # Collapse multiple spaces and newlines
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def synthesize_bytes(self, text: str, lang: Optional[str] = None) -> bytes:
        """
        Synchronous synthesis yielding complete 16kHz PCM16 Mono bytes.
        Executed inside thread pool to prevent blocking the async loop.
        """
        clean_text = self._normalize_text(text)
        if not clean_text:
            return b""

        target_lang = lang or self._detect_language(clean_text)
        voice = self.voices.get(target_lang) or self.voices.get("it") or next(iter(self.voices.values()), None)

        if not voice:
            logger.error("No TTS voice model available for synthesis.")
            return b""

        audio_parts = []
        for chunk in voice.synthesize(clean_text):
            if chunk.audio_int16_bytes:
                audio_parts.append(chunk.audio_int16_bytes)

        return b"".join(audio_parts)

    async def synthesize_stream(
        self,
        text: str,
        chunk_size: int = 1280,  # 1280 bytes = 640 samples = 40ms @ 16kHz
        lang: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Synthesizes text in a worker thread and yields streaming PCM16 chunks
        suitable for low-latency transmission over WebSocket.
        """
        pcm_bytes = await asyncio.to_thread(self.synthesize_bytes, text, lang)
        if not pcm_bytes:
            return

        for offset in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[offset : offset + chunk_size]
            yield chunk
            # Yield control to event loop to preserve smooth network streaming
            await asyncio.sleep(0.001)
