#!/usr/bin/env python3
"""
A-X-L Hardware-in-the-Loop Audio Mock Client.
Captures low-latency 16kHz PCM16 Mono audio and streams it to the Master Server.
"""

import argparse
import asyncio
import math
import sys
import numpy as np
import sounddevice as sd
import websockets

# PCM Stream parameters
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = 320  # 320 samples = 20ms @ 16kHz -> 640 bytes
EXPECTED_FRAME_BYTES = BLOCK_SIZE * 2


def calculate_dbfs(pcm_chunk: bytes) -> float:
    """Calculates RMS level in decibels relative to full scale (dBFS)."""
    samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32)
    rms = np.sqrt(np.mean(samples**2))
    if rms <= 1e-5:
        return -96.0
    db = 20.0 * math.log10(rms / 32767.0)
    return max(-96.0, db)


def render_vu_meter(db: float, width: int = 20) -> str:
    """Renders a simple ASCII peak/RMS bar."""
    # Scale from [-60 dBFS, 0 dBFS] to [0, width]
    clamped = max(-60.0, min(0.0, db))
    filled = int(((clamped + 60.0) / 60.0) * width)
    bar = "=" * filled + " " * (width - filled)
    return f"[{bar}] {db:5.1f} dBFS"


async def audio_sender(ws_url: str, device_index: int | None = None) -> None:
    """Main async streaming engine."""
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

    def audio_callback(indata, frames, time_info, status):
        """Hardware callback from PortAudio execution thread."""
        if status:
            sys.stderr.write(f"\n[ALSA/PortAudio Warning] {status}\n")
        # Direct zero-copy slice to loop thread-safe enqueue
        loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    print(f"Connecting to A-X-L Master at: {ws_url}")
    try:
        async with websockets.connect(
            ws_url,
            max_size=None,
            ping_interval=10,
            ping_timeout=5,
        ) as websocket:
            print("[CONNECTED] Link established. Starting raw audio capture...")
            print(f"Format: {SAMPLE_RATE}Hz, Mono, 16-bit PCM | Frame Size: {EXPECTED_FRAME_BYTES} bytes (20ms)\n")

            # Initialize input stream
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                device=device_index,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=audio_callback,
            )

            with stream:
                frames_sent = 0
                while True:
                    # Non-blocking dequeue
                    pcm_frame = await audio_queue.get()
                    await websocket.send(pcm_frame)
                    frames_sent += 1

                    # Telemetry & VU-meter every 10 frames (200ms)
                    if frames_sent % 10 == 0:
                        db = calculate_dbfs(pcm_frame)
                        meter = render_vu_meter(db)
                        sys.stdout.write(f"\rStreaming active | Frames: {frames_sent:06d} | Mic: {meter}")
                        sys.stdout.flush()

    except ConnectionRefusedError:
        print(f"\n[ERROR] Connection refused. Is master_server running at {ws_url}?")
    except websockets.exceptions.ConnectionClosed as exc:
        print(f"\n[DISCONNECTED] Server closed connection: {exc.code} - {exc.reason}")
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"\n[ERROR] Streaming loop crashed: {exc}")


def list_microphones() -> None:
    """Utility to list available audio input devices."""
    print("Available Audio Input Devices:")
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            print(f"  [{idx}] {dev['name']} (Inputs: {dev['max_input_channels']}, Default SR: {int(dev['default_samplerate'])}Hz)")


def main() -> None:
    parser = argparse.ArgumentParser(description="A-X-L Micro-Client Testbench")
    parser.add_argument("--host", type=str, default="localhost", help="Master server hostname or IP")
    parser.add_argument("--port", type=int, default=8000, help="Master server WebSocket port")
    parser.add_argument("--device", type=int, default=None, help="Audio input device index")
    parser.add_argument("--list-devices", action="store_true", help="List audio input devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_microphones()
        sys.exit(0)

    ws_url = f"ws://{args.host}:{args.port}/ws/audio"
    try:
        asyncio.run(audio_sender(ws_url, args.device))
    except KeyboardInterrupt:
        print("\n[STOP] Capture terminated cleanly by user.")


if __name__ == "__main__":
    main()