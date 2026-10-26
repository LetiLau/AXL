#!/usr/bin/env python3
"""
A-X-L VAD-Gated Speaker Calibration Tool.
Isolates active speech frames from live utterances before evaluating Cosine Similarity.
"""

import argparse
import sys
from pathlib import Path
import numpy as np
import sounddevice as sd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from audio_pipeline import SileroVAD
from speaker_verifier import SpeakerVerifier

SAMPLE_RATE = 16000
VAD_FRAME_SAMPLES = 512
MODELS_DIR = BASE_DIR / "models"


def capture_utterance(vad: SileroVAD, device_idx=None, min_speech_sec: float = 1.2) -> np.ndarray:
    """Listens until user speaks and finishes (detected via voice onset and silence hangover)."""
    vad.reset_state()
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=VAD_FRAME_SAMPLES,
        channels=1,
        dtype="int16",
        device=device_idx,
    )

    speech_frames = []
    is_speaking = False
    silence_counter = 0
    max_silence_frames = int(0.7 * (SAMPLE_RATE / VAD_FRAME_SAMPLES))  # ~700ms hangover

    print("[IN ASCOLTO] Parla adesso...")
    with stream:
        while True:
            frame, _ = stream.read(VAD_FRAME_SAMPLES)
            frame_int16 = frame.flatten()
            frame_f32 = frame_int16.astype(np.float32) / 32768.0

            prob = vad.process_window(frame_f32)

            if prob >= 0.50:
                is_speaking = True
                silence_counter = 0
                speech_frames.append(frame_int16)
            elif is_speaking:
                speech_frames.append(frame_int16)
                silence_counter += 1
                if silence_counter >= max_silence_frames:
                    # Speech ended cleanly
                    break

    pure_audio = np.concatenate(speech_frames)
    duration = len(pure_audio) / SAMPLE_RATE
    print(f"[CATTURA COMPLETATA] Rilevati {duration:.2f}s di segnale vocale.")
    return pure_audio


def main():
    parser = argparse.ArgumentParser(description="A-X-L Speaker Calibration")
    parser.add_argument("-d", "--device", type=int, default=None, help="Audio input device index")
    args = parser.parse_args()

    vad = SileroVAD(MODELS_DIR / "silero_vad.onnx", threshold=0.5)
    verifier = SpeakerVerifier(models_dir=MODELS_DIR)

    if "owner" not in verifier.enrolled_profiles:
        print("[ERRORE] Nessun profilo 'owner.npy' trovato. Esegui prima enroll_voice.py.")
        sys.exit(1)

    print("=== A-X-L SPEAKER CALIBRATION TESTBENCH (VAD-GATED) ===")
    print("Eseguiremo 3 test. Pronuncia un comando naturale appena vedi '[IN ASCOLTO]'.\n")

    scores = []
    for test_idx in range(1, 4):
        input(f"Test {test_idx}/3: Premi INVIO per iniziare...")
        samples = capture_utterance(vad, device_idx=args.device)

        if len(samples) < 16000:
            print("[WARN] Frase troppo corta (< 1.0s). Riprova allungando la frase.")
            continue

        _, score, _ = verifier.verify(samples)
        scores.append(score)
        print(f"-> Cosine Similarity: {score:.4f}\n")

    if scores:
        avg_score = float(np.mean(scores))
        min_score = float(np.min(scores))
        print("=== RISULTATI CALIBRAZIONE ===")
        print(f"Punteggio Medio:   {avg_score:.4f}")
        print(f"Punteggio Minimo:  {min_score:.4f}")
        print(f"Soglia consigliata per il server: {round(min_score - 0.05, 2)}")


if __name__ == "__main__":
    main()