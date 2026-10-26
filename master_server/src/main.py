import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
import numpy as np
import uvicorn

from audio_pipeline import AudioPipeline
from audio_utils import pcm_to_wav_bytes
from brain_gemini import GeminiBrain
from ring_buffer import AudioRingBuffer
from speaker_verifier import SpeakerVerifier
from tts_engine import PiperTTSEngine

THRESHOLD_VAR = 0.48 #got the value running tools/calibrate_speaker.py

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("AXL-Master")

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
        self.is_speaking: bool = False
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
        logger.info("A-X-L Full Pipeline (Acoustic + Biometrics + Brain + TTS) Ready.")

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

            # 4. Neural Text-To-Speech Synthesis & Streaming via WebSocket
            if vocal_reply and self.tts and self.active_client:
                logger.info("[TTS STREAM] Synthesizing and streaming response to client...")
                self.is_speaking = True
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

            # Inhibit audio ingestion while A-X-L is speaking to prevent acoustic feedback loop
            if stream_manager.is_speaking:
                continue

            stream_manager.audio_buffer.write(data)

            if stream_manager.pipeline:
                events = stream_manager.pipeline.ingest_frame(data)
                for ev in events:
                    ev_type = ev["event"]

                    if ev_type == "WAKEWORD_PROBE":
                        logger.info(f"[PROBE] Model: {ev['model']} | Score: {ev['score']:.3f}")

                    elif ev_type == "WAKEWORD_TRIGGER":
                        logger.info(
                            f"\n>>> [WAKEWORD ACTIVATED] Triggered! Model: {ev['model']} | Score: {ev['score']:.3f} <<<\n"
                        )

                    elif ev_type == "COMMAND_COMPLETE":
                        num_samples = ev["total_samples"]
                        command_audio = stream_manager.audio_buffer.get_last_samples(num_samples)

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

    except WebSocketDisconnect:
        logger.info(f"Client disconnected cleanly: {client_host}")
    except Exception as exc:
        logger.error(f"Streaming error: {exc}", exc_info=True)
    finally:
        await stream_manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=False, 
        workers=1
    )

    #NOT _WORK