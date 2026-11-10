import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv
import numpy as np
import sherpa_onnx

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

logger = logging.getLogger("AXL-SpeakerID")

THRESHOLD_VAR = float(os.getenv("AXL_SPEAKER_THRESHOLD", "0.48"))  # Configurable via .env, calibrated with tools/calibrate_speaker.py


class SpeakerVerifier:
    """
    Speaker verification engine powered by sherpa-onnx and CAM++ embedding model.
    Evaluates Cosine Similarity against enrolled voice profiles.
    """

    def __init__(
        self,
        models_dir: Path,
        threshold: float = THRESHOLD_VAR,
        owner_profile_name: str = "owner.npy",
    ):
        self.threshold = threshold
        self.models_dir = models_dir
        self.profiles_dir = models_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.owner_file = self.profiles_dir / owner_profile_name

        model_path = models_dir / "speaker_id.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"Speaker ID ONNX model missing: {model_path}")

        # Sherpa-onnx extractor configuration (optimized for low CPU consumption)
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path),
            num_threads=1,
            debug=False,
            provider="cpu",
        )
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.enrolled_profiles: Dict[str, np.ndarray] = {}
        self.security_mode: str = os.getenv("AXL_SECURITY_MODE", "friendly").lower().strip()
        logger.info(f"Speaker Verifier active in '{self.security_mode}' security mode.")

        self.load_profiles()

    def set_security_mode(self, mode: str) -> str:
        """
        Dynamically changes security mode: 'friendly', 'restrict', or 'paranoid'.
        """
        clean_mode = mode.lower().strip()
        if clean_mode in ["friendly", "restrict", "paranoid"]:
            self.security_mode = clean_mode
            logger.info(f">>> [SECURITY MODE UPDATED] Active mode: '{self.security_mode}' <<<")
            return f"Security mode successfully set to '{self.security_mode}'."
        return f"Unknown security mode '{mode}'. Choose 'friendly', 'restrict', or 'paranoid'."

    def load_profiles(self) -> None:
        """Loads all .npy embedding vectors from the profiles directory."""
        self.enrolled_profiles.clear()
        for p_file in self.profiles_dir.glob("*.npy"):
            name = p_file.stem
            try:
                embedding = np.load(p_file)
                self.enrolled_profiles[name] = embedding
                logger.info(f"Loaded voice profile: '{name}' (dim: {embedding.shape[0]})")
            except Exception as exc:
                logger.error(f"Failed to load voice profile {p_file}: {exc}")

        if "owner" not in self.enrolled_profiles:
            logger.warning(
                f"Owner profile ('{self.owner_file.name}') not found! Run enroll_voice.py to initialize."
            )

    def extract_embedding(self, pcm16_samples: np.ndarray) -> Optional[np.ndarray]:
        """
        Computes 1D normalized speaker embedding from int16 audio array.
        Audio must be at least 1-1.5 seconds long for stable convergence.
        """
        if len(pcm16_samples) < 16000:  # Less than 1 second
            logger.warning("Audio slice too short for reliable speaker verification.")
            return None

        # Convert int16 to float32 [-1.0, 1.0]
        samples_f32 = pcm16_samples.astype(np.float32) / 32768.0

        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples_f32)
        stream.input_finished()

        embedding = self.extractor.compute(stream)
        return np.array(embedding, dtype=np.float32)

    def verify(self, pcm16_samples: np.ndarray) -> Tuple[bool, float, str]:
        """
        Matches speech chunk against enrolled profiles based on the active security mode.
        Modes:
        - 'friendly': Trusts and authorizes every speaker (perfect for development/testing).
        - 'restrict': Authorizes owner and enrolled friends (*.npy), rejects total strangers.
        - 'paranoid': Authorizes ONLY the owner. If a friend speaks, identifies them but rejects orders.

        Returns: (is_authorized, similarity_score, matched_identity)
        """
        if "owner" not in self.enrolled_profiles:
            return True, 1.0, "unregistered_owner"

        embedding = self.extract_embedding(pcm16_samples)
        if embedding is None:
            # Fallback on short audio
            is_auth = (self.security_mode == "friendly")
            return is_auth, 0.0, ("friendly_guest" if is_auth else "unknown_too_short")

        norm_a = np.linalg.norm(embedding)

        # Compute cosine similarity across all enrolled profiles
        scores: Dict[str, float] = {}
        for name, prof_emb in self.enrolled_profiles.items():
            norm_b = np.linalg.norm(prof_emb)
            if norm_a == 0 or norm_b == 0:
                scores[name] = 0.0
            else:
                scores[name] = float(np.dot(embedding, prof_emb) / (norm_a * norm_b))

        owner_score = scores.get("owner", 0.0)
        best_name, best_score = max(scores.items(), key=lambda item: item[1]) if scores else ("unknown", 0.0)

        if self.security_mode == "friendly":
            # Friendly mode: authorizes everyone; marks as owner if matched, else friendly_guest
            is_authorized = True
            identity = "owner" if owner_score >= self.threshold else (best_name if best_score >= self.threshold else "friendly_guest")
            reported_score = max(owner_score, best_score)

        elif self.security_mode == "restrict":
            # Restrict mode: authorizes owner and enrolled friends; rejects strangers
            is_authorized = (best_score >= self.threshold)
            identity = best_name if is_authorized else "stranger"
            reported_score = best_score

        elif self.security_mode == "paranoid":
            # Paranoid mode: authorizes ONLY the registered owner
            is_authorized = (owner_score >= self.threshold)
            if is_authorized:
                identity = "owner"
            elif best_name != "owner" and best_score >= self.threshold:
                identity = f"friend_{best_name}"  # Friend recognized, but not allowed in paranoid mode
            else:
                identity = "stranger"
            reported_score = owner_score

        else:
            is_authorized = owner_score >= self.threshold
            identity = "owner" if is_authorized else "stranger"
            reported_score = owner_score

        return is_authorized, reported_score, identity