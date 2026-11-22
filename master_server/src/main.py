import asyncio
import errno
import json
import logging
import math
import os
import random
import shutil
import socket
import subprocess
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
from fastapi.responses import HTMLResponse, RedirectResponse
import numpy as np
import uvicorn

from audio_pipeline import AudioPipeline
from audio_utils import normalize_pcm_for_stt, pcm_to_wav_bytes
from brain_gemini import GeminiBrain
from ring_buffer import AudioRingBuffer
from speaker_verifier import SpeakerVerifier
from spotify_controller import spotify_controller
from tts_engine import PiperTTSEngine, detect_language
from stt_engine import STTEngine
from telemetry_utils import collect_system_telemetry
from console_manager import ConsoleManager
from commands import (
    detect_enrollment_intent,
    detect_security_mode_in_reply,
    detect_security_mode_intent,
    detect_telemetry_intent,
    ensure_dog_sound,
    get_random_dog_sound,
    handle_local_fast_path,
    is_mute_in_reply,
    is_mute_intent,
    is_unmute_intent,
    register_telemetry_listener,
    set_command_auth_context,
)

THRESHOLD_VAR = float(os.getenv("AXL_SPEAKER_THRESHOLD", "0.48"))  # Configurable via .env, calibrated with tools/calibrate_speaker.py
WAKEWORD_THRESHOLD = float(os.getenv("AXL_WAKEWORD_THRESHOLD", "0.48"))  # Configurable via .env, prevents false positives

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
logging.getLogger("faster_whisper").setLevel(logging.WARNING)

THINKING_PHRASES_IT = [
    "*pant* Sto elaborando, dammi un secondo...",
    "*pant* Aspetta un attimo, ci sto pensando...",
    "*bark* Calma, sto analizzando i dati neurali...",
    "*pant* Fammi riflettere un momento...",
    "*bark* Un secondo, consulto i miei circuiti...",
]

THINKING_PHRASES_EN = [
    "*pant* Hold on, crunching the neural data...",
    "*pant* Give me a second, I'm thinking...",
    "*bark* Processing, hold your horses...",
    "*pant* Thinking, give me a moment...",
    "*bark* Stand by, querying cognitive core...",
]


def get_random_thinking_phrase(lang: str = "it") -> str:
    """Returns a witty canine thinking filler phrase in Italian or English."""
    if lang == "en":
        return random.choice(THINKING_PHRASES_EN)
    return random.choice(THINKING_PHRASES_IT)


class StreamManager:
    """Manages audio streaming sessions, pipeline dispatching, and background task routing."""

    def __init__(self):
        self.audio_buffer = AudioRingBuffer(capacity_seconds=10.0, sample_rate=16000)
        self.pipeline: Optional[AudioPipeline] = None
        self.verifier: Optional[SpeakerVerifier] = None
        self.stt: Optional[STTEngine] = None
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
        self._active_command_task: Optional[asyncio.Task] = None
        self._active_brain_task: Optional[asyncio.Task] = None
        self._command_generation: int = 0
        self.telemetry_active: bool = False
        self.telemetry_target: str = "fedora"
        self.last_kiosk_telemetry: Optional[dict] = None
        self._telemetry_task: Optional[asyncio.Task] = None
        self._server_heartbeat_task: Optional[asyncio.Task] = None
        self._music_poll_task: Optional[asyncio.Task] = None
        self.last_music_state: Optional[dict] = None
        self.music_player_visible: bool = False
        self._duck_state: Optional[dict] = None
        self._duck_safety_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def cancel_active_commands(self, reason: str = "superseded"):
        """Cancels any in-flight command/brain tasks and invalidates stale turns."""
        self._command_generation += 1
        if self._active_brain_task and not self._active_brain_task.done():
            self._active_brain_task.cancel()
            self._active_brain_task = None
            logger.debug(f"[TASK CANCEL] Cancelled previous brain task ({reason}).")
        if self._active_command_task and not self._active_command_task.done():
            self._active_command_task.cancel()
            self._active_command_task = None
            logger.debug(f"[TASK CANCEL] Cancelled previous command task ({reason}).")

    def initialize_pipeline(self):
        logger.info("Initializing neural DSP, wake-word, speaker verification, STT, Gemini brain, and Piper TTS...")
        self.pipeline = AudioPipeline(
            models_dir=MODELS_DIR,
            vad_threshold=0.4,
            wakeword_threshold=WAKEWORD_THRESHOLD,
            silence_hangover_sec=0.5,
            max_command_duration_sec=10.0,
        )
        self.verifier = SpeakerVerifier(models_dir=MODELS_DIR, threshold=THRESHOLD_VAR)
        stt_backend = os.getenv("AXL_STT_BACKEND", "local").strip().lower()
        if stt_backend == "gemini":
            self.stt = None
            logger.info("[STT INIT] Bypassing local faster-whisper (AXL_STT_BACKEND=gemini); using Gemini multimodal STT.")
        elif stt_backend == "auto":
            try:
                self.stt = STTEngine()
            except Exception as exc:
                self.stt = None
                logger.warning(f"[STT INIT] Local faster-whisper unavailable ({exc}); falling back to Gemini multimodal STT.")
        else:
            self.stt = STTEngine()
        self.brain = GeminiBrain()
        self.tts = PiperTTSEngine(models_dir=MODELS_DIR)

        # Register telemetry bridge for function calling and HUD synchronization
        register_telemetry_listener(self.handle_telemetry_event)
        logger.info("A-X-L Full Pipeline (Acoustic + Biometrics + STT + Brain + TTS + Telemetry Bridge) Ready.")

    async def handle_telemetry_event(self, payload: dict):
        """Processes telemetry activation, intents, security mode changes, or mute state triggered by commands."""
        ptype = payload.get("type")
        if ptype == "INTENT":
            await self.dispatch_kiosk_intent(payload)
            return

        if ptype == "MUSIC_STATE":
            if payload.get("action") in ("set_volume", "volume_up", "volume_down", "mute"):
                # User explicitly changed volume via command; do not overwrite it on IDLE restore
                self._duck_state = None
                if self._duck_safety_task and not self._duck_safety_task.done():
                    self._duck_safety_task.cancel()
            await self.broadcast_ui(payload)
            asyncio.create_task(self.push_music_snapshot(delay=0.35, force=True))
            return

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
        target_norm = str(raw_target).lower().strip()
        if target_norm in ("server", "master", "thinkpad", "locale", "local"):
            target = "server"
        elif target_norm in ("kiosk", "android", "phone", "galaxy", "a33"):
            target = "kiosk"
        else:
            target = "slave"

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
                    "target": "server",
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
                            "target": "server",
                            "data": self._collect_local_telemetry(),
                        })
                    elif self.telemetry_target == "kiosk":
                        if self.last_kiosk_telemetry:
                            await self.broadcast_ui({
                                "type": "TELEMETRY_UPDATE",
                                "target": "kiosk",
                                "data": self.last_kiosk_telemetry,
                            })
                        else:
                            await self.broadcast_ui({
                                "type": "TELEMETRY_UPDATE",
                                "target": "kiosk",
                                "offline": True,
                                "error": "NO_KIOSK_FEED",
                                "data": None,
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
                                        "offline": True,
                                        "error": f"HTTP {resp.status_code}",
                                        "data": None,
                                    })
                        except Exception as e:
                            await self.broadcast_ui({
                                "type": "TELEMETRY_UPDATE",
                                "target": self.telemetry_target,
                                "offline": True,
                                "error": str(e),
                                "data": None,
                            })
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[TELEMETRY STREAM] Background poller loop terminated.")

    async def _server_heartbeat_loop(self):
        """Periodically broadcasts Master Server telemetry (every 1.0s) to feed the top mini-sparklines in UI."""
        logger.info("[SERVER HEARTBEAT] Started Master Server background telemetry feed.")
        try:
            while True:
                if self.ui_clients:
                    try:
                        server_data = self._collect_local_telemetry()
                        await self.broadcast_ui({
                            "type": "SERVER_TELEMETRY",
                            "data": server_data,
                        })
                    except Exception as ex:
                        logger.debug(f"[SERVER HEARTBEAT] Error broadcasting: {ex}")
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[SERVER HEARTBEAT] Stopped Master Server background telemetry feed.")

    async def push_music_snapshot(self, delay: float = 0.0, force: bool = False):
        """Fetches the current Spotify playback state and broadcasts MUSIC_UPDATE to connected UI clients."""
        if delay > 0:
            await asyncio.sleep(delay)
        if not spotify_controller.is_configured() or not spotify_controller.is_authenticated():
            return
        try:
            pb = await asyncio.to_thread(spotify_controller.get_current_playback)
            prev = self.last_music_state or {}
            changed = (
                force
                or not self.last_music_state
                or pb.get("playing") != prev.get("playing")
                or pb.get("track_uri") != prev.get("track_uri")
                or pb.get("track") != prev.get("track")
                or pb.get("device_name") != prev.get("device_name")
                or pb.get("repeat_state") != prev.get("repeat_state")
                or pb.get("shuffle_state") != prev.get("shuffle_state")
                or pb.get("volume_percent") != prev.get("volume_percent")
            )
            # Detect external timeline jump/seek on another device or periodic drift sync (>5s)
            if not changed and pb.get("playing"):
                elapsed = (pb.get("timestamp", 0) - prev.get("timestamp", 0))
                expected_prog = (prev.get("progress_ms") or 0) + max(0, elapsed)
                if abs((pb.get("progress_ms") or 0) - expected_prog) > 3500 or elapsed >= 5000:
                    changed = True

            if changed:
                self.last_music_state = pb
                if not pb.get("track") and self.music_player_visible:
                    self.music_player_visible = False
                elif pb.get("playing") and not self.music_player_visible:
                    self.music_player_visible = True
                    if self.ui_clients:
                        await self.broadcast_ui({
                            "type": "MUSIC_PLAYER_MODE",
                            "visible": True,
                        })

                if self.ui_clients:
                    await self.broadcast_ui({
                        "type": "MUSIC_UPDATE",
                        "data": pb,
                    })
        except Exception as exc:
            logger.debug(f"[MUSIC SYNC] Snapshot fetch error: {exc}")

    async def set_music_player_mode(self, visible: bool, pause_if_closing: bool = False):
        """Controls synchronized visibility of the music player card across all UI clients."""
        self.music_player_visible = visible
        logger.info(f"[MUSIC SYNC] set_music_player_mode: visible={visible} (pause_if_closing={pause_if_closing})")
        if not visible and pause_if_closing:
            if spotify_controller.is_configured() and spotify_controller.is_authenticated():
                try:
                    await asyncio.to_thread(spotify_controller.control_playback, "pause")
                except Exception as ex:
                    logger.debug(f"[MUSIC SYNC] Pause on close failed: {ex}")
        await self.broadcast_ui({
            "type": "MUSIC_PLAYER_MODE",
            "visible": self.music_player_visible,
        })
        await self.push_music_snapshot(delay=0.15, force=True)

    async def _music_poll_loop(self):
        """Background poller that keeps all Visor HUDs synced with external speaker/device Spotify playback."""
        logger.info("[MUSIC SYNC] Started background Spotify playback poller.")
        try:
            while True:
                if self.ui_clients and spotify_controller.is_configured() and spotify_controller.is_authenticated():
                    await self.push_music_snapshot(force=False)
                is_active = bool((self.last_music_state or {}).get("playing") or (self.last_music_state or {}).get("track"))
                await asyncio.sleep(2.5 if is_active else 5.0)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[MUSIC SYNC] Stopped background Spotify playback poller.")

    async def register_ui(self, websocket: WebSocket, is_kiosk: bool = False, is_kiosk_service: bool = False):
        """Registers a connected Web UI or Kiosk background service client and sends initial state sync."""
        await websocket.accept()
        self.ui_clients.add(websocket)

        if is_kiosk_service:
            node_tag = "KIOSK-SVC"
            node_num = None
        elif is_kiosk:
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
            "is_kiosk_service": is_kiosk_service,
            "node_num": node_num,
            "node_tag": node_tag,
        }

        logger.info(
            f"UI client attached: [{node_tag}] (is_kiosk={is_kiosk}, service={is_kiosk_service}). "
            f"Active UI clients: {len(self.ui_clients)}"
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
            "music_player_visible": self.music_player_visible,
            "node_tag": node_tag,
            "is_kiosk": is_kiosk,
        })

        # Send initial live Master Server telemetry frame for the top mini sparklines
        try:
            await websocket.send_json({
                "type": "SERVER_TELEMETRY",
                "data": self._collect_local_telemetry(),
            })
        except Exception:
            pass

        if self._server_heartbeat_task is None or self._server_heartbeat_task.done():
            self._server_heartbeat_task = asyncio.create_task(self._server_heartbeat_loop())

        if self.last_music_state:
            try:
                await websocket.send_json({
                    "type": "MUSIC_UPDATE",
                    "data": self.last_music_state,
                })
            except Exception:
                pass
        if self._music_poll_task is None or self._music_poll_task.done():
            self._music_poll_task = asyncio.create_task(self._music_poll_loop())

    async def unregister_ui(self, websocket: WebSocket):
        """Detaches a Web UI client cleanly and frees its node slot."""
        self.ui_clients.discard(websocket)
        info = self.ui_client_info.pop(websocket, None)
        if info and not info.get("is_kiosk") and not info.get("is_kiosk_service"):
            num = info.get("node_num")
            if num is not None:
                self._assigned_browser_nodes.discard(num)
        node_tag = info.get("node_tag", "UNKNOWN") if info else "UNKNOWN"
        logger.info(f"UI client [{node_tag}] detached. Active UI clients: {len(self.ui_clients)}")

    async def dispatch_kiosk_intent(self, payload: dict):
        """
        Sends an Android Intent payload to connected Kiosk service clients over /ws/ui.
        Falls back to Kiosk WebView UI clients or active audio client if no dedicated service link is attached.
        """
        action_name = payload.get("action", "unknown")
        service_targets = [
            ws for ws in list(self.ui_clients)
            if self.ui_client_info.get(ws, {}).get("is_kiosk_service")
        ]
        targets = service_targets or [
            ws for ws in list(self.ui_clients)
            if self.ui_client_info.get(ws, {}).get("is_kiosk")
        ]
        if targets:
            sent = 0
            for ws in targets:
                try:
                    await ws.send_json(payload)
                    sent += 1
                except Exception as exc:
                    logger.warning(f"[INTENT DISPATCH] Failed sending '{action_name}' to kiosk WS: {exc}")
            if sent > 0:
                logger.info(f"[INTENT DISPATCH] Dispatched '{action_name}' to {sent} kiosk link(s).")
                return

        if self.active_client:
            try:
                await self.active_client.send_text(json.dumps(payload))
                logger.info(f"[INTENT DISPATCH] Fallback dispatched '{action_name}' to audio client.")
            except Exception as exc:
                logger.warning(f"[INTENT DISPATCH] Failed forwarding '{action_name}' to audio client: {exc}")
        else:
            logger.warning(f"[INTENT DISPATCH] No connected Android Kiosk to receive '{action_name}'.")

    @staticmethod
    def _should_duck_playback(pb: Optional[dict]) -> bool:
        """Returns True only for room speakers/PCs that can leak audio into A33's mic (never phones/headphones)."""
        if not pb or not pb.get("playing"):
            return False
        if pb.get("supports_volume") is False:
            return False
        dev_name = str(pb.get("device_name") or "").lower()
        dev_type = str(pb.get("device_type") or "").lower()
        if not dev_name and not dev_type:
            return False

        # Always skip smartphones, tablets, earbuds, and headphones
        if dev_type in ("smartphone", "tablet"):
            return False
        if any(w in dev_name for w in ("s25", "a33", "phone", "cell", "buds", "earbuds", "airpods", "headphones", "cuffie", "headset")):
            return False

        if dev_type in ("speaker", "computer", "avr", "tv", "castaudio"):
            return True

        allowed_raw = os.getenv("AXL_DUCK_DEVICES", "sonos,salotto,pc,desktop,computer,speaker,cucina,camera")
        allowed_kws = [k.strip().lower() for k in allowed_raw.split(",") if k.strip()]
        return any(k in dev_name for k in allowed_kws)

    async def duck_music_for_voice(self):
        """Temporarily lowers room speaker volume during wake-word listening & TTS without pausing playback."""
        enabled = os.getenv("AXL_DUCK_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
        if not enabled or self._duck_state is not None:
            return
        if not spotify_controller.is_configured() or not spotify_controller.is_authenticated():
            return
        try:
            pb = self.last_music_state
            now_ms = int(time.time() * 1000)
            if not pb or (now_ms - int(pb.get("timestamp") or 0)) > 5000:
                pb = await asyncio.to_thread(spotify_controller.get_current_playback)
            if not self._should_duck_playback(pb):
                return
            cur_vol = pb.get("volume_percent")
            if cur_vol is None:
                return
            cur_vol = int(cur_vol)
            duck_target = max(5, min(60, int(os.getenv("AXL_DUCK_VOLUME", "18"))))
            if cur_vol <= duck_target:
                return
            dev_name = pb.get("device_name") or ""
            self._duck_state = {
                "device_id": pb.get("device_id") or "",
                "device_name": dev_name,
                "original_volume": cur_vol,
                "ducked_volume": duck_target,
            }
            res = await asyncio.to_thread(
                spotify_controller.control_playback, "set_volume", dev_name or None, duck_target
            )
            if res.get("ok"):
                logger.info(f"[AUTO-DUCK] Lowered '{dev_name}' volume {cur_vol}% -> {duck_target}% for voice.")
                if self._duck_safety_task and not self._duck_safety_task.done():
                    self._duck_safety_task.cancel()
                self._duck_safety_task = asyncio.create_task(self._duck_safety_timeout())
            else:
                self._duck_state = None
        except Exception as exc:
            self._duck_state = None
            logger.debug(f"[AUTO-DUCK] Duck skipped: {exc}")

    async def _duck_safety_timeout(self):
        try:
            await asyncio.sleep(25.0)
            if self._duck_state is not None:
                logger.warning("[AUTO-DUCK] Safety timeout reached; restoring original speaker volume.")
                await self.restore_music_ducking()
        except asyncio.CancelledError:
            pass

    async def restore_music_ducking(self):
        """Restores original room speaker volume after voice interaction returns to IDLE."""
        state = self._duck_state
        self._duck_state = None
        if self._duck_safety_task and not self._duck_safety_task.done():
            self._duck_safety_task.cancel()
        self._duck_safety_task = None
        if not state:
            return
        try:
            dev_name = state.get("device_name") or ""
            orig_vol = int(state.get("original_volume", 50))
            res = await asyncio.to_thread(
                spotify_controller.control_playback, "set_volume", dev_name or None, orig_vol
            )
            if res.get("ok"):
                logger.info(f"[AUTO-DUCK] Restored '{dev_name}' volume -> {orig_vol}%.")
        except Exception as exc:
            logger.debug(f"[AUTO-DUCK] Restore failed: {exc}")

    async def broadcast_ui(self, message: dict):
        """Dispatches an asynchronous state event to all connected Web UI clients."""
        if message.get("type") == "STATE_CHANGE":
            st = message.get("state")
            if st == "LISTENING":
                asyncio.create_task(self.duck_music_for_voice())
            elif st == "IDLE":
                asyncio.create_task(self.restore_music_ducking())
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
                old_client = self.active_client
                old_host = old_client.client.host if old_client.client else "unknown"
                new_host = websocket.client.host if websocket.client else "unknown"
                logger.info(
                    f"[AUDIO TAKEOVER] Gracefully disconnecting previous audio source ({old_host}) "
                    f"to accept new incoming audio source ({new_host})."
                )
                try:
                    await old_client.close(code=status.WS_1000_NORMAL_CLOSURE, reason="Audio takeover by new client")
                except Exception:
                    pass
                self.active_client = None

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
        self.stop_speech_requested = False
        my_gen = self._command_generation
        duration_sec = len(pcm_samples) / 16000.0
        logger.debug(
            f"[UTTERANCE DISPATCH] Processing {len(pcm_samples)} samples ({duration_sec:.2f}s) | Trigger: {reason}"
        )

        # 1. Non-blocking In-Memory WAV packaging
        wav_bytes = pcm_to_wav_bytes(pcm_samples, sample_rate=16000)
        logger.debug(f"[WAV PACKAGING] In-memory WAV generated: {len(wav_bytes)} bytes (0 disk I/O)")

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
                f">>> [AUTH GRANTED] Identity: {identity.upper()} | Score: {sim_score:.3f} | Mode: {sec_mode} | Audio: {duration_sec:.2f}s ({reason}) <<<"
            )
        else:
            logger.warning(
                f">>> [AUTH REJECTED] Identity: {identity.upper()} | Score: {sim_score:.3f} | Mode: {sec_mode} | Audio: {duration_sec:.2f}s ({reason}) <<<"
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

        # 3. Speech-to-Text: Local faster-whisper OR Gemini multimodal fallback (AXL_STT_BACKEND)
        transcript = ""
        precomputed_reply: Optional[str] = None
        detected_lang: Optional[str] = None

        if self.stt:
            transcript = await self.stt.transcribe(pcm_samples)
        elif self.brain:
            norm_float, rms, gain, is_silence = normalize_pcm_for_stt(pcm_samples)
            if is_silence:
                logger.info(f"[GEMINI STT SILENCE] Audio energy too low (RMS={rms:.5f}), ignoring.")
            else:
                norm_int16 = np.clip(norm_float * 32767.0, -32768, 32767).astype(np.int16)
                stt_wav_bytes = pcm_to_wav_bytes(norm_int16, sample_rate=16000)
                audio_res = await self.brain.process_audio_query(
                    wav_bytes=stt_wav_bytes,
                    is_stranger=not is_authorized,
                    similarity_score=sim_score,
                    identity=identity,
                    security_mode=sec_mode,
                )
                transcript = audio_res.get("transcript", "")
                detected_lang = audio_res.get("lang")
                if audio_res.get("route") in ("handled", "error") and audio_res.get("text_response"):
                    precomputed_reply = audio_res["text_response"]

        if my_gen != self._command_generation or self.stop_speech_requested:
            logger.debug("[COMMAND ABORT] Utterance dropped after STT (superseded or stopped).")
            return

        if not transcript or not transcript.strip():
            logger.debug("[STT SILENCE] No audible speech detected in utterance.")
            transcript = "[Suono/Rumore ambientale]"
            await self.broadcast_ui({
                "type": "TRANSCRIPT",
                "user_text": transcript,
                "axl_text": "",
            })
        else:
            logger.debug(f">>> [USER HEARD]: \"{transcript}\" <<<")
            await self.process_text_command(
                transcript=transcript,
                identity=identity,
                is_authorized=is_authorized,
                sec_mode=sec_mode,
                sim_score=sim_score,
                lang=detected_lang,
                precomputed_reply=precomputed_reply,
            )

    async def process_text_command(
        self,
        transcript: str,
        identity: str = "owner",
        is_authorized: bool = True,
        sec_mode: Optional[str] = None,
        sim_score: float = 1.0,
        lang: Optional[str] = None,
        precomputed_reply: Optional[str] = None,
    ) -> str:
        """
        Executes a user command string through the complete two-tiered cognitive pipeline:
        1. Tier-1 Local Fast Path (Timer, Alarm, Clock, Date, Mute, Telemetry, Security Mode) -> <5ms offline
        2. Tier-2 Cognitive Brain (Cloud Gemini via Text-Only Ingestion + Tool AFC)
        3. Synchronous Guardrails & UI Broadcast
        4. Vocal delivery via TTS in the exact language of the command (Italian / English)
        """
        if not transcript or not transcript.strip():
            return ""

        my_gen = self._command_generation

        if lang is None:
            lang = detect_language(transcript)

        if sec_mode is None:
            sec_mode = self.verifier.security_mode if self.verifier else "friendly"

        is_stranger = not is_authorized
        vocal_reply = ""

        # Set biometric / admin authorization context for tool invocation (AFC)
        set_command_auth_context(
            is_authorized=is_authorized,
            identity=identity,
            security_mode=sec_mode,
            similarity_score=sim_score,
        )

        # Broadcast caller identity & authorization status to HUD dashboard
        await self.broadcast_ui({
            "type": "AUTH_EVENT",
            "identity": identity,
            "score": float(sim_score),
            "authorized": bool(is_authorized),
        })

        fast_path_result = None
        if precomputed_reply is not None:
            vocal_reply = ensure_dog_sound(precomputed_reply)
        else:
            # 1. Tier-1 Local Fast Path Router (Timer, Sveglia, Ora, Data, Muto, Telemetria, Sicurezza)
            fast_path_result = handle_local_fast_path(
                transcript,
                is_authorized=is_authorized,
                identity=identity,
                security_mode=sec_mode,
                similarity_score=sim_score,
                lang=lang,
            )

            if fast_path_result is not None:
                vocal_reply = fast_path_result.get("reply", "")
                logger.info(f"[LOCAL FAST PATH] Executed: {fast_path_result.get('type')} ({lang.upper()}) -> '{vocal_reply}'")

        if precomputed_reply is None and fast_path_result is None and self.brain:
            if self.stop_speech_requested:
                logger.info("[COMMAND ABORT] Processing aborted before brain query by user STOP.")
                return ""

            # 2. Tier-2 Cognitive Brain (Cloud Gemini via Text-Only Ingestion + Tool AFC)
            self._active_brain_task = brain_task = asyncio.create_task(
                self.brain.process_text_query(
                    user_text=transcript,
                    is_stranger=is_stranger,
                    similarity_score=sim_score,
                    identity=identity,
                    security_mode=sec_mode,
                    lang=lang,
                )
            )

            # Wait for Gemini response, emitting filler feedback if processing exceeds timeout (default 2.0s)
            thinking_timeout = float(os.getenv("AXL_THINKING_TIMEOUT", "2.0"))
            done, _ = await asyncio.wait([brain_task], timeout=thinking_timeout)

            if not done and not self.stop_speech_requested and my_gen == self._command_generation:
                # Processing exceeds timeout: emit witty canine thinking phrase in subtitles & voice
                thinking_phrase = get_random_thinking_phrase(lang=lang)
                logger.info(
                    f"[THINKING FEEDBACK] Query processing exceeded {thinking_timeout}s. Emitting filler ({lang.upper()}): '{thinking_phrase}'"
                )

                # Broadcast subtitle update to Visor HUD
                await self.broadcast_ui({
                    "type": "TRANSCRIPT",
                    "user_text": transcript,
                    "axl_text": thinking_phrase,
                })

                # Speak the thinking phrase aloud if active client is listening
                if self.tts and self.active_client and not self.stop_speech_requested and my_gen == self._command_generation:
                    await self.speak_text(thinking_phrase, lang=lang)

                # If brain is still working after thinking phrase was spoken, maintain THINKING state on Visor
                if not self.stop_speech_requested and my_gen == self._command_generation and not brain_task.done():
                    await self.broadcast_ui({
                        "type": "STATE_CHANGE",
                        "state": "THINKING",
                        "label": "ELABORAZIONE COGNITIVA...",
                    })

            try:
                result = await brain_task
            except asyncio.CancelledError:
                logger.info("[COGNITIVE CORE] Brain query cancelled.")
                return ""
            finally:
                if self._active_brain_task is brain_task:
                    self._active_brain_task = None

            if self.stop_speech_requested or my_gen != self._command_generation:
                logger.info("[COMMAND ABORT] Stale/aborted brain result dropped.")
                return ""

            vocal_reply = ensure_dog_sound(result.get("text_response", ""))

        logger.info(f">>> [A-X-L SPEAKS ({lang.upper()})]: \"{vocal_reply}\" <<<")

        # 3. Synchronous Intent Guardrails
        can_mute = is_authorized or (sec_mode == "friendly")

        # 3a. Guardrail Unmute / Smuta
        unmute_triggered = is_unmute_intent(transcript)
        if self.is_muted and unmute_triggered:
            if can_mute:
                logger.info(f"[COMMAND GUARDRAIL] Disengaging mute mode (transcript: '{transcript}').")
                await self.handle_telemetry_event({"type": "SET_MUTE", "muted": False})
            else:
                logger.warning(
                    f"[COMMAND GUARDRAIL] Blocked unauthorized unmute attempt from speaker '{identity}' in '{sec_mode}' mode."
                )

        # 3b. Guardrail Muto
        mute_triggered = is_mute_intent(transcript) or is_mute_in_reply(vocal_reply)
        if not self.is_muted and mute_triggered:
            if can_mute:
                logger.info(f"[COMMAND GUARDRAIL] Engaging mute mode (transcript: '{transcript}' | reply: '{vocal_reply[:60]}...').")
                await self.handle_telemetry_event({"type": "SET_MUTE", "muted": True})
            else:
                logger.warning(
                    f"[COMMAND GUARDRAIL] Blocked unauthorized mute attempt from speaker '{identity}' in '{sec_mode}' mode."
                )

        req_mode = detect_security_mode_intent(transcript) or detect_security_mode_in_reply(vocal_reply)
        if req_mode and self.verifier and req_mode != self.verifier.security_mode:
            can_switch = False
            if sec_mode == "friendly":
                can_switch = True
            elif sec_mode == "restrict":
                can_switch = is_authorized
            elif sec_mode == "paranoid":
                can_switch = is_authorized and (identity.lower() in ["owner", "admin_terminal"])

            if can_switch:
                logger.info(f"[COMMAND GUARDRAIL] Switching security mode to '{req_mode}' (speaker: {identity}).")
                await self.handle_telemetry_event({"type": "SECURITY_MODE", "mode": req_mode})
            else:
                logger.warning(
                    f"[COMMAND GUARDRAIL] Blocked unauthorized security mode switch to '{req_mode}' "
                    f"from speaker '{identity}' (score: {sim_score:.3f}, mode: '{sec_mode}')."
                )

        telem_intent = detect_telemetry_intent(transcript)
        if telem_intent is not None:
            req_show = telem_intent.get("show", True)
            req_tgt = telem_intent.get("target", self.telemetry_target)
            if req_show != self.telemetry_active or (req_show and req_tgt != self.telemetry_target):
                logger.info(f"[COMMAND GUARDRAIL] Toggling telemetry HUD: show={req_show}, target={req_tgt}")
                await self.set_telemetry_mode(req_show, target=req_tgt)

        enroll_intent = detect_enrollment_intent(transcript)
        if enroll_intent is not None and not self.is_enrolling:
            target_profile = enroll_intent.get("profile_name", "owner")
            can_enroll = False
            if target_profile == "owner":
                if sec_mode in ["restrict", "paranoid"]:
                    can_enroll = is_authorized and (identity.lower() in ["owner", "admin_terminal"])
                else:
                    can_enroll = is_authorized and (identity.lower() in ["owner", "admin_terminal"] or "owner" not in self.verifier.enrolled_profiles)
            else:
                if sec_mode in ["restrict", "paranoid"]:
                    can_enroll = is_authorized and (identity.lower() in ["owner", "admin_terminal"])
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

        if self.stop_speech_requested:
            logger.info("[COMMAND ABORT] Dialogue delivery skipped due to user STOP.")
            return ""

        # 4. Broadcast dialogue subtitles to connected HUD visors
        await self.broadcast_ui({
            "type": "TRANSCRIPT",
            "user_text": transcript,
            "axl_text": vocal_reply,
        })

        # 5. Neural Text-To-Speech Synthesis & Streaming via WebSocket
        if vocal_reply and self.tts and self.active_client and not self.stop_speech_requested:
            await self.speak_text(vocal_reply, lang=lang)

        return vocal_reply

    async def speak_text(self, text: str, lang: Optional[str] = None) -> bool:
        """
        Synthesizes and streams a vocal response to the active client.
        Crucially maintains is_speaking = True and UI state = SPEAKING for the full
        duration of the actual audio playback so the STOP button remains active.
        """
        if not text or not self.tts or not self.active_client or self.stop_speech_requested:
            return False
        self.is_speaking = True
        await self.broadcast_ui({
            "type": "STATE_CHANGE",
            "state": "SPEAKING",
            "label": "VOCAL TRANSMISSION // STREAMING PCM",
        })
        try:
            chunk_size = 1280  # 40ms @ 16kHz 16-bit Mono
            playback_start_time: Optional[float] = None
            bytes_sent = 0

            async for chunk in self.tts.synthesize_stream(text, chunk_size=chunk_size, lang=lang):
                if self.stop_speech_requested:
                    logger.info("[TTS STREAM] Speech transmission interrupted by user STOP.")
                    return False
                if not chunk:
                    continue
                if playback_start_time is None:
                    playback_start_time = time.monotonic()

                if self.active_client:
                    await self.active_client.send_bytes(chunk)
                bytes_sent += len(chunk)

                # Pace transmission so client buffer neither starves nor overflows
                elapsed = time.monotonic() - playback_start_time
                sent_audio_time = bytes_sent / 32000.0
                lead_time = sent_audio_time - elapsed
                if lead_time > 0.35:
                    await asyncio.sleep(lead_time - 0.20)

            if bytes_sent == 0 or playback_start_time is None:
                return False

            total_duration_sec = bytes_sent / 32000.0

            # Audio chunks have been completely transmitted over the network.
            # Keep is_speaking = True until the physical speaker finishes playback!
            while not self.stop_speech_requested:
                elapsed = time.monotonic() - playback_start_time
                remaining = total_duration_sec - elapsed
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))

            if not self.stop_speech_requested:
                logger.info(
                    f"[TTS STREAM] Delivered [{(lang or 'auto').upper()}] response ({total_duration_sec:.2f}s, {bytes_sent} bytes)."
                )
            return not self.stop_speech_requested
        except Exception as exc:
            logger.error(f"[TTS STREAM ERROR] Failed sending audio: {exc}")
            return False
        finally:
            self.is_speaking = False
            if not self.stop_speech_requested:
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
                        f"{get_random_dog_sound()} Calibrazione completata, capo! Il profilo vocale di {profile_name} è registrato e attivo nel sistema."
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
    if stream_manager._server_heartbeat_task is None or stream_manager._server_heartbeat_task.done():
        stream_manager._server_heartbeat_task = asyncio.create_task(stream_manager._server_heartbeat_loop())

    # Launch Admin Console Terminal REPL (Minecraft-style slash commands)
    loop = asyncio.get_running_loop()
    console = ConsoleManager(stream_manager, loop)
    console.start()

    yield
    logger.info("Shutting down A-X-L Master Core...")
    console.stop()
    if stream_manager._server_heartbeat_task and not stream_manager._server_heartbeat_task.done():
        stream_manager._server_heartbeat_task.cancel()


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
                        stream_manager.cancel_active_commands("wakeword_trigger")
                        logger.info(
                            f">>> [WAKEWORD ACTIVATED] Model: {ev['model']} | Score: {ev['score']:.3f} <<<"
                        )
                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "LISTENING",
                            "score": float(ev["score"]),
                        })

                    elif ev_type == "COMMAND_COMPLETE":
                        stream_manager.cancel_active_commands("new_command")
                        num_samples = ev["total_samples"]
                        command_audio = stream_manager.audio_buffer.get_last_samples(num_samples)

                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "THINKING",
                            "label": "ELABORAZIONE COGNITIVA...",
                        })

                        # Delegate execution asynchronously: WebSocket loop remains unblocked
                        stream_manager._active_command_task = asyncio.create_task(
                            stream_manager.handle_completed_command(
                                command_audio, ev["trigger_reason"]
                            )
                        )

                    elif ev_type == "SILENCE_RESET":
                        silence_time = ev.get("silence_sec", 0.0)
                        reason = ev.get("reason", "unknown")
                        log_fn = logger.debug if reason == "silence_eos" else logger.info
                        log_fn(
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
    """WebSocket endpoint serving real-time HUD telemetry, state transitions, and Kiosk intents."""
    client_param = websocket.query_params.get("client", "").lower().strip()
    ua = websocket.headers.get("user-agent", "").lower()
    is_kiosk_service = (client_param == "kiosk_service") or ("axl-kiosk-service" in ua)
    is_kiosk = ("axl-kiosk" in ua) or (client_param == "kiosk") or is_kiosk_service
    await stream_manager.register_ui(websocket, is_kiosk=is_kiosk, is_kiosk_service=is_kiosk_service)
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
                elif mtype == "TOGGLE_MUSIC_PLAYER":
                    visible = bool(msg.get("visible", not stream_manager.music_player_visible))
                    pause = bool(msg.get("pause", False))
                    await stream_manager.set_music_player_mode(visible, pause_if_closing=pause)
                elif mtype == "KIOSK_TELEMETRY":
                    stream_manager.last_kiosk_telemetry = msg
                    spotify_controller.update_kiosk_audio_outputs(msg.get("audio_outputs"))
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

                    # Vocal confirmation for UI button click (consistent with voice & terminal commands)
                    if stream_manager.is_muted:
                        mute_reply = f"{get_random_dog_sound()} Ricevuto, vado in muto. Ricordati che puoi riattivarmi solo premendo il pulsante sul visore."
                        user_label = "[Pulsante Muto premuto]"
                    else:
                        mute_reply = f"{get_random_dog_sound()} Microfono riattivato. Sono di nuovo in ascolto."
                        user_label = "[Pulsante Muto rilasciato]"

                    await stream_manager.broadcast_ui({
                        "type": "TRANSCRIPT",
                        "user_text": user_label,
                        "axl_text": mute_reply,
                    })
                    if stream_manager.tts and stream_manager.active_client:
                        asyncio.create_task(stream_manager.speak_text(mute_reply))
                elif mtype == "STOP_SPEECH":
                    logger.info("[STOP BUTTON] User requested immediate vocal/command abort.")
                    stream_manager.stop_speech_requested = True
                    stream_manager.is_speaking = False
                    stream_manager.is_enrolling = False
                    if stream_manager._enrollment_task and not stream_manager._enrollment_task.done():
                        stream_manager._enrollment_task.cancel()
                    stream_manager.cancel_active_commands("stop_button")
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
                        "label": "STANDBY // PASSIVE SURVEILLANCE",
                        "sub": "ACOUSTIC ARRAY: ACTIVE (16kHz PCM)",
                    })
                elif mtype == "ASK_START":
                    stream_manager.stop_speech_requested = False
                    # Push-to-ask: skips the wake-word. Speaker verification still applies afterwards.
                    if stream_manager.is_speaking:
                        logger.info("[ASK BUTTON] Ignored (A-X-L is speaking).")
                    elif stream_manager.is_muted:
                        logger.warning("[ASK BUTTON] Ignored: microphone is muted.")
                    elif not stream_manager.active_client or not stream_manager.pipeline:
                        logger.warning("[ASK BUTTON] Ignored: no audio source connected.")
                    elif stream_manager.pipeline.force_listen():
                        stream_manager.cancel_active_commands("ask_button")
                        logger.info("[ASK BUTTON] Listening started without wake-word.")
                        await stream_manager.broadcast_ui({
                            "type": "STATE_CHANGE",
                            "state": "LISTENING",
                        })
                    else:
                        logger.info("[ASK BUTTON] Ignored: pipeline busy.")
                elif mtype == "MUSIC_COMMAND":
                    action = str(msg.get("action", "")).lower().strip()
                    if action == "refresh":
                        await stream_manager.push_music_snapshot(force=True)
                    elif spotify_controller.is_configured() and spotify_controller.is_authenticated():
                        if action == "toggle":
                            is_playing = bool((stream_manager.last_music_state or {}).get("playing"))
                            action = "pause" if is_playing else "play"
                        target_dev = str(msg.get("target", "") or "").strip()
                        vol_val = msg.get("volume")
                        rep_mode = str(msg.get("repeat_mode", "") or "").strip()
                        pos_ms = msg.get("position_ms")
                        try:
                            res = await asyncio.to_thread(
                                spotify_controller.control_playback,
                                action,
                                target_dev or None,
                                int(vol_val) if vol_val is not None else None,
                                rep_mode or None,
                                int(pos_ms) if pos_ms is not None else None,
                            )
                            logger.info(f"[UI MUSIC CMD] action='{action}' ok={res.get('ok')} -> {res.get('message')}")
                        except Exception as exc:
                            logger.warning(f"[UI MUSIC CMD] Failed '{action}': {exc}")
                        await stream_manager.push_music_snapshot(delay=0.25, force=True)
                elif mtype in ("REQUEST_SERVER_TELEMETRY", "REQUEST_TELEMETRY"):
                    req_target = msg.get("target", "server" if mtype == "REQUEST_SERVER_TELEMETRY" else stream_manager.telemetry_target)
                    req_norm = str(req_target).lower().strip()
                    if req_norm in ("server", "master"):
                        server_data = stream_manager._collect_local_telemetry()
                        await websocket.send_json({
                            "type": "SERVER_TELEMETRY",
                            "data": server_data,
                        })
                        if stream_manager.telemetry_active and stream_manager.telemetry_target in ("server", "master"):
                            await websocket.send_json({
                                "type": "TELEMETRY_UPDATE",
                                "target": "server",
                                "data": server_data,
                            })
                    elif req_norm == "kiosk":
                        if stream_manager.last_kiosk_telemetry:
                            await websocket.send_json({
                                "type": "TELEMETRY_UPDATE",
                                "target": "kiosk",
                                "data": stream_manager.last_kiosk_telemetry,
                            })
                        else:
                            await websocket.send_json({
                                "type": "TELEMETRY_UPDATE",
                                "target": "kiosk",
                                "offline": True,
                                "error": "NO_KIOSK_FEED",
                                "data": None,
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
                                        "offline": True,
                                        "error": f"HTTP {resp.status_code}",
                                        "data": None,
                                    })
                        except Exception as e:
                            await websocket.send_json({
                                "type": "TELEMETRY_UPDATE",
                                "target": req_target,
                                "offline": True,
                                "error": str(e),
                                "data": None,
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


@app.get("/spotify/login")
async def spotify_login():
    """Redirects the user to Spotify OAuth2 authorization to link Spotify Connect."""
    if not spotify_controller.is_configured():
        return HTMLResponse(
            status_code=400,
            content=(
                "<html><body style='background:#0b1016;color:#d6f5ff;font-family:monospace;padding:32px;'>"
                "<h2>A-X-L // Spotify Connect Setup Required</h2>"
                "<p>Set <code>SPOTIFY_CLIENT_ID</code> and <code>SPOTIFY_CLIENT_SECRET</code> in "
                "<code>master_server/.env</code> first, then restart the server and reopen "
                "<code>http://127.0.0.1:8000/spotify/login</code>.</p>"
                f"<p>Expected Redirect URI in Spotify Developer Dashboard: <code>{spotify_controller.redirect_uri}</code></p>"
                "</body></html>"
            ),
        )
    return RedirectResponse(url=spotify_controller.get_authorize_url())


@app.get("/spotify/callback")
async def spotify_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handles Spotify OAuth2 callback, exchanges code for tokens, and stores refresh token locally."""
    if error:
        return HTMLResponse(
            status_code=400,
            content=(
                "<html><body style='background:#0b1016;color:#ff6b81;font-family:monospace;padding:32px;'>"
                f"<h2>A-X-L // Spotify Authorization Error</h2><p>{error}</p></body></html>"
            ),
        )
    if not code or not spotify_controller.validate_oauth_state(state):
        return HTMLResponse(
            status_code=400,
            content=(
                "<html><body style='background:#0b1016;color:#ff6b81;font-family:monospace;padding:32px;'>"
                "<h2>A-X-L // Invalid or expired OAuth state</h2>"
                "<p>Please retry from <a href='/spotify/login' style='color:#00e5ff;'>/spotify/login</a>.</p>"
                "</body></html>"
            ),
        )
    try:
        await asyncio.to_thread(spotify_controller.exchange_code, code)
        return HTMLResponse(
            content=(
                "<html><body style='background:#0b1016;color:#00e5ff;font-family:monospace;padding:32px;'>"
                "<h2>A-X-L // Spotify Connect Linked Successfully</h2>"
                "<p>Your refresh token has been stored locally in <code>master_server/data/spotify_token.json</code>.</p>"
                "<p>You can close this tab and ask A-X-L to play music on your Sonos or Wi-Fi/Bluetooth speakers.</p>"
                "</body></html>"
            ),
        )
    except Exception as exc:
        logger.error(f"Spotify callback token exchange failed: {exc}")
        return HTMLResponse(
            status_code=500,
            content=(
                "<html><body style='background:#0b1016;color:#ff6b81;font-family:monospace;padding:32px;'>"
                f"<h2>A-X-L // Spotify Token Exchange Failed</h2><p>{exc}</p></body></html>"
            ),
        )


@app.get("/spotify/status")
async def spotify_status():
    """Returns Spotify Connect authentication status and discovered speakers."""
    info = await asyncio.to_thread(spotify_controller.list_available_speakers)
    return {
        "configured": spotify_controller.is_configured(),
        "authenticated": spotify_controller.is_authenticated(),
        "redirect_uri": spotify_controller.redirect_uri,
        "spotify_devices": info.get("spotify_devices", []),
        "sonos_lan": info.get("sonos_lan", []),
        "kiosk_audio_outputs": info.get("kiosk_audio_outputs", []),
        "spotify_error": info.get("spotify_error"),
    }


@app.get("/")
async def root():
    """Redirects base URL to the cybernetic Visor Web UI."""
    return RedirectResponse(url="/ui/")


def _is_port_available(port: int) -> bool:
    """Checks if TCP port on 0.0.0.0 can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return False
            raise


def _ensure_port_free(port: int = 8000) -> None:
    """Releases a stale process holding the server port before starting uvicorn."""
    if _is_port_available(port):
        return

    logger.warning(f"[STARTUP] Port {port} is already in use. Terminating stale process...")
    fuser_bin = shutil.which("fuser")
    if fuser_bin:
        subprocess.run([fuser_bin, "-k", f"{port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        current_pid = os.getpid()
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid and conn.pid != current_pid:
                try:
                    psutil.Process(conn.pid).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

    for _ in range(10):
        if _is_port_available(port):
            logger.info(f"[STARTUP] Port {port} released successfully.")
            return
        time.sleep(0.15)


if __name__ == "__main__":
    _ensure_port_free(8000)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
        timeout_graceful_shutdown=3,
    )