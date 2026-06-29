"""Audio input/output helpers built on sounddevice."""

from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd

CAPTURE_SAMPLERATE = 44100
CAPTURE_CHANNELS = 1


def list_input_devices() -> list[tuple[int, str]]:
    devs = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            devs.append((i, d["name"]))
    return devs


def list_output_devices() -> list[tuple[int, str]]:
    devs = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            devs.append((i, d["name"]))
    return devs


def device_default_samplerate(device_index: int | None) -> int:
    """Return the device's default samplerate as int (fallback 44100)."""
    if device_index is None:
        try:
            return int(sd.query_devices(kind="output")["default_samplerate"])
        except Exception:
            return 44100
    try:
        return int(sd.query_devices(device_index)["default_samplerate"])
    except Exception:
        return 44100


def resample_int16(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolate int16 PCM from src_rate to dst_rate."""
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm
    n_src = len(pcm)
    n_dst = max(1, int(round(n_src * dst_rate / src_rate)))
    x_src = np.arange(n_src, dtype=np.float64)
    x_dst = np.linspace(0, n_src - 1, num=n_dst, dtype=np.float64)
    return np.interp(x_dst, x_src, pcm.astype(np.float32)).astype(np.int16)


class Recorder:
    """Continuous capture into a queue; .stop_and_collect() returns int16 PCM."""

    def __init__(self, device_index: int | None = None, samplerate: int = CAPTURE_SAMPLERATE):
        self.device_index = device_index
        self.samplerate = samplerate
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.stream:
                return
            # Try requested rate first; fall back to device default on InvalidSampleRate
            try:
                self.stream = sd.InputStream(
                    device=self.device_index,
                    samplerate=self.samplerate,
                    channels=CAPTURE_CHANNELS,
                    dtype="float32",
                    callback=self._cb,
                )
            except sd.PortAudioError as e:
                if "Invalid sample rate" in str(e) or "-9997" in str(e):
                    self.samplerate = device_default_samplerate(self.device_index)
                    self.stream = sd.InputStream(
                        device=self.device_index,
                        samplerate=self.samplerate,
                        channels=CAPTURE_CHANNELS,
                        dtype="float32",
                        callback=self._cb,
                    )
                else:
                    raise
            self.stream.start()

    def _cb(self, indata, frames, time_, status) -> None:
        if status:
            pass  # ignore xruns
        self.q.put(indata.copy())

    def stop_and_collect(self) -> np.ndarray | None:
        with self._lock:
            if not self.stream:
                return None
            self.stream.stop()
            self.stream.close()
            self.stream = None
        frames = []
        while not self.q.empty():
            try:
                frames.append(self.q.get_nowait())
            except queue.Empty:
                break
        if not frames:
            return None
        pcm_f32 = np.concatenate(frames).flatten()
        return (np.clip(pcm_f32, -1.0, 1.0) * 32767.0).astype(np.int16)


class Player:
    """Streaming int16 PCM playback to an output device.

    `source_samplerate` is the rate of incoming PCM (from ElevenLabs).
    Stream is opened at the device's native default rate; chunks are
    resampled on the fly if rates differ.
    """

    def __init__(
        self,
        device_index: int | None,
        source_samplerate: int = 44100,
        device_samplerate: int | None = None,
    ):
        self.device_index = device_index
        self.source_samplerate = source_samplerate
        self.device_samplerate = device_samplerate or device_default_samplerate(device_index)
        self.stream: sd.OutputStream | None = None

    def __enter__(self) -> "Player":
        # Try device default first; if that fails, try a few common rates
        candidates = [self.device_samplerate, 48000, 44100, 22050, 16000]
        last_err: Exception | None = None
        for rate in candidates:
            try:
                self.stream = sd.OutputStream(
                    device=self.device_index,
                    samplerate=rate,
                    channels=1,
                    dtype="int16",
                )
                self.stream.start()
                self.device_samplerate = rate
                break
            except sd.PortAudioError as e:
                last_err = e
                continue
        if not self.stream:
            raise RuntimeError(
                f"Output device doesn't accept any common samplerate. Last err: {last_err}"
            )
        return self

    def write(self, pcm: np.ndarray) -> None:
        if self.stream is None:
            return
        if self.source_samplerate != self.device_samplerate:
            pcm = resample_int16(pcm, self.source_samplerate, self.device_samplerate)
        self.stream.write(pcm.reshape(-1, 1))

    def __exit__(self, *exc) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
