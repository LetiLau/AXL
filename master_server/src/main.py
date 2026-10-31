import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set

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
    detect_security_mode_in_reply,
    detect_security_mode_intent,
    detect_telemetry_intent,
    is_mute_in_reply,
    is_mute_intent,
    register_telemetry_listener,
)

THRESHOLD_VAR = 0.48 #got the value running tools/calibrate_speaker.py

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

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"


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
        self.is_speaking: bool = False
        self.is_muted: bool = False
        self.ask_while_muted: bool = False
        self.telemetry_active: bool = False
        self.telemetry_target: str = "fedora"
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

        enabled = payload.get("enabled", self.telemetry_active)
        raw_target = payload.get("target", self.telemetry_target)
        target = "server" if str(raw_target).lower().strip() in ("server", "master", "thinkpad", "locale", "local") else "slave"
        if enabled != self.telemetry_active or target != self.telemetry_target:
            await self.set_telemetry_mode(enabled, target=target)
        else:
            self.telemetry_target = target
            await self.broadcast_ui(payload)

    async def set_telemetry_mode(self, enabled: bool, target: str = "slave"):
        """Activates or deactivates live telemetry streaming mode for server or slave."""
        self.telemetry_active = enabled
        self.telemetry_target = "server" if str(target).lower().strip() in ("server", "master", "thinkpad", "locale", "local") else "slave"
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

    async def register_ui(self, websocket: WebSocket):
        """Registers a connected Web UI client and sends initial state synchronization."""
        await websocket.accept()
        self.ui_clients.add(websocket)
        logger.info(f"UI client attached. Active UI clients: {len(self.ui_clients)}")
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
        })

    async def unregister_ui(self, websocket: WebSocket):
        """Detaches a Web UI client cleanly."""
        self.ui_clients.discard(websocket)
        logger.info(f"UI client detached. Active UI clients: {len(self.ui_clients)}")

    async def broadcast_ui(self, message: dict):
        """Dispatches an asynchronous state event to all connected Web UI clients."""
        if not self.ui_clients:
            return
        dead_clients = set()
        for ws in self.ui_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead_clients.add(ws)
        if dead_clients:
            self.ui_clients -= dead_clients

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
            if not self.is_muted and mute_triggered:
                logger.info(f"[COMMAND GUARDRAIL] Engaging mute mode (transcript: '{transcript}' | reply: '{vocal_reply[:60]}...').")
                await self.handle_telemetry_event({"type": "SET_MUTE", "muted": True})

            # Guardrail 2: Security Mode Command (checks both user transcript and spoken confirmation)
            req_mode = detect_security_mode_intent(transcript) or detect_security_mode_in_reply(vocal_reply)
            if req_mode and self.verifier and req_mode != self.verifier.security_mode:
                can_switch = is_authorized or (sec_mode == "friendly")
                if can_switch:
                    logger.info(f"[COMMAND GUARDRAIL] Switching security mode to '{req_mode}' (speaker: {identity}).")
                    await self.handle_telemetry_event({"type": "SECURITY_MODE", "mode": req_mode})
                else:
                    logger.warning(
                        f"[COMMAND GUARDRAIL] Blocked unauthorized security mode switch to '{req_mode}' "
                        f"from speaker '{identity}' (score: {sim_score:.3f})."
                    )

            # Guardrail 3: Telemetry HUD Command
            telem_intent = detect_telemetry_intent(transcript)
            if telem_intent is not None:
                req_show = telem_intent.get("show", True)
                req_tgt = telem_intent.get("target", self.telemetry_target)
                if req_show != self.telemetry_active or (req_show and req_tgt != self.telemetry_target):
                    logger.info(f"[COMMAND GUARDRAIL] Toggling telemetry HUD: show={req_show}, target={req_tgt}")
                    await self.set_telemetry_mode(req_show, target=req_tgt)

            # Broadcast dialogue subtitles to connected HUD visors
            await self.broadcast_ui({
                "type": "TRANSCRIPT",
                "user_text": transcript,
                "axl_text": vocal_reply,
            })

            # 4. Neural Text-To-Speech Synthesis & Streaming via WebSocket
            if vocal_reply and self.tts and self.active_client:
                logger.info("[TTS STREAM] Synthesizing and streaming response to client...")
                self.is_speaking = True
                await self.broadcast_ui({
                    "type": "STATE_CHANGE",
                    "state": "SPEAKING",
                    "label": "VOCAL TRANSMISSION // STREAMING PCM",
                })
                try:
                    async for audio_chunk in self.tts.synthesize_stream(vocal_reply):
                        if self.active_client:
                            await self.active_client.send_bytes(audio_chunk)
                    logger.info("[TTS STREAM] Vocal response fully delivered.")
                except Exception as exc:
                    logger.error(f"[TTS STREAM ERROR] Failed sending audio: {exc}")
                finally:
                    # Small grace period (200ms) before re-enabling mic listening to absorb room decay
                    await asyncio.sleep(0.2)
                    self.is_speaking = False
                    await self.broadcast_ui({
                        "type": "STATE_CHANGE",
                        "state": "IDLE",
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

    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) % 2 != 0:
                continue

            # Inhibit audio ingestion while A-X-L is speaking or muted (except a one-shot ASK while muted)
            if stream_manager.is_speaking or (stream_manager.is_muted and not stream_manager.ask_while_muted):
                continue

            stream_manager.audio_buffer.write(data)

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
                        stream_manager.ask_while_muted = False
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
    await stream_manager.register_ui(websocket)
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
                elif mtype == "ASK_START":
                    # Push-to-ask: skips the wake-word. Speaker verification still applies afterwards.
                    # Allowed even while muted (one-shot question: the mute state is kept afterwards).
                    if stream_manager.is_speaking:
                        logger.info("[ASK BUTTON] Ignored (A-X-L is speaking).")
                    elif not stream_manager.active_client or not stream_manager.pipeline:
                        logger.warning("[ASK BUTTON] Ignored: no audio source connected.")
                    elif stream_manager.pipeline.force_listen():
                        stream_manager.ask_while_muted = stream_manager.is_muted
                        logger.info(
                            "[ASK BUTTON] Listening started without wake-word"
                            + (" (one-shot while muted)." if stream_manager.is_muted else ".")
                        )
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