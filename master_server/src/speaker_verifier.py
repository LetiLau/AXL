import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
import numpy as np
import sherpa_onnx

logger = logging.getLogger("AXL-SpeakerID")


class SpeakerVerifier:
    """
    Speaker verification engine powered by sherpa-onnx and CAM++ embedding model.
    Evaluates Cosine Similarity against enrolled voice profiles.
    """

    def __init__(
        self,
        models_dir: Path,
        threshold: float = 0.65,
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

        self.load_profiles()

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
        Matches speech chunk against enrolled owner.
        Returns: (is_owner, similarity_score, matched_name)
        """
        if "owner" not in self.enrolled_profiles:
            # Degraded operation: accept by default but raise warning
            return True, 1.0, "unregistered_owner"

        embedding = self.extract_embedding(pcm16_samples)
        if embedding is None:
            # Fallback on truncated audio to avoid blocking the pipeline
            return False, 0.0, "unknown_too_short"

        owner_embedding = self.enrolled_profiles["owner"]

        # Cosine Similarity computation
        norm_a = np.linalg.norm(embedding)
        norm_b = np.linalg.norm(owner_embedding)

        if norm_a == 0 or norm_b == 0:
            score = 0.0
        else:
            score = float(np.dot(embedding, owner_embedding) / (norm_a * norm_b))

        is_owner = score >= self.threshold
        matched_identity = "owner" if is_owner else "stranger"

        return is_owner, score, matched_identity