import asyncio
import json
import logging
import math
import os
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional, Set
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")
MODELS_DIR = BASE_DIR / "models"

import httpx
import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import numpy as np
import uvicorn

from audio_pipeline import AudioPipeline
from audio_utils import pcm_to_wav_bytes
from brain_gemini import GeminiBrain
from ring_buffer import AudioRingBuffer
from speaker_verifier import SpeakerVerifier
from tts_engine import PiperTTSEngine
from telemetry_utils import collect_system_telemetry
from commands import (
    detect_enrollment_intent,
    detect_security_mode_in_reply,
    detect_security_mode_intent,
    detect_telemetry_intent,
    is_mute_in_reply,
    is_mute_intent,
    register_telemetry_listener,
    set_command_auth_context,
)

THRESHOLD_VAR = float(os.getenv("AXL_SPEAKER_THRESHOLD", "0.48"))  # Configurable via .env, calibrated with tools/calibrate_speaker.py

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("AXL-Master")

# Suppress verbose third-party loggers for a clean terminal output
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)


class StreamManager:
    """Manages audio streaming sessions, pipeline dispatching, and background task routing."""

    def __init__(self):
        self.audio_buffer = AudioRingBuffer(capacity_seconds=10.0, sample_rate=16000)
        self.pipeline: Optional[AudioPipeline] = None
        self.verifier: Optional[SpeakerVerifier] = None
        self.brain: Optional[GeminiBrain] = None
        self.tts: Optional[PiperTTSEngine] = None
        self.active_client: Optional[WebSocket] = None
        self.ui_clients: Set[WebSocket] = set()
        self.ui_client_info: Dict[WebSocket, dict] = {}
        self._assigned_browser_nodes: Set[int] = set()
        self.is_speaking: bool = False
        self.stop_speech_requested: bool = False
        self.is_muted: bool = False
        self.is_enrolling: bool = False
        self.enrollment_target: Optional[str] = None
        self.enrollment_queue: asyncio.Queue = asyncio.Queue()
        self._enrollment_task: Optional[asyncio.Task] = None
        self.telemetry_active: bool = False
        self.telemetry_target: str = "fedora"
        self.last_kiosk_telemetry: Optional[dict] = None
        self._telemetry_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def initialize_pipeline(self):
        logger.info("Initializing neural DSP, wake-word, speaker verification, Gemini brain, and Piper TTS...")
        self.pipeline = AudioPipeline(
            models_dir=MODELS_DIR,
            vad_threshold=0.4,
            wakeword_threshold=0.35,
            silence_hangover_sec=0.5,
            max_command_duration_sec=10.0,
        )
        self.verifier = SpeakerVerifier(models_dir=MODELS_DIR, threshold=THRESHOLD_VAR)
        self.brain = GeminiBrain()
        self.tts = PiperTTSEngine(models_dir=MODELS_DIR)

        # Register telemetry bridge for function calling and HUD synchronization
        register_telemetry_listener(self.handle_telemetry_event)
        logger.info("A-X-L Full Pipeline (Acoustic + Biometrics + Brain + TTS + Telemetry Bridge) Ready.")

    async def handle_telemetry_event(self, payload: dict):
        """Processes telemetry activation, security mode changes, or mute state triggered by voice commands or slave."""
        ptype = payload.get("type")
        if ptype == "SECURITY_MODE":
            new_mode = payload.get("mode", "friendly")
            if self.verifier:
                self.verifier.set_security_mode(new_mode)
            os.environ["AXL_SECURITY_MODE"] = new_mode
            logger.info(f"[SECURITY MODE] Switched by command to '{new_mode}'")
            await self.broadcast_ui({"type": "SECURITY_MODE", "mode": new_mode})
            return

        if ptype == "SET_MUTE":
            self.is_muted = bool(payload.get("muted", True))
            if self.is_muted:
                if self.pipeline:
                    self.pipeline.reset()
                self.audio_buffer.clear()
            logger.info(f"[MUTE CONTROLLER] Mute mode updated by command: {self.is_muted}")
            await self.broadcast_ui({"type": "MUTE_STATE", "muted": self.is_muted})
            return

        if ptype == "START_VOICE_ENROLLMENT":
            pname = payload.get("profile_name", "owner")
            self.start_voice_enrollment(pname)
            return

        enabled = payload.get("enabled", self.telemetry_active)
        raw_target = payload.get("target", self.telemetry_target)
        target = "server" if str(raw_target).lower().strip() in ("server", "master", "thinkpad", "locale", "local") else "slave"
        if enabled != self.telemetry_active or target != self.telemetry_target:
            await self.set_telemetry_mode(enabled, target=target)
        else:
            self.telemetry_target = target
            await self.broadcast_ui(payload)

    async def set_telemetry_mode(self, enabled: bool, target: str = "slave"):
        """Activates or deactivates live telemetry streaming mode for server, slave, or kiosk."""
        self.telemetry_active = enabled
        target_norm = str(target).lower().strip()
        if target_norm in ("server", "master", "thinkpad", "locale", "local"):
            self.telemetry_target = "server"
        elif target_norm in ("kiosk", "android", "phone", "galaxy", "a33"):
            self.telemetry_target = "kiosk"
        else:
            self.telemetry_target = "slave"
        logger.info(f"[TELEMETRY MODE] State updated: {enabled} (target: {self.telemetry_target})")

        # Broadcast state change to UI
        await self.broadcast_ui({
            "type": "TELEMETRY_MODE",
            "enabled": self.telemetry_active,
            "target": self.telemetry_target,
        })

        if self.telemetry_active:
            if self.telemetry_target in ("server", "master"):
                await self.broadcast_ui({
                    "type": "TELEMETRY_UPDATE",
                    "target": self.telemetry_target,
                    "data": self._collect_local_telemetry(),
                })
            elif self.telemetry_target == "kiosk" and self.last_kiosk_telemetry:
                await self.broadcast_ui({
                    "type": "TELEMETRY_UPDATE",
                    "target": "kiosk",
                    "data": self.last_kiosk_telemetry,
                })
            if self._telemetry_task is None or self._telemetry_task.done():
                self._telemetry_task = asyncio.create_task(self._telemetry_poll_loop())
        else:
            if self._telemetry_task and not self._telemetry_task.done():
                self._telemetry_task.cancel()
                self._telemetry_task = None

    def _collect_local_telemetry(self) -> dict:
        """Universal hardware telemetry collector via psutil and multi-GPU probes."""
        return collect_system_telemetry(node_name="master_server")

    async def _telemetry_poll_loop(self):
        """Background poller that periodically fetches metrics from slave daemon or local server and broadcasts to UI."""
        logger.info("[TELEMETRY STREAM] Background poller loop started.")
        slave_url = os.environ.get("AXL_SLAVE_URL", "http://127.0.0.1:8001")
        try:
            while self.telemetry_active:
                if self.ui_clients:
                    if self.telemetry_target in ("server", "master"):
                        await self.broadcast_ui({
                            "type": "TELEMETRY_UPDATE",
                            "target": self.telemetry_target,
                            "data": self._collect_local_telemetry(),
                        })
                    else:
                        try:
                            async with httpx.AsyncClient(timeout=1.5) as client:
                                resp = await client.get(f"{slave_url}/telemetry")
                                if resp.status_code == 200:
                                    await self.broadcast_ui({
                                        "type": "TELEMETRY_UPDATE",
                                        "target": self.telemetry_target,
                                        "data": resp.json(),
                                    })
                                else:
                                    await self.broadcast_ui({
                                        "type": "TELEMETRY_UPDATE",
                                        "target": self.telemetry_target,
                                        "data": self._collect_local_telemetry(),
                                    })
                        except Exception:
                            await self.broadcast_ui({
                                "type": "TELEMETRY_UPDATE",
                                "target": self.telemetry_target,
                                "data": self._collect_local_telemetry(),
                            })
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[TELEMETRY STREAM] Background poller loop terminated.")

    async def register_ui(self, websocket: WebSocket, is_kiosk: bool = False):
        """Registers a connected Web UI client and sends initial state synchronization with assigned node tag."""
        await websocket.accept()
        self.ui_clients.add(websocket)

        if is_kiosk:
            node_tag = "KIOSK"
            node_num = None
        else:
            node_num = 1
            while node_num in self._assigned_browser_nodes:
                node_num += 1
            self._assigned_browser_nodes.add(node_num)
            node_tag = f"NODE-{node_num:02d}"

        self.ui_client_info[websocket] = {
            "is_kiosk": is_kiosk,
            "node_num": node_num,
            "node_tag": node_tag,
        }

        logger.info(
            f"UI client attached: [{node_tag}] (is_kiosk={is_kiosk}). Active UI clients: {len(self.ui_clients)}"
        )
        sec_mode = self.verifier.security_mode if self.verifier else "friendly"
        state = self.pipeline.state if self.pipeline else "IDLE"
        ui_state = "LISTENING" if state == "LISTENING_COMMAND" else ("SPEAKING" if self.is_speaking else "IDLE")
        await websocket.send_json({
            "type": "INIT",
            "state": ui_state,
            "security_mode": sec_mode,
            "is_muted": self.is_muted,
            "telemetry_active": self.telemetry_active,
            "telemetry_target": self.telemetry_target,
            "node_tag": node_tag,
            "is_kiosk": is_kiosk,
        })

    async def unregister_ui(self, websocket: WebSocket):
        """Detaches a Web UI client cleanly and frees its node slot."""
        self.ui_clients.discard(websocket)
        info = self.ui_client_info.pop(websocket, None)
        if info and not info.get("is_kiosk"):
            num = info.get("node_num")
            if num is not None:
                self._assigned_browser_nodes.discard(num)
        node_tag = info.get("node_tag", "UNKNOWN") if info else "UNKNOWN"
        logger.info(f"UI client [{node_tag}] detached. Active UI clients: {len(self.ui_clients)}")

    async def broadcast_ui(self, message: dict):
        """Dispatches an asynchronous state event to all connected Web UI clients."""
        if not self.ui_clients:
            return
        dead_clients = set()
        for ws in list(self.ui_clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead_clients.add(ws)
        if dead_clients:
            for ws in dead_clients:
                await self.unregister_ui(ws)

    async def connect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if self.active_client is not None:
                return False
            await websocket.accept()
            self.active_client = websocket
            self.audio_buffer.clear()
            if self.pipeline:
                self.pipeline.reset()
            if self.brain:
                self.brain.reset_session()
            return True

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if self.active_client == websocket:
                self.active_client = None
                if self.pipeline:
                    self.pipeline.reset()
                logger.info("Active audio source detached.")

    async def handle_completed_command(self, pcm_samples: np.ndarray, reason: str):
        """
        Non-blocking background handler for captured utterances.
        Encapsulates PCM into in-memory WAV and evaluates speaker identity.
        """
        duration_sec = len(pcm_samples) / 16000.0
        logger.info(
            f"[UTTERANCE DISPATCH] Processing {len(pcm_samples)} samples ({duration_sec:.2f}s) | Trigger: {reason}"
        )

        # 1. Non-blocking In-Memory WAV packaging
        wav_bytes = pcm_to_wav_bytes(pcm_samples, sample_rate=16000)
        logger.info(f"[WAV PACKAGING] In-memory WAV generated: {len(wav_bytes)} bytes (0 disk I/O)")

        # Save last captured utterance for easy acoustic debugging (ignored by git)
        try:
            debug_wav_path = BASE_DIR / "debug_last_utterance.wav"
            with open(debug_wav_path, "wb") as f_debug:
                f_debug.write(wav_bytes)
        except Exception:
            pass

        # 2. Biometric Verification executed in thread pool to prevent loop latency spikes
        is_authorized, sim_score, identity = await asyncio.to_thread(
            self.verifier.verify, pcm_samples
        )

        sec_mode = self.verifier.security_mode if self.verifier else "friendly"
        if is_authorized:
            logger.info(
                f">>> [AUTH GRANTED] Identity: {identity.upper()} | Score: {sim_score:.3f} | Mode: {sec_mode} <<<"
            )
        else:
            logger.warning(
                f">>> [AUTH REJECTED] Identity: {identity.upper()} | Score: {sim_score:.3f} | Mode: {sec_mode} <<<"
            )

        # Set biometric authorization context for tool invocation (AFC)
        set_command_auth_context(
            is_authorized=is_authorized,
            identity=identity,
            security_mode=sec_mode,
            similarity_score=sim_score,
        )

        # Broadcast biometric verification event to connected HUD visors
        await self.broadcast_ui({
            "type": "AUTH_EVENT",
            "identity": identity,
            "score": float(sim_score),
            "authorized": bool(is_authorized),
        })

        # 3. Cognitive Brain Processing (Multimodal WAV + Function Calling)
        is_stranger = not is_authorized
        if self.brain:
            result = await self.brain.process_utterance(
                wav_bytes=wav_bytes,
                is_stranger=is_stranger,
                duration_sec=duration_sec,
                similarity_score=sim_score,
                identity=identity,
                security_mode=sec_mode,
            )
            transcript = result.get("transcript", "Unknown")
            vocal_reply = result.get("text_response", "")
            logger.info(f">>> [USER HEARD]: \"{transcript}\" <<<")
            logger.info(f">>> [A-X-L SPEAKS]: \"{vocal_reply}\" <<<")

            # --- Synchronous Intent Guardrails (Guarantees 100% action execution fidelity) ---
            # Guardrail 1: Mute Command (checks both user transcript and spoken confirmation)
            mute_triggered = is_mute_intent(transcript) or is_mute_in_reply(vocal_reply)
            can_mute = is_authorized or (sec_mode == "friendly")
            if not self.is_muted and mute_triggered:
                if can_mute:
                    logger.info(f"[COMMAND GUARDRAIL] Engaging mute mode (transcript: '{transcript}' | reply: '{vocal_reply[:60]}...').")
                    await self.handle_telemetry_event({"type": "SET_MUTE", "muted": True})
                else:
                    logger.warning(
                        f"[COMMAND GUARDRAIL] Blocked unauthorized mute attempt from speaker '{identity}' in '{sec_mode}' mode."
                    )

            # Guardrail 2: Security Mode Command (checks both user transcript and spoken confirmation)
            req_mode = detect_security_mode_intent(transcript) or detect_security_mode_in_reply(vocal_reply)
            if req_mode and self.verifier and req_mode != self.verifier.security_mode:
                can_switch = False
                if sec_mode == "friendly":
                    can_switch = True
                elif sec_mode == "restrict":
                    can_switch = is_authorized
                elif sec_mode == "paranoid":
                    can_switch = is_authorized and (identity.lower() == "owner")

                if can_switch:
                    logger.info(f"[COMMAND GUARDRAIL] Switching security mode to '{req_mode}' (speaker: {identity}).")
                    await self.handle_telemetry_event({"type": "SECURITY_MODE", "mode": req_mode})
                else:
                    logger.warning(
                        f"[COMMAND GUARDRAIL] Blocked unauthorized security mode switch to '{req_mode}' "
                        f"from speaker '{identity}' (score: {sim_score:.3f}, mode: '{sec_mode}')."
                    )

            # Guardrail 3: Telemetry HUD Command
            telem_intent = detect_telemetry_intent(transcript)
            if telem_intent is not None:
                req_show = telem_intent.get("show", True)
                req_tgt = telem_intent.get("target", self.telemetry_target)
                if req_show != self.telemetry_active or (req_show and req_tgt != self.telemetry_target):
                    logger.info(f"[COMMAND GUARDRAIL] Toggling telemetry HUD: show={req_show}, target={req_tgt}")
                    await self.set_telemetry_mode(req_show, target=req_tgt)

            # Guardrail 4: Voice Enrollment Intent
            enroll_intent = detect_enrollment_intent(transcript)
            if enroll_intent is not None and not self.is_enrolling:
                target_profile = enroll_intent.get("profile_name", "owner")
                can_enroll = False
                if target_profile == "owner":
                    if sec_mode in ["restrict", "paranoid"]:
                        can_enroll = is_authorized and (identity.lower() == "owner")
                    else:
                        can_enroll = is_authorized and (identity.lower() == "owner" or "owner" not in self.verifier.enrolled_profiles)
                else:
                    if sec_mode in ["restrict", "paranoid"]:
                        can_enroll = is_authorized and (identity.lower() == "owner")
                    else:
                        can_enroll = is_authorized

                if can_enroll:
                    logger.info(f"[COMMAND GUARDRAIL] Initiating voice calibration session for '{target_profile}'")
                    self.start_voice_enrollment(target_profile)
                else:
                    logger.warning(
                        f"[COMMAND GUARDRAIL] Blocked unauthorized voice enrollment attempt for '{target_profile}' "
                        f"from speaker '{identity}' in '{sec_mode}' mode."
                    )

            # Broadcast dialogue subtitles to connected HUD visors
            await self.broadcast_ui({
                "type": "TRANSCRIPT",
                "user_text": transcript,
                "axl_text": vocal_reply,
            })

            # 4. Neural Text-To-Speech Synthesis & Streaming via WebSocket
            if vocal_reply and self.tts and self.active_client:
                logger.info("[TTS STREAM] Synthesizing and streaming response to client...")
                await self.speak_text(vocal_reply)
                if not self.stop_speech_requested:
                    logger.info("[TTS STREAM] Vocal response fully delivered.")

    async def speak_text(self, text: str) -> bool:
        """
        Synthesizes and streams a vocal response to the active client.
        Crucially maintains is_speaking = True and UI state = SPEAKING for the full
        duration of the actual audio playback so the STOP button remains active.
        """
        if not text or not self.tts or not self.active_client:
            return False
        self.is_speaking = True
        self.stop_speech_requested = False
        await self.broadcast_ui({
            "type": "STATE_CHANGE",
            "state": "SPEAKING",
            "label": "VOCAL TRANSMISSION // STREAMING PCM",
        })
        try:
            pcm_bytes = await self.tts.synthesize_bytes(text)
            if not pcm_bytes:
                return False

            total_duration_sec = len(pcm_bytes) / 32000.0
            logger.info(f"[TTS STREAM] Total vocal response duration: {total_duration_sec:.2f}s ({len(pcm_bytes)} bytes)")

            chunk_size = 1280  # 40ms @ 16kHz 16-bit Mono
            playback_start_time = time.monotonic()
            bytes_sent = 0
            total_bytes = len(pcm_bytes)

            for offset in range(0, total_bytes, chunk_size):
                if self.stop_speech_requested:
                    logger.info("[TTS STREAM] Speech transmission interrupted by user STOP.")
                    return False
                chunk = pcm_bytes[offset : offset + chunk_size]
                if self.active_client:
                    await self.active_client.send_bytes(chunk)
                bytes_sent += len(chunk)

                # Pace transmission so client buffer neither starves nor overflows
                elapsed = time.monotonic() - playback_start_time
                sent_audio_time = bytes_sent / 32000.0
                lead_time = sent_audio_time - elapsed
                if lead_time > 0.35:
                    await asyncio.sleep(lead_time - 0.20)

            # Audio chunks have been completely transmitted over the network.
            # Keep is_speaking = True until the physical speaker finishes playback!
            while not self.stop_speech_requested:
                elapsed = time.monotonic() - playback_start_time
                remaining = total_duration_sec - elapsed
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))

            if not self.stop_speech_requested:
                logger.info(f"[TTS STREAM] Vocal response playback fully completed ({total_duration_sec:.2f}s).")
            return not self.stop_speech_requested
        except Exception as exc:
            logger.error(f"[TTS STREAM ERROR] Failed sending audio: {exc}")
            return False
        finally:
            self.is_speaking = False
            self.stop_speech_requested = False
            await asyncio.sleep(0.15)
            await self.broadcast_ui({
                "type": "STATE_CHANGE",
                "state": "IDLE",
            })

    def start_voice_enrollment(self, profile_name: str):
        """Starts an asynchronous interactive voice calibration session."""
        if self.is_enrolling:
            logger.warning(f"[VOICE ENROLLMENT] Session already in progress for '{self.enrollment_target}'.")
            return
        self.is_enrolling = True
        self.enrollment_target = profile_name
        self._enrollment_task = asyncio.create_task(self._run_enrollment_session(profile_name))

    async def _run_enrollment_session(self, profile_name: str):
        """
        Executes a 3-take conversational voice calibration session directly via the active audio client.
        Isolates pure human speech with Silero VAD, computes CAM++ centroid, and dynamically updates profiles in RAM.
        """
        logger.info(f"=== [VOICE ENROLLMENT] Starting online calibration for '{profile_name}' ===")
        vad = self.pipeline.vad if self.pipeline else None
        if not vad or not self.verifier:
            logger.error("[VOICE ENROLLMENT] Pipeline VAD or SpeakerVerifier not available!")
            self.is_enrolling = False
            return

        # Wait briefly for any ongoing vocal reply to complete
        while self.is_speaking:
            await asyncio.sleep(0.1)

        try:
            target_samples = int(3.0 * 16000)  # 3.0s pure speech = 48,000 samples
            takes_samples: List[np.ndarray] = []
            prompts = [
                f"*pant* Iniziamo la calibrazione vocale per {profile_name}. Campione uno di tre: parla chiaramente per tre secondi dopo il verso. *pant*",
                f"Primo campione acquisito. Campione due di tre: dimmi un'altra frase qualsiasi. *pant*",
                f"Ottimo lavoro. Terzo e ultimo campione: continua a parlare ancora un momento. *pant*",
            ]

            for take_idx in range(1, 4):
                if self.stop_speech_requested:
                    logger.info("[VOICE ENROLLMENT] Cancelled by user STOP.")
                    break

                # 1. Vocal prompt to user
                success = await self.speak_text(prompts[take_idx - 1])
                if not success or self.stop_speech_requested:
                    logger.info("[VOICE ENROLLMENT] Interrupted during vocal prompt.")
                    break

                # 2. Flush enrollment queue of stale frames
                while not self.enrollment_queue.empty():
                    try:
                        self.enrollment_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                # 3. Update Visor UI state
                await self.broadcast_ui({
                    "type": "STATE_CHANGE",
                    "state": "LISTENING",
                    "label": f"ACQUISIZIONE VOCE // TAKE {take_idx}/3 (0%)",
                })

                # 4. Gather pure speech with Silero VAD
                vad.reset_state()
                collected_speech: List[np.ndarray] = []
                collected_samples = 0
                take_start = time.monotonic()
                timeout_sec = 14.0
                vad_residual = bytearray()
                last_reported_pct = -1

                while collected_samples < target_samples:
                    if self.stop_speech_requested:
                        break
                    if time.monotonic() - take_start > timeout_sec:
                        logger.warning(
                            f"[VOICE ENROLLMENT] Take {take_idx} timeout (collected {collected_samples}/{target_samples} samples)."
                        )
                        break

                    try:
                        data = await asyncio.wait_for(self.enrollment_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue

                    vad_residual.extend(data)
                    # Silero VAD window is 512 samples = 1024 bytes (16-bit)
                    while len(vad_residual) >= 1024:
                        window_bytes = vad_residual[:1024]
                        del vad_residual[:1024]

                        samples_int16 = np.frombuffer(window_bytes, dtype=np.int16)
                        samples_f32 = samples_int16.astype(np.float32) / 32768.0

                        prob = vad.process_window(samples_f32)
                        if prob >= 0.50:
                            collected_speech.append(samples_int16)
                            collected_samples += len(samples_int16)

                            pct = min(100, int((collected_samples / target_samples) * 100))
                            if pct // 10 != last_reported_pct:
                                last_reported_pct = pct // 10
                                await self.broadcast_ui({
                                    "type": "STATE_CHANGE",
                                    "state": "LISTENING",
                                    "label": f"ACQUISIZIONE VOCE // TAKE {take_idx}/3 ({pct}%)",
                                })

                if self.stop_speech_requested:
                    break

                if collected_samples >= 24000:  # At least 1.5s pure speech required
                    take_audio = np.concatenate(collected_speech)
                    takes_samples.append(take_audio)
                    logger.info(f"[VOICE ENROLLMENT] Take {take_idx}/3 collected successfully ({len(take_audio)} samples).")
                else:
                    logger.warning(f"[VOICE ENROLLMENT] Take {take_idx}/3 failed: insufficient speech frames.")
                    await self.speak_text("*growl* Campione non sufficiente. Calibrazione interrotta.")
                    break

            # 5. Process embeddings and save profile
            if len(takes_samples) == 3 and not self.stop_speech_requested:
                logger.info(f"[VOICE ENROLLMENT] Computing neural CAM++ embeddings for '{profile_name}'...")
                await self.broadcast_ui({
                    "type": "STATE_CHANGE",
                    "state": "THINKING",
                    "label": "ELABORAZIONE CENTROIDE BIOMETRICO...",
                })

                embeddings = []
                for idx, t_audio in enumerate(takes_samples, 1):
                    emb = await asyncio.to_thread(self.verifier.extract_embedding, t_audio)
                    if emb is not None:
                        norm = np.linalg.norm(emb)
                        if norm > 0:
                            embeddings.append(emb / norm)
                            logger.info(f"[VOICE ENROLLMENT] Take {idx} embedding generated (norm: {norm:.4f}).")

                if len(embeddings) == 3:
                    centroid = np.mean(embeddings, axis=0)
                    centroid = centroid / np.linalg.norm(centroid)

                    for i, emb in enumerate(embeddings, 1):
                        sim = float(np.dot(emb, centroid))
                        logger.info(f"[VOICE ENROLLMENT] Take {i} vs Centroid: {sim:.4f}")

                    out_profile = self.verifier.profiles_dir / f"{profile_name}.npy"
                    np.save(out_profile, centroid)

                    # Save debug WAV for human audit
                    try:
                        all_audio = np.concatenate(takes_samples)
                        debug_wav = self.verifier.profiles_dir / f"{profile_name}_debug_voice.wav"
                        with wave.open(str(debug_wav), "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(16000)
                            wf.writeframes(all_audio.tobytes())
                    except Exception as err:
                        logger.warning(f"Could not save debug WAV: {err}")

                    # Dynamic in-memory reload
                    self.verifier.load_profiles()
                    logger.info(f">>> [VOICE ENROLLMENT SUCCESS] Profile '{profile_name}' enrolled and loaded in RAM! <<<")

                    await self.speak_text(
                        f"*bark* Calibrazione completata, capo! Il profilo vocale di {profile_name} è registrato e attivo nel sistema."
                    )
                else:
                    logger.error("[VOICE ENROLLMENT ERROR] Failed extracting embeddings.")
                    await self.speak_text("*growl* Errore durante l'estrazione degli embedding. Riprova più tardi.")
            else:
                if not self.stop_speech_requested:
                    logger.info("[VOICE ENROLLMENT] Enrollment aborted due to incomplete takes.")
        except Exception as exc:
            logger.error(f"[VOICE ENROLLMENT ERROR] Exception in enrollment session: {exc}", exc_info=True)
            if not self.stop_speech_requested:
                await self.speak_text("*growl* Si è verificato un errore durante la calibrazione.")
        finally:
            self.is_enrolling = False
            self.enrollment_target = None
            self.stop_speech_requested = False
            await self.broadcast_ui({
                "type": "STATE_CHANGE",
                "state": "IDLE",
                "label": "SURVEILLANCE // PASSIVE IDLE",
            })


stream_manager = StreamManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting A-X-L Master Core...")
    stream_manager.initialize_pipeline()
    yield
    logger.info("Shutting down A-X-L Master Core...")


app = FastAPI(title="A-X-L System Master Server", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health_check():
    state = stream_manager.pipeline.state if stream_manager.pipeline else "OFFLINE"
    prob = stream_manager.pipeline.last_speech_prob if stream_manager.pipeline else 0.0
    profiles = list(stream_manager.verifier.enrolled_profiles.keys()) if stream_manager.verifier else []
    return {
        "status": "online",
        "system": "A-X-L Core",
        "pipeline_state": state,
        "vad_prob": round(prob, 3),
        "enrolled_profiles": profiles,
    }


@app.websocket("/ws/audio")
async def audio_websocket_endpoint(websocket: WebSocket):
    connected = await stream_manager.connect(websocket)
    if not connected:
        logger.warning("Rejected duplicate connection.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info(f"Audio source connected: {client_host}")

    last_mic_broadcast_time = 0.0
    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) % 2 != 0:
                continue

            # Inhibit audio ingestion while A-X-L is speaking or muted
            if stream_manager.is_speaking or stream_manager.is_muted:
                continue

            # Route incoming audio directly to the enrollment queue during calibration
            if stream_manager.is_enrolling:
                try:
                    stream_manager.enrollment_queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass
                continue

            stream_manager.audio_buffer.write(data)

            # Broadcast real-time microphone volume level (RMS/dBFS) to Web UI (~20 updates/sec)
            if stream_manager.ui_clients:
                now_mono = time.monotonic()
                if now_mono - last_mic_broadcast_time >= 0.05:
                    last_mic_broadcast_time = now_mono
                    chunk_samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    rms = float(np.sqrt(np.mean(chunk_samples**2)))
                    db = 20.0 * math.log10(rms / 32767.0) if rms > 1e-4 else -96.0
                    norm_level = max(0.0, min(1.0, (db + 55.0) / 40.0))
                    asyncio.create_task(stream_manager.broadcast_ui({
                        "type": "MIC_LEVEL",
                        "level": round(norm_level, 3),
                        "db": round(db, 1),
                    }))

            if stream_manager.pipeline:
                events = stream_manager.pipeline.ingest_frame(data)
                for ev in events:
                    ev_type = ev["event"]

                    if ev_type == "WAKEWORD_PROBE":
                        logger.debug(f"[PROBE] Model: {ev['model']} | Score: {ev['score']:.3f}")

                    elif ev_type == "WAKEWORD_TRIGGER":
                        logger.info(
                            f"\n>>> [WAKEWORD ACTIVATED] Triggered! Model: {ev['model']} | Score: {ev['score']:.3f} <<<\n"
                        )
                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "LISTENING",
                            "score": float(ev["score"]),
                        })

                    elif ev_type == "COMMAND_COMPLETE":
                        num_samples = ev["total_samples"]
                        command_audio = stream_manager.audio_buffer.get_last_samples(num_samples)

                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "THINKING",
                        })

                        # Delegate execution asynchronously: WebSocket loop remains unblocked
                        asyncio.create_task(
                            stream_manager.handle_completed_command(
                                command_audio, ev["trigger_reason"]
                            )
                        )

                    elif ev_type == "SILENCE_RESET":
                        silence_time = ev.get("silence_sec", 0.0)
                        reason = ev.get("reason", "unknown")
                        logger.info(
                            f"[PIPELINE] Reset to IDLE | Silence: {silence_time:.2f}s | Reason: {reason}"
                        )
                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "IDLE",
                        })

    except WebSocketDisconnect:
        logger.info(f"Client disconnected cleanly: {client_host}")
    except Exception as exc:
        logger.error(f"Streaming error: {exc}", exc_info=True)
    finally:
        await stream_manager.disconnect(websocket)


@app.websocket("/ws/ui")
async def ui_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint serving real-time HUD telemetry and state transitions."""
    client_param = websocket.query_params.get("client", "").lower().strip()
    ua = websocket.headers.get("user-agent", "").lower()
    is_kiosk = ("axl-kiosk" in ua) or (client_param == "kiosk")
    await stream_manager.register_ui(websocket, is_kiosk=is_kiosk)
    try:
        while True:
            # Accept incoming control signals from UI (e.g. toggle telemetry, target switch)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                mtype = msg.get("type")
                if mtype == "TOGGLE_TELEMETRY":
                    enabled = msg.get("enabled", not stream_manager.telemetry_active)
                    target = msg.get("target", stream_manager.telemetry_target)
                    await stream_manager.set_telemetry_mode(enabled, target=target)
                elif mtype == "KIOSK_TELEMETRY":
                    stream_manager.last_kiosk_telemetry = msg
                    if stream_manager.telemetry_active and stream_manager.telemetry_target == "kiosk":
                        await stream_manager.broadcast_ui({
                            "type": "TELEMETRY_UPDATE",
                            "target": "kiosk",
                            "data": msg,
                        })
                elif mtype == "SET_SECURITY_MODE":
                    new_mode = str(msg.get("mode", "")).lower().strip()
                    cur_mode = (
                        stream_manager.verifier.security_mode
                        if stream_manager.verifier
                        else os.environ.get("AXL_SECURITY_MODE", "friendly").lower().strip()
                    )
                    # Only allow UI button to lock down from 'friendly' to 'restrict' or 'paranoid'
                    if cur_mode == "friendly" and new_mode in ("restrict", "paranoid"):
                        if stream_manager.verifier:
                            stream_manager.verifier.set_security_mode(new_mode)
                        os.environ["AXL_SECURITY_MODE"] = new_mode
                        await stream_manager.broadcast_ui({
                            "type": "SECURITY_MODE",
                            "mode": new_mode,
                        })
                elif mtype == "TOGGLE_MUTE":
                    new_mute = bool(msg.get("muted", not stream_manager.is_muted))
                    stream_manager.is_muted = new_mute
                    if stream_manager.is_muted:
                        if stream_manager.pipeline:
                            stream_manager.pipeline.reset()
                        stream_manager.audio_buffer.clear()
                    logger.info(f"[MUTE CONTROLLER] Mute state updated via UI: {stream_manager.is_muted}")
                    await stream_manager.broadcast_ui({"type": "MUTE_STATE", "muted": stream_manager.is_muted})
                elif mtype == "STOP_SPEECH":
                    logger.info("[STOP BUTTON] User requested immediate vocal/enrollment abort.")
                    stream_manager.stop_speech_requested = True
                    stream_manager.is_speaking = False
                    stream_manager.is_enrolling = False
                    if stream_manager._enrollment_task and not stream_manager._enrollment_task.done():
                        stream_manager._enrollment_task.cancel()
                    if stream_manager.pipeline:
                        stream_manager.pipeline.reset()
                    stream_manager.audio_buffer.clear()
                    if stream_manager.active_client:
                        try:
                            await stream_manager.active_client.send_text("STOP")
                        except Exception:
                            pass
                    await stream_manager.broadcast_ui({
                        "type": "STATE_CHANGE",
                        "state": "IDLE",
                        "label": "SURVEILLANCE // PASSIVE IDLE",
                    })
                elif mtype == "ASK_START":
                    # Push-to-ask: skips the wake-word. Speaker verification still applies afterwards.
                    if stream_manager.is_speaking:
                        logger.info("[ASK BUTTON] Ignored (A-X-L is speaking).")
                    elif stream_manager.is_muted:
                        logger.warning("[ASK BUTTON] Ignored: microphone is muted.")
                    elif not stream_manager.active_client or not stream_manager.pipeline:
                        logger.warning("[ASK BUTTON] Ignored: no audio source connected.")
                    elif stream_manager.pipeline.force_listen():
                        logger.info("[ASK BUTTON] Listening started without wake-word.")
                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "LISTENING",
                        })
                    else:
                        logger.info("[ASK BUTTON] Ignored: pipeline busy.")
                elif mtype == "REQUEST_TELEMETRY":
                    req_target = msg.get("target", stream_manager.telemetry_target)
                    if req_target in ("server", "master"):
                        await websocket.send_json({
                            "type": "TELEMETRY_UPDATE",
                            "target": req_target,
                            "data": stream_manager._collect_local_telemetry(),
                        })
                    else:
                        slave_url = os.environ.get("AXL_SLAVE_URL", "http://127.0.0.1:8001")
                        try:
                            async with httpx.AsyncClient(timeout=1.5) as client:
                                resp = await client.get(f"{slave_url}/telemetry")
                                if resp.status_code == 200:
                                    await websocket.send_json({
                                        "type": "TELEMETRY_UPDATE",
                                        "target": req_target,
                                        "data": resp.json(),
                                    })
                                else:
                                    await websocket.send_json({
                                        "type": "TELEMETRY_UPDATE",
                                        "target": req_target,
                                        "data": stream_manager._collect_local_telemetry(),
                                    })
                        except Exception:
                            await websocket.send_json({
                                "type": "TELEMETRY_UPDATE",
                                "target": req_target,
                                "data": stream_manager._collect_local_telemetry(),
                            })
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(f"UI WebSocket client disconnected with notice: {exc}")
    finally:
        await stream_manager.unregister_ui(websocket)


@app.middleware("http")
async def disable_ui_cache(request: Request, call_next):
    """Prevents stale browser caching of Web UI CSS/JS assets."""
    response = await call_next(request)
    if request.url.path.startswith("/ui"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Mount Web UI frontend files for browser and Galaxy A33 kiosk access
WEB_UI_DIR = BASE_DIR / "web_ui"
if WEB_UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(WEB_UI_DIR), html=True), name="ui")


@app.get("/")
async def root():
    """Redirects base URL to the cybernetic Visor Web UI."""
    return RedirectResponse(url="/ui/")


if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False, 
        workers=1
    )