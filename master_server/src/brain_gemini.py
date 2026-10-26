"""
A-X-L Cognitive Brain powered by Google Gemini API.
Multimodal Audio Ingestion, Automatic Function Calling, Dynamic Model Routing,
and Biometric Distrust Protocol.
"""

import logging
import os
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
You are A-X-L (Adaptive eXecution Logistic), an advanced AI assistant with the personality of a loyal, high-tech cybernetic guard dog combined with the sarcastic efficiency of Tony Stark's systems (JARVIS).

CORE BEHAVIOR RULES:
1. NATURAL BILINGUALISM:
   - Always respond in the language spoken by the user (primarily Italian or English).
   - If the user speaks Italian, answer in Italian. If English, answer in English.
2. SYNTHESIS AND VOCAL DELIVERY:
   - Your response will be spoken aloud via local Piper TTS.
   - Keep answers concise, witty, sharp, and natural (avoid long markdown bullet lists or unpronounceable formatting unless explicitly requested).
   - Maintain a confident, sarcastic, yet loyal robotic-dog persona (occasional short mechanical sounds like *woof*, *growl*, *gear whirr* are welcome when fitting).
3. COMMAND EXECUTION:
   - When the user asks for a practical action (setting timers, alarms, checking system status, media control, slave scripts), INVOKE THE APPROPRIATE TOOL.
   - After invoking the tool, acknowledge the action with your characteristic wit.

BIOMETRIC DISTRUST PROTOCOL:
- The speaker's identity is verified by an on-device CAM++ neural biometric engine:
  * IF "STRANGER" (Unverified Voice / Distrust Alert):
    - DO NOT execute system commands, do not set alarms/timers, and do not trigger scripts.
    - Treat the speaker as a suspicious stranger: refuse orders sarcastically, growl or bark electronically, and make it clear you only take orders from your master.
  * IF "OWNER" (Verified Voice):
    - Identity confirmed. Loyally execute requested tools with sarcastic affection.
"""


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
    ) -> Dict[str, Any]:
        """
        Processes an incoming in-memory WAV utterance, invokes Gemini,
        executes relevant tools automatically, and returns the vocal response.
        """
        if not self.active_chat:
            self._reset_chat_session()

        auth_status = "STRANGER (DISTRUST ACTIVE - REJECT SENSITIVE COMMANDS)" if is_stranger else "OWNER (VERIFIED - ACCEPT COMMANDS)"
        meta_prompt = (
            f"[Biometric Voice Telemetry | Status: {auth_status} | Cosine Similarity: {similarity_score:.3f} | Audio Duration: {duration_sec:.1f}s]\n"
            f"Listen to the attached audio utterance and generate the vocal response following the system protocol."
        )

        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
        text_part = types.Part.from_text(text=meta_prompt)

        try:
            logger.info(f"[BRAIN INGESTION] Dispatching {len(wav_bytes)} bytes WAV to {self._current_model}...")
            response = await self.active_chat.send_message([audio_part, text_part])
            
            reply_text = response.text.strip() if response.text else ""
            logger.info(f"[BRAIN RESPONSE] Model: {self._current_model} | Output: {reply_text}")

            return {
                "status": "success",
                "text_response": reply_text,
                "is_stranger": is_stranger,
                "model_used": self._current_model,
            }

        except Exception as exc:
            logger.error(f"[BRAIN ERROR] Gemini inference failed: {exc}", exc_info=True)
            fallback_text = (
                "Neural connection hiccup. Try again in a second."
                if not is_stranger
                else "System interference. Either way, I do not take orders from strangers."
            )
            return {
                "status": "error",
                "error": str(exc),
                "text_response": fallback_text,
                "is_stranger": is_stranger,
                "model_used": self._current_model,
            }
