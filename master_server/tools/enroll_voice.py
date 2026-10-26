#!/usr/bin/env python3
"""
A-X-L VAD-Gated Biometric Voice Enrollment Tool.
Uses Silero VAD to isolate pure human speech frames, eliminating silence contamination.
Extracts CAM++ neural centroid and saves reference profiles.
"""

import argparse
import sys
import time
import wave
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np
import sounddevice as sd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from audio_pipeline import SileroVAD
from speaker_verifier import SpeakerVerifier

SAMPLE_RATE = 16000
VAD_FRAME_SAMPLES = 512  # 32ms window required by Silero V5
TARGET_SPEECH_SECONDS = 3.0  # Accumulate exactly 3s of active speech per take
MODELS_DIR = BASE_DIR / "models"
OUTPUT_DIR = MODELS_DIR / "profiles"

PROMPTS: Dict[str, List[str]] = {
    "it": [
        "A-X-L controlla la memoria di sistema e monitora i processi attivi sulla workstation.",
        "A-X-L apri l'ambiente di sviluppo Fedora e sincronizza la repository del progetto.",
        "Interfaccia diagnostica pronta, inizializza i nodi operativi sulla rete locale.",
    ],
    "en": [
        "Hey Jarvis, check system telemetry and monitor active background processes.",
        "Hey Jarvis, initialize development environment and sync local repository.",
        "Diagnostic interface online, verify peripheral execution nodes on local network.",
    ],
    "both": [
        "A-X-L verifica lo stato dei sistemi e controlla la memoria della workstation.",
        "Hey Jarvis, initialize development workspace and monitor cpu telemetry.",
        "A-X-L apri il terminale di sviluppo e prepara i comandi di rete.",
    ],
}


def capture_pure_speech(vad: SileroVAD, prompt_text: str, target_sec: float = 3.0, device_idx=None) -> np.ndarray:
    """Streams audio through Silero VAD until target_sec of pure speech is collected."""
    print(f"\nPrompt da pronunciare:\n-> \"{prompt_text}\"")
    input("\nPremi INVIO e inizia a parlare...")

    accumulated_speech: List[np.ndarray] = []
    target_samples = int(target_sec * SAMPLE_RATE)
    collected_samples = 0

    vad.reset_state()
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=VAD_FRAME_SAMPLES,
        channels=1,
        dtype="int16",
        device=device_idx,
    )

    print("[ASCOLTO ATTIVO] Parla chiaramente...")
    with stream:
        while collected_samples < target_samples:
            frame, _ = stream.read(VAD_FRAME_SAMPLES)
            frame_int16 = frame.flatten()
            frame_f32 = frame_int16.astype(np.float32) / 32768.0

            prob = vad.process_window(frame_f32)
            if prob >= 0.50:  # Pure speech detected
                accumulated_speech.append(frame_int16)
                collected_samples += len(frame_int16)

                pct = min(100, int((collected_samples / target_samples) * 100))
                sys.stdout.write(f"\rCampionamento voce pura: {pct}% [{collected_samples}/{target_samples} samples]")
                sys.stdout.flush()

    print("\n[OK] Frame vocali isolati con successo (zero silenzio residuo).")
    return np.concatenate(accumulated_speech)


def save_debug_wav(samples: np.ndarray, path: Path):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(samples.tobytes())


def main():
    parser = argparse.ArgumentParser(description="A-X-L Biometric Voice Enrollment")
    parser.add_argument("-n", "--name", type=str, default="owner", help="Profile name (default: owner)")
    parser.add_argument("-l", "--lang", type=str, choices=["it", "en", "both"], default="both", help="Language set")
    parser.add_argument("-d", "--device", type=int, default=None, help="Audio input device index")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_profile = OUTPUT_DIR / f"{args.name}.npy"
    debug_wav = OUTPUT_DIR / f"{args.name}_debug_voice.wav"

    print(f"=== A-X-L PURE-SPEECH BIOMETRIC ENROLLMENT ===")
    print(f"Target: {target_profile.name} | Lang: {args.lang.upper()} | Target Voice: {TARGET_SPEECH_SECONDS}s per take")

    vad = SileroVAD(MODELS_DIR / "silero_vad.onnx", threshold=0.5)
    verifier = SpeakerVerifier(models_dir=MODELS_DIR)

    prompts_list = PROMPTS[args.lang]
    embeddings: List[np.ndarray] = []
    all_speech_samples: List[np.ndarray] = []

    for idx, prompt in enumerate(prompts_list, 1):
        print(f"\n--- TAKE {idx}/{len(prompts_list)} ---")
        speech_clip = capture_pure_speech(vad, prompt, target_sec=TARGET_SPEECH_SECONDS, device_idx=args.device)
        all_speech_samples.append(speech_clip)

        emb = verifier.extract_embedding(speech_clip)
        if emb is None:
            print("[ERRORE] Impossibile estrarre l'embedding dal campione.")
            sys.exit(1)

        norm = np.linalg.norm(emb)
        embeddings.append(emb / norm)
        print(f"[OK] Embedding Take {idx} generato.")

    # Save audio concatenation to WAV for human auditing
    save_debug_wav(np.concatenate(all_speech_samples), debug_wav)
    print(f"\n[AUDIT] Audio vocale puro salvato per verifica in: {debug_wav}")

    # Compute unit-normalized centroid
    centroid = np.mean(embeddings, axis=0)
    centroid = centroid / np.linalg.norm(centroid)

    print("\n=== VERIFICA COERENZA INTERNA (COSINE SIMILARITY) ===")
    for i, emb in enumerate(embeddings, 1):
        sim = float(np.dot(emb, centroid))
        print(f"  - Take {i} vs Centroide: {sim:.4f}")

    np.save(target_profile, centroid)
    print(f"\n[SUCCESSO] Profilo biometrico registrato: {target_profile}")


if __name__ == "__main__":
    main()