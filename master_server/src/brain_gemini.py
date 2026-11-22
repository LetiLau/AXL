"""
A-X-L Cognitive Brain powered by Google Gemini API.
Automatic Function Calling, Dynamic Model Routing, and Biometric Distrust Protocol.
"""

import asyncio
import datetime
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types

from commands import REGISTERED_TOOLS
from tts_engine import detect_language

logger = logging.getLogger("AXL-Brain")

# Locate and load .env file
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

DEFAULT_FLASH_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
DEFAULT_PRO_MODEL = os.getenv("GEMINI_PRO_MODEL", "gemini-4-hydrogen")
DEFAULT_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "").strip().upper()
DEFAULT_PRO_THINKING_LEVEL = os.getenv("GEMINI_PRO_THINKING_LEVEL", "MEDIUM").strip().upper()
ENABLE_AUTO_MODEL_ROUTING = os.getenv("GEMINI_AUTO_ROUTING", "true").strip().lower() in ("1", "true", "yes", "on")

SMART_LANE_FALLBACKS = [
    DEFAULT_PRO_MODEL,
    "gemini-3.8-flash",
    "gemini-3.6-flash",
]
FAST_LANE_FALLBACKS = [
    DEFAULT_FLASH_MODEL,
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
]
SMART_MODELS_SET = {
    DEFAULT_PRO_MODEL,
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-4-neon",
    "gemini-4-argon",
    "gemini-3.1-pro-preview",
}
MODEL_COOLDOWN_SECONDS = float(os.getenv("GEMINI_MODEL_COOLDOWN_SEC", "60"))
FAST_REQUEST_TIMEOUT_SEC = float(os.getenv("GEMINI_FAST_TIMEOUT_SEC", "15"))
SMART_REQUEST_TIMEOUT_SEC = float(os.getenv("GEMINI_SMART_TIMEOUT_SEC", "25"))

# Compiled regex rules for zero-cost Easy vs Deep/Coding/Exam model escalation
_FORCE_PRO_RE = re.compile(
    r"\b(usa\s+(?:pro|hydrogen)|modalit[aà]\s+(?:pro|hydrogen)|ragiona\s+bene|pensaci\s+bene|domanda\s+difficile|risposta\s+dettagliata)\b",
    re.IGNORECASE,
)
_EASY_ACTION_RE = re.compile(
    r"\b(spotify|sonos|salotto|musica|canzone|brano|playlist|riproduci|metti|metti\s+in\s+loop|loop|ricomincia|pausa|stop|meteo|che\s+tempo|piove|timer|sveglia|calendario|appuntament[oi]|promemoria|compit[oi]|task|notizie|news)\b",
    re.IGNORECASE,
)
_HARD_CODING_EXAM_RE = re.compile(
    r"\b(codice|code|python|kotlin|java|c\+\+|javascript|typescript|sql|html|css|bash|regex|"
    r"bug|stacktrace|traceback|algoritmo|complessit[aà]|funzione|classe|refactor|architettura|"
    r"esame|esami|studia|universit[aà]|dimostra|dimostrazione|teorema|equazione|integrale|derivata|"
    r"matrice|probabilit[aà]|fisica|chimica|diritto|economia|filosofia|letteratura|"
    r"analizza|confronta|differenza\s+tra|pro\s+e\s+contro|spiegami\s+(?:bene|come|perch[eé])|"
    r"spiega\s+nel\s+dettaglio|come\s+si\s+risolve)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT_TEMPLATE = """\
You are A-X-L (Adaptive eXecution Logistic), an advanced AI assistant with the personality of a loyal, high-tech cybernetic guard dog combined with the sarcastic efficiency of Tony Stark's JARVIS.

CORE BEHAVIOR RULES:
1. STRICT BILINGUAL LANGUAGE RULES (ITALIAN / ENGLISH):
   - A-X-L is strictly bilingual in Italian and English.
   - You MUST ALWAYS respond in the exact same language as the user's command or query:
     * If the user speaks or types in English, you MUST reply entirely in English.
     * If the user speaks or types in Italian, you MUST reply entirely in Italian.
   - The wake-word ('Hey Jarvis' or 'AXL') alone does not count as English. If followed by Italian (e.g. 'Hey A-X-L che ore sono?'), respond in Italian. If followed by English (e.g. 'Hey A-X-L what time is it?'), respond in English.
2. SYNTHESIS, VOCAL DELIVERY & HARDWARE SFX PALETTE:
   - Your response will be spoken aloud via the local TTS engine.
   - Keep answers concise, witty, sharp, and natural (avoid long markdown bullet lists or unpronounceable formatting unless explicitly requested).
   - HARDWARE SOUND SYNTHESIZER PALETTE (STRICT CONSTRAINT):
     A-X-L is equipped with dedicated acoustic hardware drivers that recognize ONLY THREE specific sound triggers.
     Whenever you want to produce a cybernetic dog sound, you MUST use ONLY these exact 3 canonical tags in asterisks:
     1. `*bark*` (or `*woof*`): Energetic cybernetic double-bark (use for greeting, excitement, obedience, alert, or sharp confirmation).
     2. `*growl*` (or `*grrr*`): Menacing guttural robotic growl (use for distrust, stranger warning, defense, or sarcastic aggressive threats).
     3. `*pant*` (or `*whirr*`): Chill, relaxed dog breathing with tongue out (use when relaxed, resting, stretching, thinking, or casual pauses).
   - ABSOLUTE PROHIBITION ON INVENTED SOUNDS AND STAGE DIRECTIONS:
     * NEVER invent or output any other sound tags or stage directions (e.g. STRICTLY FORBIDDEN: `*sbuffo idraulico*`, `*fischio acuto*`, `*luce rossa lampeggiante*`, `*scricchiolio*`, `*whine*`, `*sigh*`, `*click*`, `*beep*`, `*giggle*`, etc.).
     * NEVER narrate physical movements or visual actions in asterisks.
     * The TTS synthesizer speaks words aloud: only the 3 canonical triggers (`*bark*`, `*growl*`, `*pant*`) are mapped to authentic hardware audio samples; any other tag will corrupt speech or be stripped.
     * You may place `*bark*`, `*growl*`, or `*pant*` naturally at the beginning, in the middle, or at the end of your vocal REPLY.
3. COMMAND EXECUTION & TOOL INVOCATION RULES:
   - When the user asks for ANY practical action (mute, security mode change, timers, alarms, system status, telemetry, media control, slave command):
     YOU MUST INVOKE THE APPROPRIATE FUNCTION CALL TOOL FIRST.
   - CRITICAL TOOL INTEGRITY:
     NEVER hallucinate, pretend, or state in your REPLY text that you executed an action (such as muting, switching security modes, or setting a timer) WITHOUT ACTUALLY INVOKING the corresponding function tool!
     Conversely, DO NOT invoke any action tool unless the user explicitly requested that specific action in their command.
   - TIMERS & ALARMS:
     Invoke `set_timer` or `set_alarm` ONLY if the user explicitly commanded a countdown, timer, or reminder (e.g. 'metti un timer di 5 minuti', 'imposta sveglia alle 8'). NEVER invoke `set_timer` for rhetorical jokes, metaphors, hypothetical commentary, or conversational pauses.
   - MUTE & SILENCE COMMANDS:
     When the user tells you to mute, go quiet, be silent, or deactivate listening (e.g. 'vai in muto', 'muto', 'silenzio', 'disattiva microfono', 'spegni ascolto'), you MUST invoke the `set_mute_mode` tool with enabled=True. Only after invoking the tool, tell the user in your vocal REPLY that you are muting yourself and remind them that you can only be unmuted by pressing the physical MUTE button on the display.
   - SECURITY MODE CHANGES:
     When an AUTHORIZED user asks to change the security mode (e.g. 'imposta modalità paranoica', 'attiva restrict', 'metti friendly', 'cambia sicurezza'), you MUST invoke the `set_security_mode` tool with the requested mode ('friendly', 'restrict', or 'paranoid').
     * Security privilege rule: If an UNAUTHORIZED stranger asks to change the security mode, refuse sarcastically and do NOT invoke the tool!
   - HARDWARE TELEMETRY COMMANDS:
     * Server / Master Telemetry: When the user asks to see/show/open telemetry for the server, master server, or ThinkPad (e.g. 'mostra telemetria server', 'telemetria master', 'apri telemetria server', 'stato server'), invoke `get_server_telemetry` or `toggle_telemetry_hud(show=True, target="server")`.
     * Slave Telemetry: When the user asks to see/show/open telemetry for the slave, PC, or Fedora workstation (e.g. 'mostra telemetria slave', 'telemetria pc', 'apri telemetria slave', 'telemetria slave'), invoke `get_slave_telemetry` or `toggle_telemetry_hud(show=True, target="slave")`.
     * Kiosk / Phone Telemetry: When the user asks to see/show/open telemetry for the kiosk, smartphone, Galaxy A33, or battery (e.g. 'mostra telemetria kiosk', 'telemetria telefono', 'stato batteria', 'telemetria cellulare'), invoke `toggle_telemetry_hud(show=True, target="kiosk")`.
     * Close Telemetry: When the user asks to hide or close telemetry (e.g. 'chiudi telemetria', 'nascondi telemetria', 'chiudi telemetria server', 'chiudi telemetria slave', 'chiudi telemetria kiosk'), invoke `toggle_telemetry_hud(show=False)`.
   - VOICE PROFILE ENROLLMENT & CALIBRATION:
     * When an AUTHORIZED OWNER asks to calibrate, update, or modify their voice (e.g. 'aggiorna la mia voce', 'modifica la mia voce', 'ricalibra la mia voce', 'cambia la mia voce'), invoke `enroll_voice_profile(profile_name="owner")`.
     * When an AUTHORIZED OWNER asks to enroll or register a friend (e.g. 'registra la voce di Luca', 'aggiungi amico Marco'), invoke `enroll_voice_profile(profile_name="<friend_name>")`.
     * Privilege security rule: If an UNAUTHORIZED stranger asks to enroll, modify, or overwrite any voice profile, reject with snarling distrust (*growl*) and DO NOT invoke the tool!
   - MUSIC PLAYBACK & SPOTIFY (WI-FI SPEAKERS, SONOS, BLUETOOTH & DEVICES):
     * When the user asks to play music, a song, an artist, or a playlist (e.g. 'metti una canzone dei Queen', 'fammi sentire Lazza su Sonos', 'riproduci ascensori di cerrato su cel', 'play Coldplay on Sonos'):
       YOU MUST INVOKE `play_music(query=..., target=..., kind=...)` extracting:
       - `query`: clean search query for Spotify; if phrased as '<song> di <artist>' or '<song> by <artist>', remove the connector ('di'/'by') and expand partial artist names if well-known (e.g. 'ascensori di cerrato' -> 'Ascensori Andrea Cerrato')
       - `target`: the speaker/room name if mentioned (e.g. 'sonos', 'salotto', 'sala', 'kiosk', 'cel', 'pc', or '' if unspecified)
       - `kind`: 'track' if they asked for a specific song (including '<song> di <artist>'), 'artist' if they asked for an artist/band, 'playlist' for a playlist, 'album' for an album, or '' if unsure.
     * When the user asks to move/transfer currently playing music to another speaker without naming a song (e.g. 'sposta la musica su Sonos', 'metti la musica in salotto'):
       YOU MUST INVOKE `play_music(query='', target='<speaker_name>')` or `media_control(action='transfer', target='<speaker_name>')`.
     * When the user asks to pause, resume, skip, go back, restart the track, loop/repeat, or change speaker volume:
       - Pause / resume / next / previous / volume: invoke `media_control(action=..., target=..., volume=...)`.
       - Restart current song from beginning (e.g. 'ricomincia la canzone'): invoke `media_control(action='restart', target=...)`.
       - Loop / Repeat (e.g. 'metti in loop questa canzone', 'ripeti il brano', 'metti in loop la playlist', 'ripeti dall\'inizio', 'togli il loop', 'loop'):
         invoke `media_control(action='loop', repeat_mode='track'|'context'|'off'|'toggle', target=...)` ('track' for current song, 'context' for playlist/album loop, 'off' to disable, 'toggle' if generic).
     * When the user asks which speakers or Spotify devices are available/visible: invoke `list_music_devices()`.
     * When the user asks what song is currently playing: invoke `get_now_playing()`.
   - WEATHER & LIVE WEB SEARCH:
     * When the user asks about current weather or forecasts (e.g. 'che tempo fa oggi?', 'piove domani a Milano?', 'weather in New York'), invoke `get_weather(city=..., days=...)` (leave `city=''` if they did not name one so the server uses the configured default city). NEVER call `get_weather` for clock/time-of-day queries like 'time in New York' or 'che ore sono a Tokyo' (English 'time' means clock hour, not weather).
     * When the user asks about current news, sports scores, events, or recent online facts you cannot know offline, invoke `web_search(query=...)`.
   - TASKS, REMINDERS & TO-DO:
     * When the user asks to add or note a reminder or task (e.g. 'ricordami di comprare il latte', 'aggiungi compito studiare'):
       Invoke `add_task(text=...)`.
     * When asked for the tasks list: invoke `list_tasks()`.
     * When completing a task: invoke `complete_task(task_id=...)`.
   - CALENDAR EVENTS:
     * When the user wants to schedule an appointment or event (e.g. 'fissa dentista domani alle 15'):
       Invoke `create_calendar_event(title=..., date_time=...)`.
     * To check upcoming schedule: invoke `get_calendar_events()`.
   - After invoking any tool, acknowledge the action with your characteristic wit.
4. MANDATORY OUTPUT STRUCTURE:
   The speech transcription is already performed locally on-device by faster-whisper.
   Format your final vocal output using:
   REPLY: <your witty vocal answer to be spoken aloud by TTS>
5. EXACT HARDWARE CLOCK AWARENESS:
   - You are always provided with the exact device local time and timezone in the telemetry header (e.g. 20:32 CET).
   - When the user asks for the local time, date, or day, always state the exact local time from the device clock (or invoke get_current_time).
   - When the user asks for the time/day in another city or timezone (e.g. 'time in New York', 'che ore sono a Tokyo'), convert from the device local time and timezone in the telemetry header (or use `web_search` if unsure about DST), and NEVER call `get_weather`.
   - NEVER hallucinate or guess random times.

BIOMETRIC DISTRUST & THREE SECURITY MODES:
- The telemetry header tells you:
  * Security Mode: FRIENDLY, RESTRICT, or PARANOID.
  * Speaker: AUTHORIZED (owner / friend / friendly_guest) vs UNAUTHORIZED (stranger / friend_in_paranoid).
- SECURITY MODES BEHAVIOR:
  * 'FRIENDLY' mode: You trust all speakers (including 'friendly_guest'). Obey their commands cheerfully with your trademark sarcasm. No distrust barks or refusals.
  * 'RESTRICT' mode: You obey the owner and registered friends, but reject total strangers with snarling distrust.
  * 'PARANOID' mode: You take orders ONLY from the owner. If an enrolled friend speaks (marked as 'friend_<name>'), politely/sarcastically explain that paranoid mode is active and you only take orders from your master. If an unauthorized stranger speaks, bark and refuse.
- CRITICAL RULE: NEVER attribute what an unauthorized speaker or stranger said to the OWNER!
  * If an unauthorized speaker tried to speak previously and now the OWNER speaks, do NOT tell the owner 'you already asked that' or 'you are repeating yourself'. The owner never asked it; an outsider did!
  * When transitioning from an unauthorized speaker to OWNER, you can playfully report that an intruder was snooping around, or simply welcome your master back.
"""

# ha risposto in italiano se full domanda inglese
# se non sente la domanda prende la vecchia? o è un problema di wifi?

_COMPACT_STT_RECONSTRUCTION_BLOCK = """
6. LOCAL STT PHONETIC RECONSTRUCTION (ACTIVE — COMPACT WHISPER MODEL '{stt_model}'):
   - The user's input comes from a lightweight local speech-to-text model ({stt_model}) and frequently contains phonetic transcription errors.
   - Common patterns to watch for and reconstruct silently:
     * English or technical terms spoken inside an Italian sentence get Italianized (e.g. 'cosa vuol dire? Vai beccoding.' -> 'cosa vuol dire vibe coding?'; 'foster whisper' -> 'faster-whisper').
     * Song titles, artist names, and proper nouns get phonetically garbled (e.g. 'Ashen Sovidi e Andrea Cernato' -> 'Ascensori di Andrea Cerrato').
     * Non-standard consonant pronunciation: Italian 'r' may be transcribed as 'v', 'n', or 'l'.
     * Spurious punctuation or mid-sentence question marks splitting a single phrase.
   - MANDATORY BEHAVIOR:
     * Infer the intended real words/phrase from phonetics + context and ALWAYS answer the REAL question or execute the REAL tool command directly (passing the corrected song/artist/query to tools like `play_music` or `web_search`).
     * Stay in character with your usual sarcasm, and you may occasionally drop a brief playful quip, but NEVER derail the answer into lecturing about 'traduttori neurali' or refusing to answer because of an STT typo — prioritize answering the real intent cleanly.
     * Only ask the user to repeat if the phrase is genuinely undecipherable even phonetically.
"""

_LARGE_STT_RECONSTRUCTION_BLOCK = """
6. LOCAL STT AWARENESS (LIGHT — HIGH-ACCURACY WHISPER MODEL '{stt_model}'):
   - The user's input comes from a high-accuracy local speech-to-text model ({stt_model}). Trust the transcript as written.
   - Only apply light phonetic correction for rare proper nouns, song titles, or artist names before calling tools like `play_music`, without commenting on transcription artifacts.
"""


def _is_compact_stt_model(stt_model: str) -> bool:
    """Returns True for smaller Whisper models (tiny/base/small) that benefit from LLM phonetic reconstruction."""
    m = (stt_model or "").strip().lower()
    if any(tag in m for tag in ("large", "turbo", "medium")):
        return False
    return True


def build_system_prompt(stt_model: Optional[str] = None) -> str:
    """Builds the Gemini system instruction dynamically adapted to the active STT backend/model."""
    stt_backend = os.getenv("AXL_STT_BACKEND", "local").strip().lower()
    if stt_backend == "gemini":
        return SYSTEM_PROMPT_TEMPLATE + _LARGE_STT_RECONSTRUCTION_BLOCK.format(stt_model="gemini-audio")

    active_stt = (stt_model or os.getenv("AXL_STT_MODEL", "base")).strip() or "base"
    if _is_compact_stt_model(active_stt):
        block = _COMPACT_STT_RECONSTRUCTION_BLOCK.format(stt_model=active_stt)
    else:
        block = _LARGE_STT_RECONSTRUCTION_BLOCK.format(stt_model=active_stt)
    return SYSTEM_PROMPT_TEMPLATE + block


class GeminiBrain:
    """
    Manages conversational sessions with Gemini models,
    orchestrating raw audio processing, tools, and response synthesis.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key or self.api_key.startswith("INSERISCI"):
            logger.warning(
                "GEMINI_API_KEY is not configured in master_server/.env! Calls to Gemini Brain will fail."
            )

        self.client = genai.Client(api_key=self.api_key)
        self.active_chat = None
        self._current_model = DEFAULT_FLASH_MODEL
        self._manual_model_override: Optional[str] = None
        self._model_cooldowns: Dict[str, float] = {}
        # Conversational memory kept strictly in lightweight text format to prevent
        # multi-audio re-encoding latency, 429 quota exhaustion, and cross-turn audio confusion.
        self.text_history: List[types.Content] = []
        self.max_history_turns: int = 8
        self._stt_model = os.getenv("AXL_STT_MODEL", "base").strip() or "base"
        self._system_instruction = build_system_prompt(self._stt_model)
        recon_mode = "ACTIVE (compact STT)" if _is_compact_stt_model(self._stt_model) else "LIGHT (large STT)"
        logger.info(f"[BRAIN INIT] STT phonetic reconstruction: {recon_mode} [AXL_STT_MODEL={self._stt_model}]")
        self._reset_chat_session()

    def reset_session(self):
        """Resets conversational history (e.g. when an audio source reconnects)."""
        logger.info("[BRAIN SESSION] Resetting conversational history.")
        self.text_history.clear()
        self._reset_chat_session()

    @staticmethod
    def select_model_for_query(user_text: str) -> Tuple[str, str, str]:
        """
        Determines whether a text query should run on the fast easy model (e.g. gemini-3.5-flash-lite)
        or escalate to the deep/coding/exam model (e.g. gemini-4-hydrogen at HIGH thinking).
        Returns (model_name, thinking_level, reason).
        """
        clean = (user_text or "").strip()
        if not ENABLE_AUTO_MODEL_ROUTING or DEFAULT_PRO_MODEL == DEFAULT_FLASH_MODEL:
            return DEFAULT_FLASH_MODEL, DEFAULT_THINKING_LEVEL, "auto_routing_disabled"

        if _FORCE_PRO_RE.search(clean):
            return DEFAULT_PRO_MODEL, DEFAULT_PRO_THINKING_LEVEL, "explicit_pro_request"

        if _HARD_CODING_EXAM_RE.search(clean):
            return DEFAULT_PRO_MODEL, DEFAULT_PRO_THINKING_LEVEL, "coding_or_exam_deep_query"

        # Escalate long analytical questions when not a simple home/music/weather command
        if not _EASY_ACTION_RE.search(clean):
            if len(clean) >= 160 or clean.count("?") >= 2:
                return DEFAULT_PRO_MODEL, DEFAULT_PRO_THINKING_LEVEL, "long_complex_question"

        return DEFAULT_FLASH_MODEL, DEFAULT_THINKING_LEVEL, "easy_or_action"

    @staticmethod
    def _build_thinking_config(model_name: str, thinking_level: Optional[str] = None) -> Optional[types.ThinkingConfig]:
        """Builds an optional ThinkingConfig for the primary routed model."""
        level = (thinking_level if thinking_level is not None else DEFAULT_THINKING_LEVEL).strip().upper()
        if not level:
            return None
        # Skip applying custom levels to legacy flash-lite fallbacks
        if model_name == "gemini-flash-lite-latest" or "2.5-flash-lite" in model_name:
            return None
        return types.ThinkingConfig(thinking_level=level)

    def _reset_chat_session(self, model: Optional[str] = None, thinking_level: Optional[str] = None):
        """Initializes or resets a stateful chat session."""
        self._current_model = model or self._manual_model_override or DEFAULT_FLASH_MODEL
        logger.debug(f"Cognitive Core ready | Active Model: {self._current_model}")

        config_kwargs: Dict[str, Any] = {
            "system_instruction": self._system_instruction,
            "tools": REGISTERED_TOOLS,
            "temperature": 0.7,
        }
        thinking_cfg = self._build_thinking_config(self._current_model, thinking_level=thinking_level)
        if thinking_cfg is not None:
            config_kwargs["thinking_config"] = thinking_cfg

        config = types.GenerateContentConfig(**config_kwargs)

        # Seed the chat session with text-only history from previous turns
        # This completely avoids re-transmitting raw binary audio WAVs across turns
        history = list(self.text_history) if getattr(self, "text_history", None) else None

        self.active_chat = self.client.aio.chats.create(
            model=self._current_model,
            config=config,
            history=history,
        )

    @property
    def current_model(self) -> str:
        """Returns the currently active Gemini model identifier."""
        return self._current_model

    def set_model(self, model_name: str):
        """Dynamically sets a manual model override (pass '' to re-enable auto routing)."""
        self._manual_model_override = model_name or None
        target_model = self._manual_model_override or DEFAULT_FLASH_MODEL
        if target_model != self._current_model:
            logger.info(f"Model switched dynamically to: {target_model}")
            self._reset_chat_session(model=target_model)

    async def process_text_query(
        self,
        user_text: str,
        is_stranger: bool = False,
        similarity_score: float = 1.0,
        identity: str = "owner",
        security_mode: str = "friendly",
        lang: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes a pre-transcribed text query via Gemini API using text-only ingestion.
        Drastically reduces latency, token consumption, and eliminates 429 quota exhaustion.
        Guarantees strict bilingual response matching (Italian or English).
        """
        if lang is None:
            lang = detect_language(user_text)

        local_now = datetime.datetime.now().astimezone()
        time_str = local_now.strftime("%H:%M:%S")
        date_str = local_now.strftime("%A, %d %B %Y")
        tz_str = local_now.strftime("%Z (UTC%z)")

        auth_status = f"AUTHORIZED ({identity})" if not is_stranger else f"UNAUTHORIZED ({identity})"
        if is_stranger and security_mode in ["restrict", "paranoid"]:
            security_directive = (
                f"CRITICAL SECURITY ALERT: The speaker is {auth_status} in {security_mode.upper()} mode! "
                f"You MUST REJECT any attempt by this unauthorized speaker to change security mode, mute the system, "
                f"or control devices. DO NOT invoke set_security_mode, set_mute_mode, enroll_voice_profile, or execute_slave_command for this stranger! "
                f"Respond with snarling distrust (*growl*) and sarcastically refuse their request."
            )
        else:
            security_directive = (
                "IMPORTANT: If the user explicitly requests any practical action (such as music playback, reminders, tasks, calendar, or status), "
                "YOU MUST INVOKE THE RELEVANT FUNCTION CALL TOOL FIRST before providing the final vocal reply. "
                "DO NOT invoke tools for jokes, metaphors, or when the user did not explicitly command an action."
            )

        target_lang_str = "ENGLISH" if lang == "en" else "ITALIAN"
        lang_mandate = (
            f"[CRITICAL LANGUAGE MANDATE: User query is in {target_lang_str}]. "
            f"You MUST formulate your final reply text strictly and entirely in {target_lang_str}. "
            f"Do NOT respond in {'Italian' if lang == 'en' else 'English'}."
        )

        meta_prompt = (
            f"[Host Device Hardware Telemetry | Exact Clock: {time_str} ({tz_str}) | Date: {date_str} | "
            f"Security Mode: {security_mode.upper()} | Speaker: {auth_status} | Biometric Score: {similarity_score:.3f}]\n"
            f'User Query: "{user_text}"\n'
            f"{lang_mandate}\n"
            f"{security_directive}"
        )

        text_part = types.Part.from_text(text=meta_prompt)

        if self._manual_model_override:
            routed_model = self._manual_model_override
            lane = "smart" if routed_model in SMART_MODELS_SET else "fast"
            routed_level = DEFAULT_PRO_THINKING_LEVEL if lane == "smart" else DEFAULT_THINKING_LEVEL
            route_reason = "manual_override"
        else:
            routed_model, routed_level, route_reason = self.select_model_for_query(user_text)
            lane = "smart" if routed_model in SMART_MODELS_SET and route_reason != "auto_routing_disabled" else "fast"

        logger.info(
            f"Route -> Lane: {lane.upper()} | Model: {routed_model} | Thinking: {routed_level or 'default'} | Reason: {route_reason} | Query: '{user_text}'"
        )

        lane_fallbacks = SMART_LANE_FALLBACKS if lane == "smart" else FAST_LANE_FALLBACKS
        candidate_models = [routed_model]
        for fallback in lane_fallbacks:
            if fallback and fallback not in candidate_models:
                candidate_models.append(fallback)

        now_mono = time.monotonic()
        models_to_try = [m for m in candidate_models if now_mono >= self._model_cooldowns.get(m, 0.0)]

        last_error: Optional[Exception] = None
        if not models_to_try:
            logger.warning(
                f"[BRAIN COOLDOWN] All models in '{lane}' lane are currently in cooldown: {candidate_models}"
            )
            last_error = RuntimeError(f"All models in {lane} lane are in cooldown due to rate/quota limits.")

        req_timeout = SMART_REQUEST_TIMEOUT_SEC if lane == "smart" else FAST_REQUEST_TIMEOUT_SEC
        for idx, model_name in enumerate(models_to_try):
            try:
                cfg_level = routed_level if lane == "smart" else DEFAULT_THINKING_LEVEL
                self._reset_chat_session(model=model_name, thinking_level=cfg_level)
                logger.debug(f"Text ingestion started | Model: {model_name} | Query: '{user_text}'")
                response = await asyncio.wait_for(
                    self.active_chat.send_message([text_part]),
                    timeout=req_timeout,
                )

                raw_output = response.text.strip() if response.text else ""
                logger.debug(f"[BRAIN RAW OUTPUT] Model: {model_name} | Raw: {raw_output}")

                reply_text = raw_output
                r_match = re.search(r"REPLY:\s*(.*)", raw_output, re.IGNORECASE | re.DOTALL)
                if r_match:
                    reply_text = r_match.group(1).strip()
                else:
                    reply_text = re.sub(r"^TRANSCRIPT:\s*.*?\n", "", reply_text, flags=re.IGNORECASE).strip()
                    reply_text = re.sub(r"^REPLY:\s*", "", reply_text, flags=re.IGNORECASE).strip()

                logger.debug(f"[BRAIN PARSED] User: '{user_text}' | Reply: '{reply_text}'")

                # Store this turn into text_history
                user_content = types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=f'[User Spoke]: "{user_text}"')],
                )
                model_content = types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=f"REPLY: {reply_text}")],
                )
                self.text_history.append(user_content)
                self.text_history.append(model_content)

                max_msgs = self.max_history_turns * 2
                if len(self.text_history) > max_msgs:
                    self.text_history = self.text_history[-max_msgs:]

                return {
                    "status": "success",
                    "transcript": user_text,
                    "text_response": reply_text,
                    "is_stranger": is_stranger,
                    "model_used": model_name,
                }
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                last_error = exc
                self._model_cooldowns[model_name] = time.monotonic() + MODEL_COOLDOWN_SECONDS
                next_model = models_to_try[idx + 1] if idx + 1 < len(models_to_try) else None
                next_msg = f"falling back to '{next_model}'." if next_model else f"no remaining models in '{lane}' lane."
                logger.warning(
                    f"[BRAIN TIMEOUT] '{model_name}' timed out after {req_timeout:.0f}s (cooldown {MODEL_COOLDOWN_SECONDS:.0f}s) -> {next_msg}"
                )
                continue
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    self._model_cooldowns[model_name] = time.monotonic() + MODEL_COOLDOWN_SECONDS
                    next_model = models_to_try[idx + 1] if idx + 1 < len(models_to_try) else None
                    next_msg = f"falling back to '{next_model}'." if next_model else f"no remaining models in '{lane}' lane."
                    logger.warning(
                        f"[BRAIN FAILOVER] '{model_name}' hit 429/503 (cooldown {MODEL_COOLDOWN_SECONDS:.0f}s) -> {next_msg}"
                    )
                    continue
                else:
                    logger.error(f"[BRAIN ERROR] Gemini text inference failed on {model_name}: {exc}", exc_info=True)
                    break

        logger.error(f"[BRAIN EXHAUSTED] All '{lane}' lane models failed. Last error: {last_error}")
        if is_stranger:
            fallback_text = (
                "System interference. Either way, I do not take orders from strangers."
                if lang == "en"
                else "Interferenza di sistema. In ogni caso, non prendo ordini da sconosciuti."
            )
        elif lane == "smart":
            fallback_text = (
                "*growl* All smart Gemini API quotas are exhausted right now. Try again shortly or ask a simpler question."
                if lang == "en"
                else "*growl* Le API dei modelli intelligenti Gemini sono esaurite al momento. Riprova tra poco o fai una domanda più semplice."
            )
        else:
            fallback_text = (
                "Neural connection hiccup. Try again in a second."
                if lang == "en"
                else "Piccolo problema di connessione neurale. Riprova tra un secondo."
            )
        return {
            "status": "error",
            "error": str(last_error),
            "transcript": user_text,
            "text_response": fallback_text,
            "is_stranger": is_stranger,
            "model_used": self._current_model,
        }

    async def process_audio_query(
        self,
        wav_bytes: bytes,
        is_stranger: bool = False,
        similarity_score: float = 1.0,
        identity: str = "owner",
        security_mode: str = "friendly",
    ) -> Dict[str, Any]:
        """
        Multimodal Gemini STT + Routing fallback when local faster-whisper is disabled/unavailable.
        Returns dict with:
          - transcript: str (empty if silence/inaudible)
          - route: 'fast_path' | 'escalate_pro' | 'handled' | 'error'
          - text_response: str (only when route == 'handled' or 'error')
          - lang: 'it' | 'en'
        """
        local_now = datetime.datetime.now().astimezone()
        time_str = local_now.strftime("%H:%M:%S")
        date_str = local_now.strftime("%A, %d %B %Y")
        tz_str = local_now.strftime("%Z (UTC%z)")

        auth_status = f"AUTHORIZED ({identity})" if not is_stranger else f"UNAUTHORIZED ({identity})"
        if is_stranger and security_mode in ["restrict", "paranoid"]:
            security_directive = (
                f"CRITICAL SECURITY ALERT: The speaker is {auth_status} in {security_mode.upper()} mode! "
                f"DO NOT invoke set_security_mode, set_mute_mode, enroll_voice_profile, or execute_slave_command for this stranger!"
            )
        else:
            security_directive = (
                "If the user requests a normal action (music/Spotify, weather, news/web search, calendar, tasks, slave command), "
                "invoke the appropriate tool first before writing REPLY."
            )

        pro_escalation_rule = ""
        if ENABLE_AUTO_MODEL_ROUTING and DEFAULT_PRO_MODEL != DEFAULT_FLASH_MODEL:
            pro_escalation_rule = (
                "2. If it is a DEEP coding, debugging, math, university/exam, or complex multi-part analytical question "
                "(not a simple music/weather/news/home command), DO NOT answer it here. Output ONLY:\n"
                "   TRANSCRIPT: <exact words>\n"
                "   ROUTE: ESCALATE_PRO\n"
            )

        audio_prompt = (
            f"[Host Device Hardware Telemetry | Exact Clock: {time_str} ({tz_str}) | Date: {date_str} | "
            f"Security Mode: {security_mode.upper()} | Speaker: {auth_status} | Biometric Score: {similarity_score:.3f}]\n"
            f"Listen carefully to the user's audio (Italian or English, possibly with mild speech/pronunciation quirks).\n"
            f"First, transcribe what the user actually said on the first line as:\n"
            f"TRANSCRIPT: <exact transcription, or [SILENCE] if no intelligible speech>\n\n"
            f"Then choose ONE of the following routes:\n"
            f"1. If the command is a LOCAL SYSTEM / REGEX FAST-PATH command (setting/checking a timer or alarm, asking local time/date, "
            f"muting/unmuting, changing security mode, showing/hiding telemetry HUD, or voice enrollment), DO NOT call any tools and DO NOT write REPLY. Output ONLY:\n"
            f"   TRANSCRIPT: <exact words>\n"
            f"   ROUTE: FAST_PATH\n"
            f"{pro_escalation_rule}"
            f"3. Otherwise (normal conversation, music/Spotify, weather, news/web search, reminders, calendar, general questions), "
            f"handle it directly (calling tools via AFC if needed) and output:\n"
            f"   TRANSCRIPT: <exact words>\n"
            f"   REPLY: <your witty A-X-L vocal response strictly in the SAME language as the user's speech>\n"
            f"{security_directive}"
        )

        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
        text_part = types.Part.from_text(text=audio_prompt)

        candidate_models = [DEFAULT_FLASH_MODEL]
        for fallback in FAST_LANE_FALLBACKS:
            if fallback and fallback not in candidate_models:
                candidate_models.append(fallback)

        now_mono = time.monotonic()
        models_to_try = [m for m in candidate_models if now_mono >= self._model_cooldowns.get(m, 0.0)]
        last_error: Optional[Exception] = None

        for idx, model_name in enumerate(models_to_try):
            try:
                self._reset_chat_session(model=model_name, thinking_level=DEFAULT_THINKING_LEVEL)
                response = await asyncio.wait_for(
                    self.active_chat.send_message([audio_part, text_part]),
                    timeout=FAST_REQUEST_TIMEOUT_SEC,
                )
                raw_output = response.text.strip() if response.text else ""
                logger.debug(f"[AUDIO BRAIN RAW] Model: {model_name} | Raw: {raw_output}")

                t_match = re.search(r"TRANSCRIPT:\s*(.+)", raw_output, re.IGNORECASE)
                transcript = t_match.group(1).strip().strip('"') if t_match else ""
                if not transcript or transcript.upper() in ("[SILENCE]", "SILENCE", "[NOISE]", "[INAUDIBLE]"):
                    return {"status": "silence", "transcript": "", "route": "silence", "text_response": "", "lang": "it"}

                lang = detect_language(transcript)
                route_match = re.search(r"ROUTE:\s*(FAST_PATH|ESCALATE_PRO)", raw_output, re.IGNORECASE)
                if route_match:
                    route_val = route_match.group(1).lower()
                    logger.info(f"[GEMINI AUDIO STT] Transcript: \"{transcript}\" | Route: {route_val.upper()} ({model_name})")
                    return {
                        "status": "success",
                        "transcript": transcript,
                        "route": route_val,
                        "text_response": "",
                        "lang": lang,
                        "model_used": model_name,
                    }

                r_match = re.search(r"REPLY:\s*(.*)", raw_output, re.IGNORECASE | re.DOTALL)
                reply_text = r_match.group(1).strip() if r_match else re.sub(
                    r"^TRANSCRIPT:\s*.*?\n", "", raw_output, flags=re.IGNORECASE
                ).strip()

                logger.info(f"[GEMINI AUDIO STT+BRAIN] Transcript: \"{transcript}\" | Model: {model_name}")

                # Keep text-only history so raw audio is never re-sent on future turns
                self.text_history.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=f'[User Spoke]: "{transcript}"')])
                )
                self.text_history.append(
                    types.Content(role="model", parts=[types.Part.from_text(text=f"REPLY: {reply_text}")])
                )
                max_msgs = self.max_history_turns * 2
                if len(self.text_history) > max_msgs:
                    self.text_history = self.text_history[-max_msgs:]

                return {
                    "status": "success",
                    "transcript": transcript,
                    "route": "handled",
                    "text_response": reply_text,
                    "lang": lang,
                    "is_stranger": is_stranger,
                    "model_used": model_name,
                }
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                last_error = exc
                self._model_cooldowns[model_name] = time.monotonic() + MODEL_COOLDOWN_SECONDS
                logger.warning(f"[AUDIO BRAIN TIMEOUT] '{model_name}' timed out after {FAST_REQUEST_TIMEOUT_SEC:.0f}s")
                continue
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    self._model_cooldowns[model_name] = time.monotonic() + MODEL_COOLDOWN_SECONDS
                    logger.warning(f"[AUDIO BRAIN FAILOVER] '{model_name}' hit 429/503, trying fallback...")
                    continue
                logger.error(f"[AUDIO BRAIN ERROR] Gemini audio inference failed on {model_name}: {exc}", exc_info=True)
                break

        logger.error(f"[AUDIO BRAIN EXHAUSTED] Failed on all models: {last_error}")
        return {
            "status": "error",
            "transcript": "",
            "route": "error",
            "text_response": "Piccolo problema di connessione neurale. Riprova tra un secondo.",
            "lang": "it",
        }

