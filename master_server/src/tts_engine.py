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
import wave
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

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
    - Authentic Canine SFX Pre-roll (Bark, Growl, Woof)
    """

    BARK_REGEX = re.compile(
        r"\b(bark|barks|woof|woofs|ruff|yip|yips|bau|abbaia|abbaiata|abbaiando)\b",
        re.IGNORECASE,
    )
    GROWL_REGEX = re.compile(
        r"\b(growl|growls|grr+|ringhia|ringhio|ringhiando|snarl|snarls)\b|grr+[-_]?(woof|bark|\w*)",
        re.IGNORECASE,
    )
    PANT_REGEX = re.compile(
        r"\b(whirr+|pant|panting|ansim[ao]|ansimando|respiro|breath|breathing|chill)\b",
        re.IGNORECASE,
    )
    STAGE_DIRECTION_KEYWORDS = re.compile(
        r"\b(luce|ross[ao]|lampeggi\w*|schermo|display|occhi|servo|meccanic\w*|motore|testa|coda|orecchi\w*|whine|gemito|piagnucol\w*|sigh|sospiro|giggle|risatina|clank|beep|sbuff\w*|fischi\w*|sbadigli\w*|ronz\w*)\b",
        re.IGNORECASE,
    )

    def __init__(self, models_dir: Path, sfx_dir: Optional[Path] = None):
        self.models_dir = models_dir
        self.sfx_dir = sfx_dir or (models_dir.parent / "assets" / "sfx")
        self.voices: Dict[str, PiperVoice] = {}
        self.sfx_cache: Dict[str, bytes] = {}
        self.sample_rate = 16000
        self._load_local_voices()
        self._load_sfx()

    def _load_sfx(self):
        """Pre-loads 16kHz PCM16 Mono sound effects from assets/sfx/ into memory."""
        if not self.sfx_dir.exists():
            logger.warning(f"SFX directory not found at {self.sfx_dir}")
            return

        for wav_path in self.sfx_dir.glob("*.wav"):
            if wav_path.name == "bark_old.wav":
                continue
            try:
                with wave.open(str(wav_path), "rb") as wf:
                    channels = wf.getnchannels()
                    width = wf.getsampwidth()
                    rate = wf.getframerate()
                    frames = wf.readframes(wf.getnframes())

                    if channels == 1 and width == 2 and rate == 16000:
                        pcm = frames
                    else:
                        raw_np = np.frombuffer(frames, dtype=np.int16)
                        if channels == 2:
                            raw_np = (raw_np[0::2] / 2 + raw_np[1::2] / 2).astype(np.int16)
                        if rate != 16000:
                            samples_f = raw_np.astype(np.float32)
                            pcm = signal.resample_poly(samples_f, 16000, rate).astype(np.int16).tobytes()
                        else:
                            pcm = raw_np.tobytes()

                    key = wav_path.stem.lower()
                    self.sfx_cache[key] = pcm
                    logger.info(f"Loaded SFX asset: {wav_path.name} ({len(pcm)} bytes, {len(pcm) / 32000:.2f}s)")
            except Exception as exc:
                logger.error(f"Failed to load SFX asset {wav_path}: {exc}")

    def _parse_segments(self, text: str) -> List[Tuple[str, str]]:
        """
        Parses text into an ordered list of segments:
        - ('text', clean_content)
        - ('sfx', 'growl' | 'bark')
        Preserves original sequence (in-line SFX) and removes stage directions.
        """
        tag_pattern = re.compile(r"(\*[^*]+\*|\[[^\]]+\]|\([^)]+\))")
        tokens = tag_pattern.split(text)
        segments: List[Tuple[str, str]] = []

        for token in tokens:
            if not token:
                continue

            if (token.startswith("*") and token.endswith("*")) or \
               (token.startswith("[") and token.endswith("]")) or \
               (token.startswith("(") and token.endswith(")")):

                content = token[1:-1].strip()
                lower = content.lower()

                # 1. Growl check takes priority over bark (handles grrr-woof, grrwoof, etc.)
                if self.GROWL_REGEX.search(lower):
                    segments.append(("sfx", "growl"))
                    continue

                # 2. Bark check
                if self.BARK_REGEX.search(lower):
                    segments.append(("sfx", "bark"))
                    continue

                # 3. Pant / Chill breathing check (replaces *whirr*)
                if self.PANT_REGEX.search(lower):
                    segments.append(("sfx", "pant"))
                    continue

                # 4. Stage directions / sound keywords to ignore
                word_count = len(lower.split())
                if word_count >= 2 or self.STAGE_DIRECTION_KEYWORDS.search(lower):
                    continue

                # 4. Single-word markdown emphasis (e.g. *davvero*, *molto*):
                if token.startswith("*"):
                    segments.append(("text", content))
                    continue
                continue
            else:
                cleaned = token.strip()
                if cleaned:
                    segments.append(("text", cleaned))

        # Merge consecutive text segments
        merged: List[Tuple[str, str]] = []
        for kind, val in segments:
            if merged and merged[-1][0] == "text" and kind == "text":
                merged[-1] = ("text", merged[-1][1] + " " + val)
            else:
                merged.append((kind, val))

        return merged

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

    async def _synthesize_voice_segment(self, clean_text: str, target_lang: str) -> bytes:
        """Synthesizes a single text segment using Diego (Italian), Bryce (English), or local fallback."""
        if not clean_text:
            return b""

        if target_lang == "it":
            speech_pcm = await self._synthesize_edge_tts(clean_text, voice_name="it-IT-DiegoNeural")
            if not speech_pcm:
                logger.info("[TTS FALLBACK] Synthesizing Italian via local Piper voice...")
                speech_pcm = await asyncio.to_thread(self._synthesize_piper_local, clean_text, "it_local")
            return speech_pcm or b""
        else:
            if "en" in self.voices:
                return await asyncio.to_thread(self._synthesize_piper_local, clean_text, "en")
            speech_pcm = await self._synthesize_edge_tts(clean_text, voice_name="en-US-ChristopherNeural")
            return speech_pcm or b""

    async def synthesize_bytes(self, text: str, lang: Optional[str] = None) -> bytes:
        """
        Synthesizes complete 16kHz PCM16 Mono bytes for the given text.
        Extracts canine cues (*bark*, *growl*, *grrr-woof*) and sequences authentic audio SFX inline.
        Chooses Diego for Italian, Bryce for English, with local fallback.
        """
        segments = self._parse_segments(text)
        if not segments:
            return b""

        text_indices = [i for i, (kind, _) in enumerate(segments) if kind == "text"]

        # If there is no vocal text to speak (e.g. only *Growl*), return SFX sequence directly
        if not text_indices:
            sfx_parts = [self.sfx_cache.get(val, b"") for kind, val in segments if kind == "sfx"]
            return b"".join(sfx_parts)

        # Detect language from combined clean text
        full_text = " ".join(segments[i][1] for i in text_indices)
        target_lang = lang or self._detect_language(full_text)

        # Concurrently synthesize all voice parts
        voice_results = await asyncio.gather(
            *(
                self._synthesize_voice_segment(self._normalize_text(segments[i][1]), target_lang)
                for i in text_indices
            )
        )
        voice_map = dict(zip(text_indices, voice_results))

        # Assemble final PCM sequence with natural 100ms silence gap between transitions
        silence_gap = b"\x00" * int(0.10 * self.sample_rate * 2)  # 100ms
        audio_sequence: List[bytes] = []

        for i, (kind, val) in enumerate(segments):
            if kind == "sfx":
                pcm = self.sfx_cache.get(val)
                if pcm:
                    logger.info(f"[TTS SFX] Inserting canine audio SFX inline: '{val}' ({len(pcm)} bytes)")
                    audio_sequence.append(pcm)
            elif kind == "text":
                pcm = voice_map.get(i, b"")
                if pcm:
                    audio_sequence.append(pcm)

        interleaved: List[bytes] = []
        for part in audio_sequence:
            if not part:
                continue
            if interleaved:
                interleaved.append(silence_gap)
            interleaved.append(part)

        return b"".join(interleaved)

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
