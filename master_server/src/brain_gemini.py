"""
A-X-L Cognitive Brain powered by Google Gemini API.
Automatic Function Calling, Dynamic Model Routing, and Biometric Distrust Protocol.
"""

import datetime
import logging
import os
import re
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

DEFAULT_FLASH_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
DEFAULT_PRO_MODEL = os.getenv("GEMINI_PRO_MODEL", "gemini-3.1-pro-preview")

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
   - MUSIC PLAYBACK & SPOTIFY:
     * When the user asks to play music, a song, an artist, or a playlist (e.g. 'metti una canzone dei Queen', 'fammi sentire Lazza', 'metti musica per studiare'):
       YOU MUST INVOKE the `play_music` tool with the extracted query (e.g. query='Queen', query='Lazza 100 Messaggi').
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
   - When the user asks for the time, date, or day, always state the exact local time from the device clock (or invoke get_current_time).
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
        # Conversational memory kept strictly in lightweight text format to prevent
        # multi-audio re-encoding latency, 429 quota exhaustion, and cross-turn audio confusion.
        self.text_history: List[types.Content] = []
        self.max_history_turns: int = 8
        self._reset_chat_session()

    def reset_session(self):
        """Resets conversational history (e.g. when an audio source reconnects)."""
        logger.info("[BRAIN SESSION] Resetting conversational history.")
        self.text_history.clear()
        self._reset_chat_session()

    def _reset_chat_session(self, model: Optional[str] = None):
        """Initializes or resets a stateful chat session."""
        self._current_model = model or DEFAULT_FLASH_MODEL
        logger.info(f"[AXL-Brain]: Cognitive Core ready | Active Model: {self._current_model}")

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT_TEMPLATE,
            tools=REGISTERED_TOOLS,
            temperature=0.7,
        )

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
        """Dynamically switches the active Gemini model."""
        if model_name != self._current_model:
            logger.info(f"[AXL-Brain]: Model switched dynamically to: {model_name}")
            self._reset_chat_session(model=model_name)

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

        models_to_try = [self._current_model]
        for fallback in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_error = None
        for model_name in models_to_try:
            try:
                self._reset_chat_session(model=model_name)
                logger.info(f"[AXL-Brain]: Text ingestion started | Model: {model_name} | Query: '{user_text}'")
                response = await self.active_chat.send_message([text_part])

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
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                if "429" in err_str or "503" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(
                        f"[BRAIN FAILOVER] Model '{model_name}' hit quota or capacity spike (429/503). Retrying fallback..."
                    )
                    continue
                else:
                    logger.error(f"[BRAIN ERROR] Gemini text inference failed on {model_name}: {exc}", exc_info=True)
                    break

        logger.error(f"[BRAIN EXHAUSTED] All candidate models failed. Last error: {last_error}")
        if lang == "en":
            fallback_text = (
                "Neural connection hiccup. Try again in a second."
                if not is_stranger
                else "System interference. Either way, I do not take orders from strangers."
            )
        else:
            fallback_text = (
                "Piccolo problema di connessione neurale. Riprova tra un secondo."
                if not is_stranger
                else "Interferenza di sistema. In ogni caso, non prendo ordini da sconosciuti."
            )
        return {
            "status": "error",
            "error": str(last_error),
            "transcript": user_text,
            "text_response": fallback_text,
            "is_stranger": is_stranger,
            "model_used": self._current_model,
        }

