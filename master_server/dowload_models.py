#!/usr/bin/env python3
"""
Model provisioning utility for A-X-L Master Server.
Downloads required ONNX checkpoints for VAD, Speaker Verification, Wake-Word, and TTS.
"""

import sys
import urllib.request
from pathlib import Path

# Paths configuration
BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"

# Target download artifacts (Active production models only)
MODEL_TARGETS = {
    # 1. Silero VAD (Voice Activity Detection)
    "silero_vad.onnx": (
        "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
    ),
    # 2. Speaker Verification (CAM++ DAMO Academy via sherpa-onnx)
    "speaker_id.onnx": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_CAM++_LM.onnx"
    ),
    # 3. English Piper TTS (Bryce - Jarvis Persona)
    "en_US-bryce-medium.onnx": (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/bryce/medium/en_US-bryce-medium.onnx"
    ),
    "en_US-bryce-medium.onnx.json": (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/bryce/medium/en_US-bryce-medium.onnx.json"
    ),
    # 4. Italian Local Offline Fallback Piper TTS (Miro - Tuned)
    "it_IT-miro-high.onnx": (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/miro/high/it_IT-miro-high.onnx"
    ),
    "it_IT-miro-high.onnx.json": (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/miro/high/it_IT-miro-high.onnx.json"
    ),
}


class DownloadProgressBar:
    """Console progress reporter for stream downloads."""

    def __init__(self, filename: str):
        self.filename = filename

    def __call__(self, block_num: int, block_size: int, total_size: int):
        downloaded = block_num * block_size
        if total_size > 0:
            percent = min(100.0, downloaded * 100 / total_size)
            downloaded_mb = downloaded / (1024 * 1024)
            total_mb = total_size / (1024 * 1024)
            progress_str = f"\r[{self.filename}] {percent:5.1f}% ({downloaded_mb:5.1f} MB / {total_mb:5.1f} MB)"
        else:
            progress_str = f"\r[{self.filename}] {downloaded / (1024 * 1024):5.1f} MB downloaded"

        sys.stdout.write(progress_str)
        sys.stdout.flush()


def download_file(url: str, destination: Path) -> None:
    """Download single binary asset with resume/skip validation."""
    if destination.exists() and destination.stat().st_size > 0:
        print(f"[SKIP] Asset already present: {destination.name}")
        return

    print(f"[FETCH] Starting download: {destination.name}")
    try:
        progress = DownloadProgressBar(destination.name)
        urllib.request.urlretrieve(url, destination, reporthook=progress)
        print(f"\n[OK] Successfully retrieved {destination.name}")
    except Exception as exc:
        print(f"\n[ERROR] Failed to download {destination.name}: {exc}")
        if destination.exists():
            destination.unlink()
        raise


def sync_openwakeword_models() -> None:
    """Synchronize openWakeWord standard models using framework loader."""
    print("\n[SYNC] Synchronizing openWakeWord models...")
    try:
        import openwakeword.utils

        openwakeword.utils.download_models(model_names=["hey_jarvis"])
        print("[OK] openWakeWord standard checkpoints verified.")
    except ImportError:
        print("[WARN] openwakeword package not available in current environment.")
    except Exception as exc:
        print(f"[ERROR] Failed to sync openwakeword models: {exc}")


def main() -> None:
    """Execution entrypoint."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print("=== A-X-L Offline Models Deployment ===")
    print("Target Directory: {MODELS_DIR}\n")

    for filename, url in MODEL_TARGETS.items():
        destination = MODELS_DIR / filename
        download_file(url, destination)

    sync_openwakeword_models()
    print("\n[COMPLETE] Phase 1 Model Provisioning finished successfully.")


if __name__ == "__main__":
    main()