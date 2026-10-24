import logging
from pathlib import Path
from typing import Any, Dict, Optional
import numpy as np
import onnxruntime as ort

logger = logging.getLogger("AXL-AudioPipeline")


class SileroVAD:
    """
    Inference wrapper for Silero VAD v5 ONNX model.
    Enforces the mandatory 64-sample rolling context window (576 input samples at 16kHz).
    """

    def __init__(self, model_path: Path, threshold: float = 0.5, sample_rate: int = 16000):
        if not model_path.exists():
            raise FileNotFoundError(f"Silero VAD checkpoint not found: {model_path}")

        self.sample_rate = sample_rate
        self.threshold = threshold
        self.window_size_samples = 512
        # Silero v5 architectural requirement: 64 samples for 16kHz (32 samples for 8kHz)
        self.context_size = 64 if sample_rate == 16000 else 32

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

        input_names = [inp.name for inp in self.session.get_inputs()]
        self.input_name = next(name for name in input_names if "input" in name)
        self.state_name = next(name for name in input_names if "state" in name)
        self.sr_name = next(name for name in input_names if "sr" in name)
        self.output_names = [out.name for out in self.session.get_outputs()]

        self.reset_state()

    def reset_state(self) -> None:
        """Zeroes recurrent neural memory and rolling context."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.context_size), dtype=np.float32)
        self._sr_tensor = np.array(self.sample_rate, dtype=np.int64)

    def process_window(self, samples_float32: np.ndarray) -> float:
        """
        Runs inference on 512 float32 samples.
        Concatenates rolling context: input shape becomes [1, 576].
        """
        if len(samples_float32) != self.window_size_samples:
            raise ValueError(f"Expected {self.window_size_samples} samples, received {len(samples_float32)}")

        # Reshape current audio chunk to [1, 512]
        x = np.expand_dims(samples_float32, axis=0)

        # Concatenate 64 context samples to the left -> [1, 576]
        x_with_context = np.concatenate([self._context, x], axis=1)

        ort_inputs = {
            self.input_name: x_with_context,
            self.state_name: self._state,
            self.sr_name: self._sr_tensor,
        }

        outputs = self.session.run(self.output_names, ort_inputs)

        speech_prob = float(outputs[0].flatten()[0])
        self._state = outputs[1]
        
        # Preserve trailing 64 samples as context for the subsequent window
        self._context = x[:, -self.context_size:]

        return speech_prob


class AudioPipeline:
    """
    Audio stream ingestion and VAD state machine.
    """

    def __init__(self, models_dir: Path, vad_threshold: float = 0.5):
        vad_path = models_dir / "silero_vad.onnx"
        self.vad = SileroVAD(vad_path, threshold=vad_threshold)

        self._residual_buffer = bytearray()
        self._vad_window_bytes = 512 * 2  # 1024 bytes (512 samples int16)

        self.is_speech_active = False
        self.last_speech_prob = 0.0

    def reset(self) -> None:
        self._residual_buffer.clear()
        self.vad.reset_state()
        self.is_speech_active = False
        self.last_speech_prob = 0.0

    def ingest_frame(self, pcm_bytes: bytes) -> Optional[Dict[str, Any]]:
        self._residual_buffer.extend(pcm_bytes)

        result = None
        while len(self._residual_buffer) >= self._vad_window_bytes:
            window_bytes = self._residual_buffer[:self._vad_window_bytes]
            del self._residual_buffer[:self._vad_window_bytes]

            samples_int16 = np.frombuffer(window_bytes, dtype=np.int16)
            samples_float32 = samples_int16.astype(np.float32) / 32768.0

            prob = self.vad.process_window(samples_float32)
            self.last_speech_prob = prob

            previous_state = self.is_speech_active
            self.is_speech_active = prob >= self.vad.threshold

            speech_started = not previous_state and self.is_speech_active
            speech_ended = previous_state and not self.is_speech_active

            result = {
                "speech_prob": prob,
                "is_speech": self.is_speech_active,
                "speech_started": speech_started,
                "speech_ended": speech_ended,
            }

        return result