"""
A-X-L Hybrid Neural TTS Engine.
- Italian (Primary): Microsoft Edge Neural TTS (it-IT-DiegoNeural, +10% rate) with real-time
  sentence-by-sentence streaming.
- Italian (Offline Fallback): Local Pocket TTS (giovanni) on CPU if Diego fails or is offline.
- English: 100% local Piper ONNX (Bryce - en_US-bryce-medium, Jarvis persona).
- Authentic Canine SFX Pre-roll (Bark, Growl, Pant).
All audio is streamed incrementally in real-time as uniform 16kHz 16-bit Mono PCM.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import numpy as np
from scipy import signal
from piper.voice import PiperVoice, SynthesisConfig

try:
    from pocket_tts import TTSModel as PocketTTSModel
    HAS_POCKET_TTS = True
except ImportError:
    PocketTTSModel = None  # type: ignore[assignment,misc]
    HAS_POCKET_TTS = False

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    edge_tts = None  # type: ignore[assignment]
    HAS_EDGE_TTS = False

logger = logging.getLogger("AXL-TTS")


EN_EXCLUSIVE_TOKENS = {
    "the", "be", "to", "of", "and", "that", "have", "i", "it", "for", "not", "on", "with", "he",
    "as", "you", "do", "at", "this", "but", "his", "by", "from", "they", "we", "say", "her", "she", "or",
    "an", "will", "my", "one", "all", "would", "there", "their", "what", "up", "out", "if", "about",
    "who", "get", "which", "go", "when", "make", "can", "like", "time", "just", "him", "know",
    "take", "people", "into", "year", "your", "good", "some", "could", "them", "see", "other", "than", "then",
    "now", "look", "only", "its", "over", "think", "also", "back", "after", "use", "two", "how", "our",
    "work", "first", "well", "way", "even", "new", "want", "because", "any", "these", "give", "day", "most", "us",
    "remove", "cancel", "delete", "stop", "start", "pause", "resume", "play", "turn", "switch", "mute", "unmute",
    "set", "reset", "tell", "show", "open", "close", "hide", "check", "status", "timer", "alarm", "volume", "loud",
    "quiet", "increase", "decrease", "please", "help", "search", "find", "listen", "hear", "minutes", "seconds",
    "hours", "hour", "minute", "second", "today", "tomorrow", "yesterday", "weather", "music", "song", "joke",
    "hello", "hi", "hey", "sir", "boss", "why", "where", "off", "down", "is", "are", "am", "was", "were",
    "has", "had", "been", "being", "did", "does", "doing", "system", "mode", "screen"
}

IT_EXCLUSIVE_TOKENS = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "da", "con", "su", "per", "tra", "fra",
    "e", "ed", "o", "oppure", "ma", "però", "se", "che", "chi", "cui", "non", "più", "molto", "poco", "tutto",
    "tutti", "tutte", "questo", "questa", "questi", "queste", "quello", "quella", "quelli", "quelle", "mio", "mia",
    "miei", "mie", "tuo", "tua", "tuoi", "tue", "suo", "sua", "suoi", "sue", "nostro", "nostra", "nostri", "nostre",
    "vostro", "vostra", "vostri", "vostre", "loro", "mi", "ti", "si", "ci", "vi", "ne", "te", "noi",
    "voi", "lui", "lei", "esso", "essa", "essi", "esse", "è", "ho", "hai", "ha", "abbiamo", "avete", "hanno",
    "sono", "sei", "siamo", "siete", "eri", "eravamo", "eravate", "erano", "stato", "stata", "stati",
    "state", "sto", "stai", "sta", "stiamo", "stanno", "stavo", "stavi", "stava",
    "faccio", "fai", "fa", "facciamo", "fate", "fanno", "fatto", "posso", "puoi", "può", "possiamo",
    "potete", "possono", "potuto", "voglio", "vuoi", "vuole", "vogliamo", "volete", "vogliono", "voluto", "devo",
    "devi", "deve", "dobbiamo", "dovete", "devono", "dovuto", "so", "sai", "sa", "sappiamo", "sapete", "sanno",
    "saputo", "va", "vai", "andiamo", "andate", "vanno", "andato", "andata", "viene", "vieni", "veniamo", "venite",
    "vengono", "venuto", "venuta", "dimmi", "dimmelo", "spiegami", "mostrami", "dammi", "metti", "imposta",
    "attiva", "disattiva", "togli", "rimuovi", "mutati", "muto", "smuta", "smutati", "parla", "taci", "ascolta",
    "cosa", "dove", "quando", "perché", "perche", "quale", "quali", "quanto", "quanta", "quanti", "quante",
    "ore", "ora", "giorno", "oggi", "domani", "ieri", "tempo", "musica", "canzone", "sveglia", "minuti", "secondi",
    "ciao", "buongiorno", "buonasera", "grazie", "favore", "aiuto", "chiudi", "apri", "schermo", "visore", "modalità",
    "modalita"
}


def detect_language(text: str) -> str:
    """Determines if the utterance is in English ('en') or Italian ('it')."""
    if not text:
        return "it"
    cleaned = text.lower()
    cleaned = re.sub(r"\b(hey\s+)?(jarvis|axl)\b", "", cleaned).strip()

    en_contractions = len(re.findall(r"\b(what's|it's|don't|can't|i'm|you're|they're|we're|won't|didn't|doesn't|isn't|aren't|let's|how's|that's)\b", cleaned))
    it_apostrophes = len(re.findall(r"\b(l'|un'|d'|c'|dov'|com'|quant'|qual'è|qual'e)\b", cleaned))

    words = re.findall(r"\b\w+\b", cleaned)
    it_bonus = 0
    en_bonus = 0
    if re.search(r"\bcome\s+(stai|va|funziona|si\s+fa|stanno)\b", cleaned):
        it_bonus += 2
    elif re.search(r"\bcome\s+(here|on|back|in)\b", cleaned):
        en_bonus += 2

    en_count = sum(1 for w in words if w in EN_EXCLUSIVE_TOKENS) + (en_contractions * 2) + en_bonus
    it_count = sum(1 for w in words if w in IT_EXCLUSIVE_TOKENS) + (it_apostrophes * 2) + it_bonus

    if en_count > it_count:
        return "en"
    elif it_count > en_count:
        return "it"
    return "en" if any(w in EN_EXCLUSIVE_TOKENS for w in words) else "it"


class PiperTTSEngine:
    """
    Hybrid neural TTS engine delivering natural speech:
    - Diego (it-IT-DiegoNeural via Edge-TTS) as primary Italian voice, streamed per-sentence
    - Pocket TTS (giovanni) as local CPU offline fallback for Italian
    - Bryce (en_US-bryce-medium via Piper ONNX) for English
    - Authentic Canine SFX Pre-roll (Bark, Growl, Pant)
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
        self.static_cache_dir = models_dir.parent / "assets" / "tts_cache"
        self.runtime_cache_dir = models_dir.parent / "data" / "tts_cache"
        self.voices: Dict[str, PiperVoice] = {}
        self.pocket_model = None
        self.pocket_state = None
        self.sfx_cache: Dict[str, bytes] = {}
        self.voice_cache: Dict[str, bytes] = {}
        self.sample_rate = 16000
        self._load_local_voices()
        self._load_sfx()
        self._load_voice_cache()

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
        """Pre-loads local Piper (English) and Pocket TTS (Italian fallback) models into memory."""
        # 1. English: Bryce (Jarvis persona via Piper ONNX)
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

        # 2. Italian Offline Fallback: Pocket TTS (giovanni)
        if HAS_POCKET_TTS and PocketTTSModel is not None:
            logger.info("Loading Italian offline fallback model: Pocket TTS (giovanni)...")
            try:
                import huggingface_hub.constants as hf_constants
                prev_hf_offline = hf_constants.HF_HUB_OFFLINE
                hf_constants.HF_HUB_OFFLINE = True
                try:
                    self.pocket_model = PocketTTSModel.load_model(language="italian")
                    self.pocket_state = self.pocket_model.get_state_for_audio_prompt("giovanni")
                except Exception:
                    hf_constants.HF_HUB_OFFLINE = prev_hf_offline
                    self.pocket_model = PocketTTSModel.load_model(language="italian")
                    self.pocket_state = self.pocket_model.get_state_for_audio_prompt("giovanni")
                finally:
                    hf_constants.HF_HUB_OFFLINE = prev_hf_offline
                logger.info("Italian Pocket TTS fallback (giovanni) initialized.")
            except Exception as exc:
                logger.warning(f"Failed to initialize Pocket TTS fallback: {exc}")
                self.pocket_model = None
                self.pocket_state = None
        else:
            logger.warning("pocket-tts package not installed; Italian offline fallback unavailable.")

    @staticmethod
    def _detect_language(text: str) -> str:
        """Determines if the utterance should be synthesized in English or Italian."""
        return detect_language(text)

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

        # Shorten unnatural inter-sentence pauses without breaking sentence boundaries:
        cleaned = re.sub(r"\.{2,}", "...", cleaned)
        # Convert semicolons and colons to commas to avoid long dead pauses
        cleaned = re.sub(r"[;:]\s*", ", ", cleaned)
        # Collapse multiple whitespaces
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Splits normalized text into sentence chunks for low-latency incremental synthesis."""
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
        return parts or ([text] if text else [])

    def _synthesize_pocket_local(self, text: str) -> bytes:
        """
        Synchronous local synthesis via Pocket TTS (giovanni, 24kHz float32 -> 16kHz PCM16 Mono).
        """
        if self.pocket_model is None or self.pocket_state is None:
            logger.error("Pocket TTS fallback model not loaded.")
            return b""

        try:
            audio_tensor = self.pocket_model.generate_audio(
                self.pocket_state,
                text,
                copy_state=True,
            )
            samples_f = audio_tensor.detach().cpu().numpy().astype(np.float32)
            sr = getattr(self.pocket_model, "sample_rate", 24000)
        except Exception as exc:
            logger.error(f"Pocket TTS synthesis error: {exc}", exc_info=True)
            return b""

        if samples_f.size == 0:
            return b""

        if sr == 24000:
            samples_16k = signal.resample_poly(samples_f, 2, 3)
        elif sr != 16000:
            samples_16k = signal.resample_poly(samples_f, 16000, sr)
        else:
            samples_16k = samples_f

        pcm_int16 = np.clip(samples_16k * 32767.0, -32768.0, 32767.0).astype(np.int16)
        return pcm_int16.tobytes()

    async def _synthesize_edge_tts(self, text: str, voice_name: str = "it-IT-DiegoNeural") -> Optional[bytes]:
        """
        Cloud synthesis via Microsoft Edge neural TTS (primary Italian voice).
        Converts MP3 output to 16kHz PCM16 Mono via ffmpeg.
        """
        if not HAS_EDGE_TTS or edge_tts is None:
            return None

        try:
            communicate = edge_tts.Communicate(text, voice_name, rate="+10%")
            mp3_chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_chunks.append(chunk["data"])

            if not mp3_chunks:
                return None

            raw_mp3 = b"".join(mp3_chunks)

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
            logger.warning(f"[EDGE-TTS] Diego synthesis failed or timed out: {exc}. Falling back to local.")
            return None

    def _synthesize_piper_local(self, text: str, voice_key: str = "en") -> bytes:
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

    @staticmethod
    def _cache_key(clean_text: str, target_lang: str) -> str:
        """Stable hash key for pre-generated and runtime cached TTS segments."""
        return hashlib.sha256(f"{target_lang}:{clean_text}".encode("utf-8")).hexdigest()[:16]

    def _load_voice_cache(self):
        """Pre-loads 16kHz PCM16 Mono cached phrases from assets/tts_cache and data/tts_cache."""
        loaded = 0

        # 1. Static pre-generated cache (human-readable filenames mapped via manifest.json)
        manifest_path = self.static_cache_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for filename, meta in manifest.items():
                    if len(self.voice_cache) >= 300:
                        break
                    text = meta["text"] if isinstance(meta, dict) else str(meta)
                    lang = meta.get("lang", "it") if isinstance(meta, dict) else "it"
                    wav_path = self.static_cache_dir / filename
                    if not wav_path.exists():
                        continue
                    with wave.open(str(wav_path), "rb") as wf:
                        if wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == 16000:
                            key = self._cache_key(text, lang)
                            self.voice_cache[key] = wf.readframes(wf.getnframes())
                            loaded += 1
            except Exception as exc:
                logger.warning(f"Failed to load static TTS manifest {manifest_path}: {exc}")

        # 2. Runtime disk cache (hash-named WAVs)
        if self.runtime_cache_dir.exists():
            for wav_path in self.runtime_cache_dir.glob("*.wav"):
                if len(self.voice_cache) >= 300:
                    break
                try:
                    with wave.open(str(wav_path), "rb") as wf:
                        if wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == 16000:
                            self.voice_cache[wav_path.stem] = wf.readframes(wf.getnframes())
                            loaded += 1
                except Exception as exc:
                    logger.warning(f"Failed to load cached TTS asset {wav_path}: {exc}")

        if loaded:
            logger.info(f"Loaded {loaded} cached TTS phrase(s) into RAM.")

    def _save_runtime_cache(self, key: str, pcm: bytes):
        """Persists a synthesized 16kHz PCM16 Mono phrase to data/tts_cache for instant reuse."""
        if not pcm or len(self.voice_cache) >= 300:
            return
        self.voice_cache[key] = pcm
        try:
            self.runtime_cache_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self.runtime_cache_dir / f"{key}.wav"
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm)
        except Exception as exc:
            logger.debug(f"Could not write runtime TTS cache {key}: {exc}")

    async def _synthesize_voice_segment(
        self,
        clean_text: str,
        target_lang: str,
    ) -> bytes:
        """
        Synthesizes a single text segment:
        - First checks in-memory/disk cache (pre-generated static phrases + runtime cache).
        - Italian: Diego Neural (Edge-TTS) primary, with automatic fallback to local Pocket TTS (giovanni).
        - English: Local Piper ONNX (Bryce), with Edge-TTS fallback if missing.
        """
        if not clean_text:
            return b""

        cache_key = self._cache_key(clean_text, target_lang)
        cached = self.voice_cache.get(cache_key)
        if cached:
            return cached

        if target_lang == "it":
            speech_pcm = await self._synthesize_edge_tts(clean_text, voice_name="it-IT-DiegoNeural")
            if speech_pcm:
                if len(clean_text) <= 220:
                    await asyncio.to_thread(self._save_runtime_cache, cache_key, speech_pcm)
                return speech_pcm
            logger.info("[TTS FALLBACK] Diego unavailable; synthesizing Italian via local Pocket TTS (giovanni)...")
            speech_pcm = await asyncio.to_thread(self._synthesize_pocket_local, clean_text)
            return speech_pcm or b""
        else:
            if "en" in self.voices:
                return await asyncio.to_thread(self._synthesize_piper_local, clean_text, "en")
            speech_pcm = await self._synthesize_edge_tts(clean_text, voice_name="en-US-ChristopherNeural")
            return speech_pcm or b""

    async def synthesize_bytes(
        self,
        text: str,
        lang: Optional[str] = None,
    ) -> bytes:
        """
        Synthesizes complete 16kHz PCM16 Mono bytes for the given text.
        Extracts canine cues (*bark*, *growl*, *grrr-woof*) and sequences authentic audio SFX inline.
        Uses Diego Neural for Italian (with Pocket TTS Giovanni fallback) and Piper Bryce for English.
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
                self._synthesize_voice_segment(
                    self._normalize_text(segments[i][1]),
                    target_lang,
                )
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
        Incrementally synthesizes and yields 16kHz PCM16 Mono chunks sentence-by-sentence
        so playback starts immediately after the first sentence is ready.
        """
        segments = self._parse_segments(text)
        if not segments:
            return

        text_indices = [i for i, (kind, _) in enumerate(segments) if kind == "text"]
        full_text = " ".join(segments[i][1] for i in text_indices)
        target_lang = lang or (self._detect_language(full_text) if full_text else "it")

        # Build ordered playback items: ('sfx', pcm) or ('tts', sentence_text)
        # Insert 100ms silence gap between segment transitions (e.g. SFX <-> speech)
        silence_gap = b"\x00" * int(0.10 * self.sample_rate * 2)
        items: List[Tuple[str, object]] = []
        has_previous_segment = False

        for kind, val in segments:
            if kind == "sfx":
                pcm = self.sfx_cache.get(val)
                if pcm:
                    if has_previous_segment:
                        items.append(("pcm", silence_gap))
                    logger.info(f"[TTS SFX] Inserting canine audio SFX inline: '{val}' ({len(pcm)} bytes)")
                    items.append(("pcm", pcm))
                    has_previous_segment = True
            elif kind == "text":
                clean = self._normalize_text(val)
                sentences = self._split_sentences(clean)
                if sentences:
                    if has_previous_segment:
                        items.append(("pcm", silence_gap))
                    for sent in sentences:
                        items.append(("tts", sent))
                    has_previous_segment = True

        if not items:
            return

        # Pipeline synthesis: start synthesizing the next TTS sentence while streaming current audio
        pending_tasks: Dict[int, asyncio.Task] = {}
        for idx, (item_kind, payload) in enumerate(items):
            if item_kind == "tts":
                pending_tasks[idx] = asyncio.create_task(
                    self._synthesize_voice_segment(str(payload), target_lang)
                )
                break

        try:
            for idx, (item_kind, payload) in enumerate(items):
                if item_kind == "pcm":
                    pcm_bytes = payload  # type: ignore[assignment]
                else:
                    task = pending_tasks.pop(idx, None)
                    if task is None:
                        pcm_bytes = await self._synthesize_voice_segment(str(payload), target_lang)
                    else:
                        pcm_bytes = await task

                    # Kick off synthesis of the next TTS sentence immediately
                    for next_idx in range(idx + 1, len(items)):
                        if items[next_idx][0] == "tts" and next_idx not in pending_tasks:
                            pending_tasks[next_idx] = asyncio.create_task(
                                self._synthesize_voice_segment(str(items[next_idx][1]), target_lang)
                            )
                            break

                if not pcm_bytes:
                    continue

                for offset in range(0, len(pcm_bytes), chunk_size):
                    yield pcm_bytes[offset : offset + chunk_size]
        finally:
            for task in pending_tasks.values():
                if not task.done():
                    task.cancel()
