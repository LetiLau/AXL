import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
import uvicorn

from audio_pipeline import AudioPipeline
from ring_buffer import AudioRingBuffer
from speaker_verifier import SpeakerVerifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("AXL-Master")

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"


class StreamManager:
    """Manages audio streaming sessions, pipeline dispatching and speaker verification."""

    def __init__(self):
        self.audio_buffer = AudioRingBuffer(capacity_seconds=10.0, sample_rate=16000)
        self.pipeline: Optional[AudioPipeline] = None
        self.verifier: Optional[SpeakerVerifier] = None
        self.active_client: Optional[WebSocket] = None
        self._lock = asyncio.Lock()

    def initialize_pipeline(self):
        logger.info("Initializing neural DSP, wake-word, and speaker verification...")
        self.pipeline = AudioPipeline(
            models_dir=MODELS_DIR,
            vad_threshold=0.5,
            wakeword_threshold=0.35,
            silence_hangover_sec=0.7,
        )
        self.verifier = SpeakerVerifier(models_dir=MODELS_DIR, threshold=0.65)
        logger.info("A-X-L Acoustic Front-End & Biometrics Ready.")

    async def connect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if self.active_client is not None:
                return False
            await websocket.accept()
            self.active_client = websocket
            self.audio_buffer.clear()
            if self.pipeline:
                self.pipeline.reset()
            return True

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if self.active_client == websocket:
                self.active_client = None
                if self.pipeline:
                    self.pipeline.reset()
                logger.info("Active audio source detached.")


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

    frames_received = 0

    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) % 2 != 0:
                continue

            stream_manager.audio_buffer.write(data)
            frames_received += 1

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
                        duration_sec = len(command_audio) / 16000.0

                        logger.info(
                            f"[COMMAND CAPTURED] Utterance ready: {len(command_audio)} samples ({duration_sec:.2f}s)"
                        )

                        # Biometric Speaker Verification (CAM++)
                        if stream_manager.verifier:
                            is_owner, score, identity = stream_manager.verifier.verify(command_audio)
                            if is_owner:
                                logger.info(
                                    f">>> [AUTH SUCCESS] Identity: OWNER | Cosine Sim: {score:.3f} (>= {stream_manager.verifier.threshold}) <<<"
                                )
                            else:
                                logger.warning(
                                    f">>> [SECURITY ALERT] Identity: STRANGER | Cosine Sim: {score:.3f} (< {stream_manager.verifier.threshold}) <<<"
                                )

                    elif ev_type == "SILENCE_RESET":
                        silence_time = ev.get("silence_sec", 0.0)
                        logger.info(
                            f"[PIPELINE] Silence detected ({silence_time:.2f}s) --> State reset to IDLE."
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