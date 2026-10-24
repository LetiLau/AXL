import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
import uvicorn

from ring_buffer import AudioRingBuffer

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("AXL-Master")

# Audio stream contract
EXPECTED_FRAME_BYTES = 640  # 20ms of 16kHz 16-bit mono PCM


class StreamManager:
    """Manages active microphone input sessions."""

    def __init__(self):
        # 10 seconds rolling memory buffer
        self.audio_buffer = AudioRingBuffer(capacity_seconds=10.0, sample_rate=16000)
        self.active_client: Optional[WebSocket] = None
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if self.active_client is not None:
                # Reject concurrent stream: single source of truth architecture
                return False
            await websocket.accept()
            self.active_client = websocket
            self.audio_buffer.clear()
            return True

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if self.active_client == websocket:
                self.active_client = None
                logger.info("Active audio source detached.")


stream_manager = StreamManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle hook for hardware & inference engines."""
    logger.info("Initializing A-X-L Master Core...")
    logger.info("CPU target: x86_64 AVX2 optimizations enabled.")
    yield
    logger.info("Shutting down A-X-L Master Core...")


app = FastAPI(
    title="A-X-L System Master Server",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint for node discovery and probes."""
    return {
        "status": "online",
        "system": "A-X-L Core",
        "audio_stream_active": stream_manager.active_client is not None,
        "total_samples_buffered": stream_manager.audio_buffer.total_samples_written,
    }


@app.websocket("/ws/audio")
async def audio_websocket_endpoint(websocket: WebSocket):
    """
    Primary ingestion channel for raw PCM16 streaming from Thin Kiosk (or Mock Client).
    Receives raw byte frames, validates payload boundaries, and updates RingBuffer.
    """
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
            # Non-blocking async frame receipt
            data = await websocket.receive_bytes()
            data_len = len(data)

            # Integrity check: byte length must be multiple of 2 (int16 samples)
            if data_len % 2 != 0:
                logger.error(f"Malformed frame: odd byte size ({data_len}). Dropping frame.")
                continue

            if data_len != EXPECTED_FRAME_BYTES:
                # Non-fatal: allows variable sized chunks if client latency spikes
                pass

            # Write into continuous ring buffer
            stream_manager.audio_buffer.write(data)
            frames_received += 1

            # Telemetry heartbeat every 500 frames (~10 seconds of audio)
            if frames_received % 500 == 0:
                logger.info(
                    f"Streaming active: {frames_received} frames ingested "
                    f"({stream_manager.audio_buffer.total_samples_written} total samples)"
                )

    except WebSocketDisconnect:
        logger.info(f"Audio source disconnected cleanly: {client_host}")
    except Exception as exc:
        logger.error(f"Unexpected streaming pipeline error: {exc}", exc_info=True)
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