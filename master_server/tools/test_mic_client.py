#!/usr/bin/env python3
"""
A-X-L Hardware-in-the-Loop Audio Mock Client.
Full-duplex audio peripheral:
- Captures low-latency 16kHz PCM16 Mono audio from microphone and streams to Master.
- Receives neural TTS audio stream from Master and plays it through local speakers.
"""

import argparse
import asyncio
import math
import sys
from typing import Optional

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
    clamped = max(-60.0, min(0.0, db))
    filled = int(((clamped + 60.0) / 60.0) * width)
    bar = "=" * filled + " " * (width - filled)
    return f"[{bar}] {db:5.1f} dBFS"


def play_audio_pcm(pcm_bytes: bytes, device_index: Optional[int] = None) -> None:
    """
    Plays received 16kHz PCM16 Mono audio through local output device,
    resampling cleanly to the device's native rate if required (e.g. 48kHz).
    """
    if not pcm_bytes:
        return

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    target_sr = 16000
    try:
        dev_idx = device_index if device_index is not None else sd.default.device[1]
        dev_info = sd.query_devices(dev_idx)
        target_sr = int(dev_info["default_samplerate"])
    except Exception:
        target_sr = 48000

    if target_sr == 48000:
        samples = np.repeat(samples, 3)
    elif target_sr != 16000:
        ratio = target_sr / 16000.0
        new_len = int(len(samples) * ratio)
        samples = np.interp(
            np.linspace(0, len(samples), new_len, endpoint=False),
            np.arange(len(samples)),
            samples,
        )

    try:
        sd.play(samples, samplerate=target_sr, device=device_index)
        sd.wait()
    except Exception as exc:
        sys.stderr.write(f"\n[AUDIO PLAYBACK ERROR] {exc}\n")


async def audio_client(
    ws_url: str,
    input_device: Optional[int] = None,
    output_device: Optional[int] = None,
) -> None:
    """Main bidirectional streaming engine."""
    loop = asyncio.get_running_loop()
    mic_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

    def audio_callback(indata, frames, time_info, status):
        """Hardware callback from PortAudio execution thread."""
        if status:
            sys.stderr.write(f"\n[ALSA/PortAudio Warning] {status}\n")
        loop.call_soon_threadsafe(mic_queue.put_nowait, bytes(indata))

    print(f"Connecting to A-X-L Master at: {ws_url}")
    try:
        async with websockets.connect(
            ws_url,
            max_size=None,
            ping_interval=10,
            ping_timeout=5,
        ) as websocket:
            print("[CONNECTED] Link established. Starting full-duplex audio stream...")
            print(f"Format: {SAMPLE_RATE}Hz, Mono, 16-bit PCM | Frame: {EXPECTED_FRAME_BYTES} bytes (20ms)\n")

            # Initialize mic input stream
            mic_stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                device=input_device,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=audio_callback,
            )

            async def send_worker():
                frames_sent = 0
                while True:
                    pcm_frame = await mic_queue.get()
                    await websocket.send(pcm_frame)
                    frames_sent += 1

                    if frames_sent % 10 == 0:
                        db = calculate_dbfs(pcm_frame)
                        meter = render_vu_meter(db)
                        sys.stdout.write(f"\rStreaming active | Frames: {frames_sent:06d} | Mic: {meter}")
                        sys.stdout.flush()

            async def receive_worker():
                while True:
                    first_msg = await websocket.recv()
                    if isinstance(first_msg, str):
                        if first_msg == "STOP":
                            sd.stop()
                            sys.stdout.write("\n>>> [A-X-L SILENCED] Playback aborted via STOP request <<<\n")
                            sys.stdout.flush()
                        continue

                    if isinstance(first_msg, bytes):
                        # Gather all consecutive chunks of the incoming vocal utterance
                        chunks = [first_msg]
                        aborted = False
                        while True:
                            try:
                                next_msg = await asyncio.wait_for(websocket.recv(), timeout=0.40)
                                if isinstance(next_msg, str):
                                    if next_msg == "STOP":
                                        sd.stop()
                                        aborted = True
                                        sys.stdout.write("\n>>> [A-X-L SILENCED] Playback aborted via STOP request <<<\n")
                                        sys.stdout.flush()
                                        break
                                elif isinstance(next_msg, bytes):
                                    chunks.append(next_msg)
                            except asyncio.TimeoutError:
                                break

                        if aborted or not chunks:
                            continue

                        full_speech = b"".join(chunks)
                        duration_sec = len(full_speech) / 32000.0
                        sys.stdout.write(
                            f"\n>>> [A-X-L SPEAKING] Playing vocal response ({duration_sec:.2f}s) <<<\n"
                        )
                        sys.stdout.flush()

                        await asyncio.to_thread(play_audio_pcm, full_speech, output_device)

            with mic_stream:
                await asyncio.gather(send_worker(), receive_worker())

    except ConnectionRefusedError:
        print(f"\n[ERROR] Connection refused. Is master_server running at {ws_url}?")
    except websockets.exceptions.ConnectionClosed as exc:
        print(f"\n[DISCONNECTED] Server closed connection: {exc.code} - {exc.reason}")
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"\n[ERROR] Audio loop crashed: {exc}")


def list_devices() -> None:
    """Utility to list available audio input and output devices."""
    print("Available Audio Devices:")
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        in_ch = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        sr = int(dev["default_samplerate"])
        print(f"  [{idx}] {dev['name']} (Inputs: {in_ch}, Outputs: {out_ch}, Default SR: {sr}Hz)")


def main() -> None:
    parser = argparse.ArgumentParser(description="A-X-L Micro-Client Testbench (Full-Duplex)")
    parser.add_argument("--host", type=str, default="localhost", help="Master server hostname or IP")
    parser.add_argument("--port", type=int, default=8000, help="Master server WebSocket port")
    parser.add_argument("--device", type=int, default=None, help="Audio input (microphone) device index")
    parser.add_argument("--output-device", type=int, default=None, help="Audio output (speaker) device index")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    ws_url = f"ws://{args.host}:{args.port}/ws/audio"
    try:
        asyncio.run(audio_client(ws_url, args.device, args.output_device))
    except KeyboardInterrupt:
        print("\n[STOP] Capture terminated cleanly by user.")


if __name__ == "__main__":
    main()
