"""
A-X-L Hybrid Neural TTS Engine.
- Italian: Ultra-natural studio neural voice (Diego) via Edge-TTS with tuned rate (+10%)
  and snappy conversational cadence.
- English: High-tech Jarvis persona (Bryce) via local Piper ONNX.
- Offline Fallback: Seamless failover to local Piper Italian (Miro / Riccardo).
All audio is streamed in real-time as uniform 16kHz 16-bit Mono PCM.
"""

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

import numpy as np
from scipy import signal
from piper.voice import PiperVoice, SynthesisConfig

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False

logger = logging.getLogger("AXL-TTS")


class PiperTTSEngine:
    """
    Hybrid neural TTS engine delivering natural human speech:
    - Diego (Neural Italian, +10% rate, reduced inter-sentence pauses)
    - Bryce (Neural English, Jarvis style)
    - Local Piper offline fallback
    """

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self.voices: Dict[str, PiperVoice] = {}
        self.sample_rate = 16000
        self._load_local_voices()

    def _load_local_voices(self):
        """Pre-loads local Piper models into memory for English and offline fallback."""
        # 1. English: Bryce (Jarvis persona)
        bryce_path = self.models_dir / "en_US-bryce-medium.onnx"
        if bryce_path.exists():
            logger.info(f"Loading English Piper model: {bryce_path.name}")
            try:
                self.voices["en"] = PiperVoice.load(str(bryce_path))
                logger.info(f"English voice ({bryce_path.stem}) initialized.")
            except Exception as exc:
                logger.error(f"Failed to load English voice: {exc}", exc_info=True)
        else:
            logger.warning(f"English Piper model not found at {bryce_path}")

        # 2. Italian local fallback: Miro (tuned)
        miro_path = self.models_dir / "it_IT-miro-high.onnx"
        if miro_path.exists():
            logger.info(f"Loading Italian local fallback model: {miro_path.name}")
            try:
                self.voices["it_local"] = PiperVoice.load(str(miro_path))
                logger.info(f"Local Italian fallback ({miro_path.stem}) initialized.")
            except Exception as exc:
                logger.error(f"Failed to load local Italian fallback: {exc}", exc_info=True)
        else:
            logger.warning(f"Italian local fallback model not found at {miro_path}")

    @staticmethod
    def _detect_language(text: str) -> str:
        """Determines if the utterance should be synthesized in English or Italian."""
        text_lower = text.lower()
        it_tokens = {
            "il", "lo", "la", "i", "gli", "le", "è", "e", "un", "una",
            "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
            "ho", "sono", "sistemi", "fatto", "non", "che", "cosa", "chi", "come", "ora"
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
        Cleans markdown formatting and adjusts punctuation to shorten
        inter-phrase dead silences for smooth, snappy speech delivery.
        """
        # Remove markdown decorations
        cleaned = re.sub(r"[*_`#~]", "", text)
        cleaned = re.sub(r"http\S+", "", cleaned)
        cleaned = cleaned.replace("%", " percento")

        # Shorten unnatural inter-sentence pauses:
        # Replace multiple dots with ellipsis
        cleaned = re.sub(r"\.{2,}", "...", cleaned)
        # Convert sentence-ending periods followed by space into commas for snappy conversational cadence
        cleaned = re.sub(r"(?<=\w)\.\s+(?=[A-Za-zÀ-ÿ])", ", ", cleaned)
        # Convert semicolons and colons to commas to avoid long dead pauses
        cleaned = re.sub(r"[;:]\s*", ", ", cleaned)
        # Collapse multiple whitespaces
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    async def _synthesize_edge_tts(self, text: str, voice_name: str = "it-IT-DiegoNeural") -> Optional[bytes]:
        """
        Synthesizes text using Microsoft Edge neural TTS in-memory.
        Uses rate=+10% for crisp, natural pacing with reduced pauses.
        Converts in-memory MP3 output to 16kHz PCM16 Mono via ffmpeg.
        """
        if not HAS_EDGE_TTS:
            return None

        try:
            # rate=+10% tightens inter-word pauses and gives an energetic, confident tone
            communicate = edge_tts.Communicate(text, voice_name, rate="+10%")
            mp3_chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_chunks.append(chunk["data"])

            if not mp3_chunks:
                return None

            raw_mp3 = b"".join(mp3_chunks)

            # In-memory streaming conversion: MP3 -> 16000Hz 16-bit Mono PCM
            def _convert():
                proc = subprocess.Popen(
                    ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                pcm, _ = proc.communicate(input=raw_mp3)
                return pcm

            return await asyncio.to_thread(_convert)

        except Exception as exc:
            logger.warning(f"[EDGE-TTS] Online synthesis failed or timed out: {exc}. Dropping to local fallback.")
            return None

    def _synthesize_piper_local(self, text: str, voice_key: str = "it_local") -> bytes:
        """
        Synchronous local synthesis via Piper ONNX.
        Resamples output to 16000Hz if the model native rate is 22050Hz.
        """
        voice = self.voices.get(voice_key) or next(iter(self.voices.values()), None)
        if not voice:
            logger.error("No local Piper voice loaded.")
            return b""

        # Tuned parameters for local synthesis to prevent eating consonants
        syn_cfg = SynthesisConfig(
            noise_scale=0.333,
            noise_w_scale=0.6,
            length_scale=1.10,
        )

        audio_parts = []
        for chunk in voice.synthesize(text, syn_config=syn_cfg):
            if chunk.audio_int16_bytes:
                audio_parts.append(chunk.audio_int16_bytes)

        if not audio_parts:
            return b""

        raw_pcm = b"".join(audio_parts)
        native_sr = voice.config.sample_rate

        # Resample 22050Hz to 16000Hz if needed
        if native_sr == 22050:
            samples_22k = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32)
            samples_16k = signal.resample_poly(samples_22k, 320, 441).astype(np.int16)
            return samples_16k.tobytes()

        return raw_pcm

    async def synthesize_bytes(self, text: str, lang: Optional[str] = None) -> bytes:
        """
        Synthesizes complete 16kHz PCM16 Mono bytes for the given text.
        Chooses Diego for Italian, Bryce for English, with local fallback.
        """
        clean_text = self._normalize_text(text)
        if not clean_text:
            return b""

        target_lang = lang or self._detect_language(clean_text)

        if target_lang == "it":
            # Primary: Diego Neural (natural, studio human quality)
            pcm_bytes = await self._synthesize_edge_tts(clean_text, voice_name="it-IT-DiegoNeural")
            if pcm_bytes:
                return pcm_bytes

            # Offline Fallback: Local Miro/Riccardo Piper
            logger.info("[TTS FALLBACK] Synthesizing Italian via local Piper voice...")
            return await asyncio.to_thread(self._synthesize_piper_local, clean_text, "it_local")

        else:
            # English: Bryce Piper model (Jarvis persona)
            if "en" in self.voices:
                return await asyncio.to_thread(self._synthesize_piper_local, clean_text, "en")
            # Secondary English fallback: Christopher or Ryan Neural
            pcm_bytes = await self._synthesize_edge_tts(clean_text, voice_name="en-US-ChristopherNeural")
            return pcm_bytes or b""

    async def synthesize_stream(
        self,
        text: str,
        chunk_size: int = 1280,  # 1280 bytes = 640 samples = 40ms @ 16kHz
        lang: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Yields streaming 16kHz PCM16 Mono chunks suitable for low-latency transmission over WebSocket.
        """
        pcm_bytes = await self.synthesize_bytes(text, lang)
        if not pcm_bytes:
            return

        for offset in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[offset : offset + chunk_size]
            yield chunk
            await asyncio.sleep(0.001)
