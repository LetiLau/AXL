"""
A-X-L Local-First Neural TTS Engine.
- Italian (default): 100% local Kokoro-82M ONNX (im_nicola) — natural human prosody on CPU.
- English: 100% local Piper ONNX (Bryce - en_US-bryce-medium, Jarvis persona).
- Optional Cloud Override (/sayDiego, /askDiego): Microsoft Edge Neural TTS (it-IT-DiegoNeural).
- Authentic Canine SFX Pre-roll (Bark, Growl, Pant).
All audio is streamed incrementally in real-time as uniform 16kHz 16-bit Mono PCM.
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
    from kokoro_onnx import Kokoro
    HAS_KOKORO = True
except ImportError:
    Kokoro = None  # type: ignore[assignment,misc]
    HAS_KOKORO = False

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
    Local-first neural TTS engine delivering natural speech:
    - Kokoro-82M ONNX (im_nicola) for Italian
    - Piper ONNX (en_US-bryce-medium) for English
    - Optional Edge-TTS (it-IT-DiegoNeural) for explicit /sayDiego & /askDiego commands
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
        self.voices: Dict[str, PiperVoice] = {}
        self.kokoro: Optional[Kokoro] = None
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
        """Pre-loads local Kokoro (Italian) and Piper (English) ONNX models into memory."""
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

        # 2. Italian: Kokoro-82M ONNX (im_nicola)
        kokoro_model = self.models_dir / "kokoro-v1.0.int8.onnx"
        if not kokoro_model.exists():
            kokoro_model = self.models_dir / "kokoro-v1.0.onnx"
        kokoro_voices = self.models_dir / "voices-v1.0.bin"

        if HAS_KOKORO and kokoro_model.exists() and kokoro_voices.exists():
            logger.info(f"Loading Italian Kokoro ONNX model: {kokoro_model.name}")
            try:
                self.kokoro = Kokoro(str(kokoro_model), str(kokoro_voices))
                logger.info("Italian Kokoro voice (im_nicola) initialized.")
            except Exception as exc:
                logger.error(f"Failed to load Kokoro ONNX model: {exc}", exc_info=True)
        else:
            logger.warning(
                f"Kokoro ONNX model/voices not found ({kokoro_model.name}, {kokoro_voices.name}) or kokoro_onnx missing."
            )

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

    def _synthesize_kokoro_local(self, text: str, voice_name: str = "im_nicola", lang: str = "it") -> bytes:
        """
        Synchronous local synthesis via Kokoro-82M ONNX (24kHz float32 -> 16kHz PCM16 Mono).
        """
        if not self.kokoro:
            logger.error("Kokoro ONNX model not loaded.")
            return b""

        try:
            samples, sr = self.kokoro.create(text, voice=voice_name, speed=1.0, lang=lang)
        except Exception as exc:
            logger.error(f"Kokoro synthesis error: {exc}", exc_info=True)
            return b""

        if samples is None or len(samples) == 0:
            return b""

        samples_f = np.asarray(samples, dtype=np.float32)
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
        Optional cloud synthesis via Microsoft Edge neural TTS (used only by /sayDiego and /askDiego).
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

    async def _synthesize_voice_segment(
        self,
        clean_text: str,
        target_lang: str,
        use_diego: bool = False,
    ) -> bytes:
        """
        Synthesizes a single text segment:
        - If use_diego=True: uses Microsoft Edge Neural TTS (it-IT-DiegoNeural) with local fallback.
        - Default (100% local): Kokoro ONNX (im_nicola) for Italian, Piper ONNX (Bryce) for English.
        """
        if not clean_text:
            return b""

        if use_diego:
            diego_voice = "it-IT-DiegoNeural" if target_lang == "it" else "en-US-ChristopherNeural"
            speech_pcm = await self._synthesize_edge_tts(clean_text, voice_name=diego_voice)
            if speech_pcm:
                return speech_pcm

        if target_lang == "en":
            return await asyncio.to_thread(self._synthesize_piper_local, clean_text, "en")

        if self.kokoro is not None:
            return await asyncio.to_thread(self._synthesize_kokoro_local, clean_text, "im_nicola", "it")

        return await asyncio.to_thread(self._synthesize_piper_local, clean_text, "en")

    async def synthesize_bytes(
        self,
        text: str,
        lang: Optional[str] = None,
        use_diego: bool = False,
    ) -> bytes:
        """
        Synthesizes complete 16kHz PCM16 Mono bytes for the given text.
        Extracts canine cues (*bark*, *growl*, *grrr-woof*) and sequences authentic audio SFX inline.
        Uses Kokoro ONNX (im_nicola) for Italian and Piper ONNX (Bryce) for English,
        or Diego Neural when use_diego=True.
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
                    use_diego=use_diego,
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
        use_diego: bool = False,
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
                    self._synthesize_voice_segment(str(payload), target_lang, use_diego=use_diego)
                )
                break

        try:
            for idx, (item_kind, payload) in enumerate(items):
                if item_kind == "pcm":
                    pcm_bytes = payload  # type: ignore[assignment]
                else:
                    task = pending_tasks.pop(idx, None)
                    if task is None:
                        pcm_bytes = await self._synthesize_voice_segment(
                            str(payload), target_lang, use_diego=use_diego
                        )
                    else:
                        pcm_bytes = await task

                    # Kick off synthesis of the next TTS sentence immediately
                    for next_idx in range(idx + 1, len(items)):
                        if items[next_idx][0] == "tts" and next_idx not in pending_tasks:
                            pending_tasks[next_idx] = asyncio.create_task(
                                self._synthesize_voice_segment(
                                    str(items[next_idx][1]), target_lang, use_diego=use_diego
                                )
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
