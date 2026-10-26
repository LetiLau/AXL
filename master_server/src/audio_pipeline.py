import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import onnxruntime as ort
from openwakeword.model import Model as OWWModel

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
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self.context_size), dtype=np.float32)
        self._sr_tensor = np.array(self.sample_rate, dtype=np.int64)

    def process_window(self, samples_float32: np.ndarray) -> float:
        if len(samples_float32) != self.window_size_samples:
            raise ValueError(f"Expected {self.window_size_samples} samples, received {len(samples_float32)}")

        x = np.expand_dims(samples_float32, axis=0)
        x_with_context = np.concatenate([self._context, x], axis=1)

        ort_inputs = {
            self.input_name: x_with_context,
            self.state_name: self._state,
            self.sr_name: self._sr_tensor,
        }

        outputs = self.session.run(self.output_names, ort_inputs)
        speech_prob = float(outputs[0].flatten()[0])
        self._state = outputs[1]
        self._context = x[:, -self.context_size:]

        return speech_prob


class WakeWordDetector:
    """
    openWakeWord wrapper operating on strict 1280-sample chunks (80ms).
    """

    def __init__(self, models_dir: Path, threshold: float = 0.35):
        self.threshold = threshold
        self.custom_model_path = models_dir / "axl.onnx"

        if self.custom_model_path.exists():
            model_target = str(self.custom_model_path)
            logger.info(f"Loading custom wake-word model: {self.custom_model_path.name}")
        else:
            model_target = "hey_jarvis"
            logger.info(f"Custom 'axl.onnx' not found. Active model: hey_jarvis (threshold: {threshold})")

        self.oww = OWWModel(
            wakeword_models=[model_target],
            inference_framework="onnx",
        )
        self.model_key = list(self.oww.models.keys())[0]

    def reset(self) -> None:
        self.oww.reset()

    def predict(self, pcm16_chunk: np.ndarray) -> float:
        prediction = self.oww.predict(pcm16_chunk)
        return float(prediction.get(self.model_key, 0.0))


class AudioPipeline:
    """
    Multi-rate Audio Ingestion & FSM:
    - Silero VAD window: 512 samples (32ms)
    - openWakeWord window: 1280 samples (80ms)
    - End-of-Speech: 800ms sustained silence hangover
    - Guardrail watchdog: 10s maximum command duration
    """

    STATE_IDLE = "IDLE"
    STATE_COLLECTING_COMMAND = "COLLECTING_COMMAND"

    def __init__(
        self,
        models_dir: Path,
        vad_threshold: float = 0.5,
        wakeword_threshold: float = 0.35,
        silence_hangover_sec: float = 0.8,
        max_command_duration_sec: float = 10.0,
    ):
        self.vad = SileroVAD(models_dir / "silero_vad.onnx", threshold=vad_threshold)
        self.wakeword = WakeWordDetector(models_dir, threshold=wakeword_threshold)

        self.silence_hangover_sec = silence_hangover_sec
        self.max_command_duration_sec = max_command_duration_sec

        self._vad_residual = bytearray()
        self._vad_window_bytes = 512 * 2  # 1024 bytes

        self._ww_residual = bytearray()
        self._ww_window_bytes = 1280 * 2  # 2560 bytes

        # State tracking
        self.state = self.STATE_IDLE
        self.last_speech_prob = 0.0
        self.last_speech_timestamp = 0.0
        self.command_start_timestamp = 0.0
        self.command_start_sample = 0
        self.total_samples_ingested = 0

    def reset(self) -> None:
        self._vad_residual.clear()
        self._ww_residual.clear()
        self.vad.reset_state()
        self.wakeword.reset()
        self.state = self.STATE_IDLE
        self.last_speech_prob = 0.0
        self.last_speech_timestamp = 0.0
        self.command_start_timestamp = 0.0
        self.command_start_sample = 0
        self.total_samples_ingested = 0

    def ingest_frame(self, pcm_bytes: bytes) -> List[Dict[str, Any]]:
        self._vad_residual.extend(pcm_bytes)
        self._ww_residual.extend(pcm_bytes)

        n_samples = len(pcm_bytes) // 2
        self.total_samples_ingested += n_samples
        events = []
        now = time.monotonic()

        # --- Stage 1: Silero VAD (512 samples) ---
        while len(self._vad_residual) >= self._vad_window_bytes:
            window = self._vad_residual[:self._vad_window_bytes]
            del self._vad_residual[:self._vad_window_bytes]

            samples_int16 = np.frombuffer(window, dtype=np.int16)
            samples_float32 = samples_int16.astype(np.float32) / 32768.0

            prob = self.vad.process_window(samples_float32)
            self.last_speech_prob = prob
            is_speech = prob >= self.vad.threshold

            if is_speech:
                self.last_speech_timestamp = now

        # --- Stage 2: openWakeWord Streaming (1280 samples) ---
        while len(self._ww_residual) >= self._ww_window_bytes:
            chunk = self._ww_residual[:self._ww_window_bytes]
            del self._ww_residual[:self._ww_window_bytes]

            samples_int16 = np.frombuffer(chunk, dtype=np.int16)
            ww_score = self.wakeword.predict(samples_int16)

            if ww_score > 0.15:
                events.append({
                    "event": "WAKEWORD_PROBE",
                    "score": ww_score,
                    "model": self.wakeword.model_key,
                })

            if self.state == self.STATE_IDLE and ww_score >= self.wakeword.threshold:
                self.state = self.STATE_COLLECTING_COMMAND
                self.command_start_timestamp = now
                # Include 1.5s pre-roll (24000 samples) to ensure the wake-word & onset are fully captured
                self.command_start_sample = max(0, self.total_samples_ingested - 24000)
                events.append({
                    "event": "WAKEWORD_TRIGGER",
                    "score": ww_score,
                    "model": self.wakeword.model_key,
                })

        # --- Stage 3: End-of-Speech & Guardrail Watchdog Evaluation ---
        if self.state == self.STATE_COLLECTING_COMMAND:
            silence_duration = now - self.last_speech_timestamp
            command_duration = now - self.command_start_timestamp

            # Condition A: Silence closure with post-wakeword grace window (>= 0.8s total duration)
            silence_triggered = (
                silence_duration >= self.silence_hangover_sec
                and command_duration >= 0.8
            )
            # Condition B: Safety timeout guardrail
            timeout_triggered = command_duration >= self.max_command_duration_sec

            if silence_triggered or timeout_triggered:
                captured_samples = self.total_samples_ingested - self.command_start_sample
                reason = "silence_eos" if silence_triggered else "timeout_guardrail"

                events.append({
                    "event": "COMMAND_COMPLETE",
                    "start_sample": self.command_start_sample,
                    "total_samples": captured_samples,
                    "trigger_reason": reason,
                    "command_duration_sec": command_duration,
                })

                events.append({
                    "event": "SILENCE_RESET",
                    "silence_sec": silence_duration,
                    "reason": reason,
                })

                self.state = self.STATE_IDLE
                self.wakeword.reset()

        return events