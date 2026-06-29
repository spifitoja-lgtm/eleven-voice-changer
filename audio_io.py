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
            self.stream = sd.InputStream(
                device=self.device_index,
                samplerate=self.samplerate,
                channels=CAPTURE_CHANNELS,
                dtype="float32",
                callback=self._cb,
            )
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
    """Streaming int16 PCM playback to an output device."""

    def __init__(self, device_index: int | None, samplerate: int = 44100):
        self.device_index = device_index
        self.samplerate = samplerate
        self.stream: sd.OutputStream | None = None

    def __enter__(self) -> "Player":
        self.stream = sd.OutputStream(
            device=self.device_index,
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
        )
        self.stream.start()
        return self

    def write(self, pcm: np.ndarray) -> None:
        if self.stream is None:
            return
        self.stream.write(pcm.reshape(-1, 1))

    def __exit__(self, *exc) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
