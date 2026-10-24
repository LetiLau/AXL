import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
import uvicorn

from audio_pipeline import AudioPipeline
from ring_buffer import AudioRingBuffer

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("AXL-Master")

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"


class StreamManager:
    """Manages active microphone input sessions and real-time DSP stages."""

    def __init__(self):
        self.audio_buffer = AudioRingBuffer(capacity_seconds=10.0, sample_rate=16000)
        self.pipeline: Optional[AudioPipeline] = None
        self.active_client: Optional[WebSocket] = None
        self._lock = asyncio.Lock()

    def initialize_pipeline(self):
        """Loads ONNX inference models into memory."""
        logger.info("Initializing neural DSP pipeline...")
        self.pipeline = AudioPipeline(MODELS_DIR, vad_threshold=0.5)
        logger.info("Silero VAD v5 operational.")

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
    """Lifecycle hook for hardware & inference engines."""
    logger.info("Starting A-X-L Master Core...")
    stream_manager.initialize_pipeline()
    yield
    logger.info("Shutting down A-X-L Master Core...")


app = FastAPI(
    title="A-X-L System Master Server",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint exposing runtime pipeline telemetry."""
    is_speech = stream_manager.pipeline.is_speech_active if stream_manager.pipeline else False
    last_prob = stream_manager.pipeline.last_speech_prob if stream_manager.pipeline else 0.0
    return {
        "status": "online",
        "system": "A-X-L Core",
        "audio_stream_active": stream_manager.active_client is not None,
        "total_samples_buffered": stream_manager.audio_buffer.total_samples_written,
        "vad": {
            "is_speech": is_speech,
            "probability": round(last_prob, 3),
        },
    }


@app.websocket("/ws/audio")
async def audio_websocket_endpoint(websocket: WebSocket):
    connected = await stream_manager.connect(websocket)
    if not connected:
        logger.warning("Rejected duplicate streaming connection attempt.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info(f"Audio stream source connected from {client_host}")

    frames_received = 0

    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) % 2 != 0:
                continue

            stream_manager.audio_buffer.write(data)
            frames_received += 1

            if stream_manager.pipeline:
                vad_meta = stream_manager.pipeline.ingest_frame(data)
                if vad_meta:
                    prob = vad_meta["speech_prob"]

                    # 1. Trigger istantaneo al fronte di salita
                    if vad_meta["speech_started"]:
                        logger.info(f"[VAD EVENT] >>> SPEECH DETECTED (prob: {prob:.3f})")

                    # 2. Trigger istantaneo al fronte di discesa
                    elif vad_meta["speech_ended"]:
                        logger.info(f"[VAD EVENT] <<< SILENCE DETECTED (prob: {prob:.3f})")

                    # 3. Log vocale continuativo (ogni ~200ms mentre parli)
                    elif vad_meta["is_speech"] and frames_received % 10 == 0:
                        logger.info(f"[VAD ACTIVE] Voice ongoing | prob: {prob:.3f}")

            # Heartbeat esplicito a livello INFO ogni ~2 secondi (100 frames)
            if frames_received % 100 == 0 and stream_manager.pipeline:
                curr_prob = stream_manager.pipeline.last_speech_prob
                state_str = "SPEECH" if stream_manager.pipeline.is_speech_active else "IDLE"
                logger.info(f"[STREAM STATUS] Frames: {frames_received:06d} | State: {state_str} | VAD prob: {curr_prob:.4f}")

    except WebSocketDisconnect:
        logger.info(f"Audio source disconnected cleanly: {client_host}")
    except Exception as exc:
        logger.error(f"Streaming pipeline error: {exc}", exc_info=True)
    finally:
        await stream_manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
        log_level="info",
    )