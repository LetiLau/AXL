#!/usr/bin/env python3
"""
A-X-L Biometric Voice Enrollment Tool.
Records reference audio samples, extracts CAM++ neural embedding,
and saves the owner profile to models/profiles/owner.npy.
"""

import sys
import time
from pathlib import Path
import numpy as np
import sounddevice as sd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from speaker_verifier import SpeakerVerifier

SAMPLE_RATE = 16000
DURATION_SEC = 5.0
MODELS_DIR = BASE_DIR / "models"
OUTPUT_DIR = MODELS_DIR / "profiles"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    owner_target = OUTPUT_DIR / "owner.npy"

    print("=== A-X-L BIOMETRIC VOICE ENROLLMENT ===")
    print(f"Sampling Rate: {SAMPLE_RATE} Hz | Duration: {DURATION_SEC}s")
    print(f"Target Output: {owner_target}\n")

    input("Press ENTER and start talking in a natural way (e.g. read and article or describe a command)...")

    for i in range(3, 0, -1):
        sys.stdout.write(f"\rStart recording {i}... ")
        sys.stdout.flush()
        time.sleep(1)

    print("\n[RECORDING IN PROGRESS] Speak Now...")
    audio = sd.rec(
        int(DURATION_SEC * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    print("[ENROLL COMPLETE]")

    samples = audio.flatten()

    # Dynamic range sanity check
    rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
    dbfs = 20.0 * np.log10(rms / 32767.0) if rms > 0 else -96.0
    print(f"Livello medio segnale: {dbfs:.2f} dBFS")

    if dbfs < -40.0:
        print("[ERROR] Low audio level. Increase the microphone volume and try again.")
        sys.exit(1)

    print("\nCAM++ extraction engine initialization...")
    verifier = SpeakerVerifier(models_dir=MODELS_DIR)

    print("Neural embedding computation...")
    embedding = verifier.extract_embedding(samples)

    if embedding is None:
        print("[ERROR] Unable to generate embedding. Audio does not conform.")
        sys.exit(1)

    # Save normalized vector
    np.save(owner_target, embedding)
    print(f"\n[SUCCESS] Registered Master Profile:")
    print(f" - File: {owner_target}")
    print(f" - Embedding size: {embedding.shape}")
    print(f" - L2-Norm Vector: {np.linalg.norm(embedding):.4f}")


if __name__ == "__main__":
    main()