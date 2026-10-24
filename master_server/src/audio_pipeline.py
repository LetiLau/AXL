import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
import numpy as np
import onnxruntime as ort

logger = logging.getLogger("AXL-AudioPipeline")


class SileroVAD:
    """
    Inference wrapper for Silero VAD v5 ONNX model.
    Optimized for low-latency sequential CPU execution (x86_64 AVX2).
    """

    def __init__(self, model_path: Path, threshold: float = 0.5, sample_rate: int = 16000):
        if not model_path.exists():
            raise FileNotFoundError(f"Silero VAD checkpoint not found: {model_path}")

        self.sample_rate = sample_rate
        self.threshold = threshold
        self.window_size_samples = 512  # 32ms at 16kHz for Silero v5

        # Restrict execution thread pool for low-power dual-core CPU target
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

        # Introspect model inputs dynamically
        self.input_names = [inp.name for inp in self.session.get_inputs()]
        self.output_names = [out.name for out in self.session.get_outputs()]

        # Initial hidden state tensor: shape [2, batch_size=1, 128]
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr_tensor = np.array(self.sample_rate, dtype=np.int64)

    def reset_state(self) -> None:
        """Zeroes recurrent neural memory."""
        self._state.fill(0.0)

    def process_window(self, samples_float32: np.ndarray) -> float:
        """
        Executes inference on exactly 512 float32 normalized samples [-1.0, 1.0].
        Returns speech probability p in range [0.0, 1.0].
        """
        if len(samples_float32) != self.window_size_samples:
            raise ValueError(f"Expected {self.window_size_samples} samples, received {len(samples_float32)}")

        # Reshape to [batch_size=1, samples=512]
        input_data = np.expand_dims(samples_float32, axis=0)

        ort_inputs = {
            self.input_names[0]: input_data,
            self.input_names[1]: self._state,
            self.input_names[2]: self._sr_tensor,
        }

        outputs = self.session.run(self.output_names, ort_inputs)
        
        # Silero returns [probability, next_state]
        speech_prob = float(outputs[0][0][0])
        self._state = outputs[1]

        return speech_prob


class AudioPipeline:
    """
    DSP orchestration pipeline: PCM byte chunking, Silero VAD evaluation,
    and downstream feature dispatching.
    """

    def __init__(self, models_dir: Path, vad_threshold: float = 0.5):
        vad_path = models_dir / "silero_vad.onnx"
        self.vad = SileroVAD(vad_path, threshold=vad_threshold)
        
        # Audio accumulator: bridge 320-sample inputs to 512-sample VAD windows
        self._residual_buffer = bytearray()
        self._vad_window_bytes = 512 * 2  # 512 samples * 2 bytes (int16)
        
        # Operational telemetry
        self.is_speech_active = False
        self.last_speech_prob = 0.0

    def reset(self) -> None:
        """Clears residual accumulator and recurrent states on client reconnect."""
        self._residual_buffer.clear()
        self.vad.reset_state()
        self.is_speech_active = False
        self.last_speech_prob = 0.0

    def ingest_frame(self, pcm_bytes: bytes) -> Optional[Dict[str, float]]:
        """
        Ingests variable or 640-byte raw PCM chunks.
        Evaluates VAD whenever 512 samples (1024 bytes) are aggregated.
        Returns detection metadata dict or None if insufficient window.
        """
        self._residual_buffer.extend(pcm_bytes)

        latest_meta = None
        while len(self._residual_buffer) >= self._vad_window_bytes:
            # Extract exactly 512 samples
            window_bytes = self._residual_buffer[:self._vad_window_bytes]
            del self._residual_buffer[:self._vad_window_bytes]

            # Convert to float32 [-1.0, 1.0] without copy bloat
            samples_int16 = np.frombuffer(window_bytes, dtype=np.int16)
            samples_float32 = samples_int16.astype(np.float32) / 32768.0

            prob = self.vad.process_window(samples_float32)
            self.last_speech_prob = prob
            self.is_speech_active = prob >= self.vad.threshold

            latest_meta = {
                "speech_prob": prob,
                "is_speech": self.is_speech_active,
            }

        return latest_meta