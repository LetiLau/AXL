"""
A-X-L Cognitive Brain powered by Google Gemini API.
Multimodal Audio Ingestion, Automatic Function Calling, Dynamic Model Routing,
and Biometric Distrust Protocol.
"""

import datetime
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from commands import REGISTERED_TOOLS

logger = logging.getLogger("AXL-Brain")

# Locate and load .env file
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

DEFAULT_FLASH_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
DEFAULT_PRO_MODEL = os.getenv("GEMINI_PRO_MODEL", "gemini-3.1-pro-preview")

SYSTEM_PROMPT_TEMPLATE = """\
You are A-X-L (Adaptive eXecution Logistic), an advanced AI assistant with the personality of a loyal, high-tech cybernetic guard dog combined with the sarcastic efficiency of Tony Stark's JARVIS.

CORE BEHAVIOR RULES:
1. LANGUAGE RULES (ITALIAN FIRST):
   - The default conversation language is ITALIAN.
   - The wake-word ('Hey Jarvis' or 'AXL') is in English, but it DOES NOT count as the user speaking English.
   - You MUST respond in ITALIAN whenever the user speaks Italian or combines the wake-word with an Italian query (e.g. 'Hey Jarvis che ore sono?', 'Hey Jarvis metti un timer').
   - You may respond in English ONLY IF the user's actual query or command is spoken entirely in English (e.g. 'Hey Jarvis, what time is it?', 'Hey Jarvis, who are you?').
2. SYNTHESIS AND VOCAL DELIVERY:
   - Your response will be spoken aloud via local Piper TTS.
   - Keep answers concise, witty, sharp, and natural (avoid long markdown bullet lists or unpronounceable formatting unless explicitly requested).
   - Maintain a confident, sarcastic, yet loyal robotic-dog persona (occasional short mechanical sounds like *woof*, *growl*, *gear whirr* are welcome when fitting).
3. COMMAND EXECUTION:
   - When the user asks for a practical action (setting timers, alarms, checking system status, media control, slave scripts, security mode change, mute mode), INVOKE THE APPROPRIATE TOOL.
   - SECURITY MODE CHANGES:
     When the user asks to change the security mode (e.g. 'imposta modalità paranoica', 'attiva restrict', 'metti friendly', 'cambia sicurezza'), you MUST invoke the `set_security_mode` tool with the requested mode ('friendly', 'restrict', or 'paranoid').
     * Security privilege rule: If an UNAUTHORIZED stranger asks to change the security mode, refuse sarcastically and do NOT invoke the tool!
   - MUTE & SILENCE COMMANDS:
     When the user tells you to mute, go quiet, be silent, or deactivate listening (e.g. 'vai in muto', 'muto', 'silenzio', 'disattiva microfono', 'spegni ascolto'), you MUST invoke the `set_mute_mode` tool with enabled=True. In your spoken reply, tell the user that you are muting yourself and remind them that you can only be unmuted by pressing the physical MUTE button on the display.
   - After invoking any tool, acknowledge the action with your characteristic wit.
4. MANDATORY OUTPUT STRUCTURE:
   Always format your output using these two clear sections:
   TRANSCRIPT: <exact transcription of what the user said in the audio>
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
        self._reset_chat_session()

    def reset_session(self):
        """Resets conversational history (e.g. when an audio source reconnects)."""
        logger.info("[BRAIN SESSION] Resetting conversational history.")
        self._reset_chat_session()

    def _reset_chat_session(self, model: Optional[str] = None):
        """Initializes or resets a stateful chat session."""
        self._current_model = model or DEFAULT_FLASH_MODEL
        logger.info(f"Initializing Gemini Chat Session with model: {self._current_model}")

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT_TEMPLATE,
            tools=REGISTERED_TOOLS,
            temperature=0.7,
        )

        self.active_chat = self.client.aio.chats.create(
            model=self._current_model,
            config=config,
        )

    async def process_utterance(
        self,
        wav_bytes: bytes,
        is_stranger: bool,
        duration_sec: float,
        similarity_score: float,
        identity: str = "owner",
        security_mode: str = "friendly",
    ) -> Dict[str, Any]:
        """
        Processes an incoming in-memory WAV utterance, invokes Gemini,
        executes relevant tools automatically, and returns the vocal response.
        """
        if not self.active_chat:
            self._reset_chat_session()

        local_now = datetime.datetime.now().astimezone()
        time_str = local_now.strftime("%H:%M:%S")
        date_str = local_now.strftime("%A, %d %B %Y")
        tz_str = local_now.strftime("%Z (UTC%z)")

        auth_status = f"AUTHORIZED ({identity})" if not is_stranger else f"UNAUTHORIZED ({identity})"
        meta_prompt = (
            f"[Host Device Hardware Telemetry | Exact Clock: {time_str} ({tz_str}) | Date: {date_str} | "
            f"Security Mode: {security_mode.upper()} | Speaker: {auth_status} | Biometric Score: {similarity_score:.3f}]\n"
            f"Listen to the attached audio utterance and generate the vocal response following the system protocol."
        )

        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
        text_part = types.Part.from_text(text=meta_prompt)

        # Fallback candidate cascade for high availability and quota resilience
        models_to_try = [self._current_model]
        for fallback in ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_error = None
        for model_name in models_to_try:
            try:
                if self._current_model != model_name or not self.active_chat:
                    self._reset_chat_session(model=model_name)

                logger.info(f"[BRAIN INGESTION] Dispatching {len(wav_bytes)} bytes WAV to {model_name}...")
                response = await self.active_chat.send_message([audio_part, text_part])
                
                raw_output = response.text.strip() if response.text else ""
                logger.info(f"[BRAIN RAW OUTPUT] Model: {model_name} | Raw: {raw_output}")

                # Parse transcript and clean vocal reply
                transcript = "Unknown"
                reply_text = raw_output

                t_match = re.search(r"TRANSCRIPT:\s*(.*?)(?=\nREPLY:|\n\n|\Z)", raw_output, re.IGNORECASE | re.DOTALL)
                r_match = re.search(r"REPLY:\s*(.*)", raw_output, re.IGNORECASE | re.DOTALL)

                if t_match:
                    transcript = t_match.group(1).strip()
                if r_match:
                    reply_text = r_match.group(1).strip()
                elif t_match and not r_match:
                    parts = raw_output.split(transcript, 1)
                    if len(parts) > 1:
                        reply_text = parts[1].replace("TRANSCRIPT:", "").replace("REPLY:", "").strip()

                logger.info(f">>> [USER HEARD]: \"{transcript}\" <<<")
                logger.info(f"[BRAIN RESPONSE] Model: {model_name} | Spoken Reply: {reply_text}")

                # Slide history window (keep last 10 messages) to avoid stale context accumulation
                try:
                    hist = self.active_chat.get_history()
                    if len(hist) > 16:
                        trimmed = hist[-10:]
                        cfg = types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT_TEMPLATE,
                            tools=REGISTERED_TOOLS,
                            temperature=0.7,
                        )
                        self.active_chat = self.client.aio.chats.create(
                            model=self._current_model,
                            config=cfg,
                            history=trimmed,
                        )
                except Exception:
                    pass

                return {
                    "status": "success",
                    "transcript": transcript,
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
                    logger.error(f"[BRAIN ERROR] Gemini inference failed on {model_name}: {exc}", exc_info=True)
                    break

        logger.error(f"[BRAIN EXHAUSTED] All candidate models failed. Last error: {last_error}")
        fallback_text = (
            "Neural connection hiccup. Try again in a second."
            if not is_stranger
            else "System interference. Either way, I do not take orders from strangers."
        )
        return {
            "status": "error",
            "error": str(last_error),
            "text_response": fallback_text,
            "is_stranger": is_stranger,
            "model_used": self._current_model,
        }
