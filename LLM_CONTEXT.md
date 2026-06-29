# Cały kod w jednym miejscu (dla AI)

Wklej całość tego pliku w Claude / GPT / Gemini z opisem problemu — model dostaje pełen kontekst projektu i może zaproponować poprawkę.

## Co robi program

Push-to-talk voice changer. Workflow:
1. Użytkownik wpisuje API key ElevenLabs + wybiera voice_id (swój wytrenowany)
2. Trzyma niebieski przycisk (lub SPACJA), mówi
3. Po puszczeniu: PCM audio z mikrofonu → `POST /v1/speech-to-speech/{voice_id}/stream` ElevenLabs API → streamowany PCM odpowiedź → playback na wybranym output device
4. Output device routowany na virtual mic (VB-Cable / BlackHole) → Telegram/Discord słyszy zmieniony głos

## Stack

- Python 3.12 (Windows EXE), Tkinter GUI
- `sounddevice` (PortAudio wrapper) — capture + playback
- `numpy` — PCM buffer + linear resampling
- `requests` — HTTPS do ElevenLabs API
- PyInstaller one-folder build → `ElevenVoiceChanger.exe`
- GitHub Actions windows-latest runner → Release ZIP

## Znane problemy / TODO

- **Latencja ~500ms-1.5s**: ElevenLabs STS przez API nie jest pełen real-time. Trzeba by zaimplementować WebSocket streaming albo VAD z mniejszymi chunkami.
- **Push-to-talk only**: nie ma continuous mode z VAD.
- **Brak input streaming**: API żąda całego WAV pliku per request — nie da się wysyłać małych chunków bez zwiększenia latencji.
- **401 Unauthorized**: jeśli klucz ElevenLabs jest "Restricted" bez scope "Voices" — `GET /v1/voices` zwraca 401. Fix: użytkownik musi utworzyć klucz "All Access" w panelu.

## Pliki

### `app.py`

```python
"""ElevenLabs Voice Changer GUI — push-to-talk speech-to-speech.

Hold the big mic button (or SPACE while window focused) → record → release →
the recorded audio is sent to ElevenLabs STS with your trained voice_id →
returned audio plays through the selected output device. Route that output to
a virtual mic (VB-Cable on Windows, BlackHole on Mac) to feed Telegram/Discord.
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import requests

from audio_io import (
    CAPTURE_SAMPLERATE,
    Player,
    Recorder,
    device_default_samplerate,
    list_input_devices,
    list_output_devices,
)
from elevenlabs_sts import DEFAULT_MODEL, convert_stream, list_voices
import settings

APP_TITLE = "ElevenLabs Voice Changer"
OUTPUT_SAMPLERATE = 44100

try:
    import sv_ttk
    HAS_THEME = True
except Exception:
    HAS_THEME = False


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("760x640")

        self.log_q: queue.Queue[str] = queue.Queue()
        self.recorder: Recorder | None = None
        self.recording = False
        self.voices: list[dict] = []

        self.cfg = settings.load()
        self._build_ui()
        self._poll_log()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        creds = ttk.LabelFrame(self.root, text="ElevenLabs")
        creds.pack(fill="x", **pad)

        ttk.Label(creds, text="API key:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.api_var = tk.StringVar(value=self.cfg.get("api_key", ""))
        ttk.Entry(creds, textvariable=self.api_var, show="•", width=58).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(creds, text="Load voices", command=self._load_voices).grid(row=0, column=2, padx=6)

        ttk.Label(creds, text="Voice:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.voice_var = tk.StringVar(value=self.cfg.get("voice_label", ""))
        self.voice_combo = ttk.Combobox(creds, textvariable=self.voice_var, width=56, state="readonly")
        self.voice_combo.grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(creds, text="Manual ID…", command=self._enter_manual_voice).grid(row=1, column=2, padx=6)

        ttk.Label(creds, text="Model:").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.model_var = tk.StringVar(value=self.cfg.get("model", DEFAULT_MODEL))
        ttk.Combobox(
            creds, textvariable=self.model_var, width=56,
            values=["eleven_multilingual_sts_v2", "eleven_english_sts_v2"],
        ).grid(row=2, column=1, sticky="ew", padx=6)

        creds.columnconfigure(1, weight=1)

        # ---- devices ----
        devs = ttk.LabelFrame(self.root, text="Audio devices")
        devs.pack(fill="x", **pad)

        self.input_devs = list_input_devices()
        self.output_devs = list_output_devices()

        ttk.Label(devs, text="Input mic:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.input_var = tk.StringVar()
        self.input_combo = ttk.Combobox(
            devs, textvariable=self.input_var, state="readonly", width=58,
            values=[f"[{i}] {n}" for i, n in self.input_devs],
        )
        self.input_combo.grid(row=0, column=1, sticky="ew", padx=6)
        self._restore_device_selection(self.input_combo, self.input_devs, self.cfg.get("input_device"))

        ttk.Label(devs, text="Output to:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.output_var = tk.StringVar()
        self.output_combo = ttk.Combobox(
            devs, textvariable=self.output_var, state="readonly", width=58,
            values=[f"[{i}] {n}" for i, n in self.output_devs],
        )
        self.output_combo.grid(row=1, column=1, sticky="ew", padx=6)
        self._restore_device_selection(self.output_combo, self.output_devs, self.cfg.get("output_device"))
        ttk.Label(devs, text="(Use VB-Cable on Windows / BlackHole on Mac for routing to Telegram)", foreground="#888").grid(row=2, column=1, sticky="w", padx=6)

        devs.columnconfigure(1, weight=1)

        # ---- voice settings ----
        vs = ttk.LabelFrame(self.root, text="Voice settings")
        vs.pack(fill="x", **pad)
        ttk.Label(vs, text="Stability:").grid(row=0, column=0, sticky="w", padx=6, pady=2)
        self.stability_var = tk.DoubleVar(value=float(self.cfg.get("stability", 0.5)))
        ttk.Scale(vs, from_=0.0, to=1.0, orient="horizontal", variable=self.stability_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(vs, text="Similarity:").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.similarity_var = tk.DoubleVar(value=float(self.cfg.get("similarity", 0.8)))
        ttk.Scale(vs, from_=0.0, to=1.0, orient="horizontal", variable=self.similarity_var).grid(row=1, column=1, sticky="ew", padx=6)
        vs.columnconfigure(1, weight=1)

        # ---- talk button ----
        talk_frm = ttk.Frame(self.root)
        talk_frm.pack(fill="x", **pad)
        self.talk_btn = tk.Button(
            talk_frm, text="🎤 HOLD TO TALK", height=2,
            font=("Arial", 16, "bold"), bg="#2d6cdf", fg="white",
            activebackground="#1e4fb3",
        )
        self.talk_btn.pack(fill="x")
        self.talk_btn.bind("<ButtonPress-1>", self._start_recording)
        self.talk_btn.bind("<ButtonRelease-1>", self._stop_and_send)
        self.root.bind("<KeyPress-space>", lambda e: self._start_recording(e) if not self.recording else None)
        self.root.bind("<KeyRelease-space>", lambda e: self._stop_and_send(e) if self.recording else None)

        # ---- log ----
        self.log = scrolledtext.ScrolledText(self.root, height=12, font=("Consolas", 10))
        self.log.pack(fill="both", expand=True, **pad)
        self.log.configure(state="disabled")

        self.status_var = tk.StringVar(value="Ready. Hold the button (or SPACE) to talk.")
        ttk.Label(self.root, textvariable=self.status_var).pack(fill="x", **pad)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- helpers ----------

    def _restore_device_selection(self, combo: ttk.Combobox, devs: list, saved: str | None) -> None:
        if not devs:
            return
        if saved:
            for idx, value in enumerate(combo["values"]):
                if value == saved:
                    combo.current(idx)
                    return
        combo.current(0)

    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_log(self) -> None:
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log)

    def _get_input_index(self) -> int | None:
        v = self.input_var.get()
        if not v:
            return None
        try:
            return int(v[1:v.index("]")])
        except (ValueError, IndexError):
            return None

    def _get_output_index(self) -> int | None:
        v = self.output_var.get()
        if not v:
            return None
        try:
            return int(v[1:v.index("]")])
        except (ValueError, IndexError):
            return None

    def _current_voice_id(self) -> str | None:
        label = self.voice_var.get()
        for v in self.voices:
            if self._voice_label(v) == label:
                return v["voice_id"]
        # Maybe label IS a raw voice_id (manually entered)
        return label.strip() or None

    @staticmethod
    def _voice_label(v: dict) -> str:
        return f"{v.get('name', '?')}  ({v.get('voice_id', '')})"

    # ---------- API actions ----------

    def _load_voices(self) -> None:
        api = self.api_var.get().strip()
        if not api:
            messagebox.showerror(APP_TITLE, "Wpisz API key najpierw.")
            return
        try:
            self.voices = list_voices(api)
        except requests.RequestException as e:
            messagebox.showerror(APP_TITLE, f"Nie mogę pobrać listy głosów: {e}")
            return
        labels = [self._voice_label(v) for v in self.voices]
        self.voice_combo["values"] = labels
        if labels:
            self.voice_combo.current(0)
        self._log(f"[voices] loaded {len(labels)} voices")

    def _enter_manual_voice(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Manual Voice ID")
        win.geometry("440x130")
        ttk.Label(win, text="Wklej voice_id z ElevenLabs (np. Xb7hH8MSUJpSbSDYk0k2):").pack(padx=10, pady=10)
        v = tk.StringVar(value=self.voice_var.get())
        ttk.Entry(win, textvariable=v, width=50).pack(padx=10)
        def ok():
            self.voice_var.set(v.get().strip())
            self.voice_combo["values"] = list(self.voice_combo["values"]) + [v.get().strip()]
            win.destroy()
        ttk.Button(win, text="OK", command=ok).pack(pady=10)

    # ---------- recording flow ----------

    def _start_recording(self, _event) -> None:
        if self.recording:
            return
        self.recording = True
        self.talk_btn.configure(text="🔴 RECORDING...", bg="#d93b3b")
        self.status_var.set("Recording…")
        self.recorder = Recorder(
            device_index=self._get_input_index(),
            samplerate=CAPTURE_SAMPLERATE,
        )
        try:
            self.recorder.start()
        except Exception as e:
            self.recording = False
            self.talk_btn.configure(text="🎤 HOLD TO TALK", bg="#2d6cdf")
            self.status_var.set("Ready.")
            messagebox.showerror(APP_TITLE, f"Mic open failed: {e}")

    def _stop_and_send(self, _event) -> None:
        if not self.recording:
            return
        self.recording = False
        self.talk_btn.configure(text="🎤 HOLD TO TALK", bg="#2d6cdf")
        self.status_var.set("Sending to ElevenLabs…")

        rec = self.recorder
        self.recorder = None
        if not rec:
            return

        pcm = rec.stop_and_collect()
        rec_rate = rec.samplerate  # actual capture rate (mic might have forced 48k fallback)
        if pcm is None or len(pcm) < rec_rate // 4:  # < 0.25s
            self.status_var.set("Ready. (too short to send)")
            self._log("[rec] too short, skipped")
            return

        api = self.api_var.get().strip()
        voice_id = self._current_voice_id()
        if not api or not voice_id:
            messagebox.showerror(APP_TITLE, "Brak API key lub voice_id.")
            self.status_var.set("Ready.")
            return

        self._save_state()
        threading.Thread(target=self._send_worker, args=(api, voice_id, pcm, rec_rate), daemon=True).start()

    def _send_worker(self, api: str, voice_id: str, pcm, rec_rate: int) -> None:
        out_index = self._get_output_index()
        model = self.model_var.get()
        stability = float(self.stability_var.get())
        similarity = float(self.similarity_var.get())
        # Request ElevenLabs PCM at a rate the device supports — 44100 if device
        # is 44100/48000/22050 etc., else fall back to 22050. Player handles
        # resampling to the actual device rate.
        sts_rate = 44100
        try:
            self.log_q.put(f"[api] sending {len(pcm) / rec_rate:.1f}s of audio (recorded at {rec_rate}Hz)…")
            with Player(out_index, source_samplerate=sts_rate) as player:
                if player.device_samplerate != sts_rate:
                    self.log_q.put(
                        f"[player] device rate {player.device_samplerate}Hz — resampling from {sts_rate}Hz"
                    )
                total = 0
                for chunk in convert_stream(
                    api, voice_id, pcm, rec_rate,
                    model_id=model,
                    stability=stability,
                    similarity_boost=similarity,
                    output_samplerate=sts_rate,
                ):
                    player.write(chunk)
                    total += len(chunk)
            secs = total / sts_rate
            self.log_q.put(f"[api] played {secs:.1f}s of converted audio")
            self.status_var.set("Ready.")
        except Exception as e:
            self.log_q.put(f"[error] {type(e).__name__}: {e}")
            self.status_var.set("Error — see log.")

    # ---------- shutdown ----------

    def _save_state(self) -> None:
        settings.save({
            "api_key": self.api_var.get(),
            "voice_label": self.voice_var.get(),
            "model": self.model_var.get(),
            "input_device": self.input_var.get(),
            "output_device": self.output_var.get(),
            "stability": self.stability_var.get(),
            "similarity": self.similarity_var.get(),
        })

    def _on_close(self) -> None:
        self._save_state()
        if self.recording and self.recorder:
            self.recorder.stop_and_collect()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    if HAS_THEME:
        try:
            sv_ttk.set_theme("dark")
        except Exception:
            pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### `audio_io.py`

```python
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
```

### `elevenlabs_sts.py`

```python
"""ElevenLabs Speech-to-Speech (STS) client.

Sends a chunk of PCM audio to ElevenLabs, streams back converted PCM audio
in the target voice. Designed for push-to-talk flow: record a chunk, send,
play the response. Output is PCM 44.1kHz mono int16 — no ffmpeg needed.
"""

from __future__ import annotations

import io
import wave
from typing import Iterator

import numpy as np
import requests

API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_sts_v2"


def pcm_to_wav_bytes(pcm: np.ndarray, samplerate: int) -> bytes:
    """Encode mono int16 (or float32, will be quantized) PCM to WAV bytes."""
    if pcm.dtype != np.int16:
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
    if pcm.ndim > 1:
        pcm = pcm[:, 0]  # take first channel if stereo
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def convert_stream(
    api_key: str,
    voice_id: str,
    pcm: np.ndarray,
    samplerate: int,
    *,
    model_id: str = DEFAULT_MODEL,
    stability: float = 0.5,
    similarity_boost: float = 0.8,
    output_samplerate: int = 44100,
    chunk_size: int = 4096,
    timeout: int = 60,
) -> Iterator[np.ndarray]:
    """POST PCM to /speech-to-speech/{voice_id}/stream; yield int16 PCM chunks.

    Output is mono PCM at `output_samplerate` (16-bit little-endian).
    """
    wav_bytes = pcm_to_wav_bytes(pcm, samplerate)
    url = f"{API_BASE}/speech-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": api_key, "accept": "*/*"}
    voice_settings = (
        f'{{"stability":{stability},"similarity_boost":{similarity_boost}}}'
    )
    output_format = f"pcm_{output_samplerate}"
    data = {
        "model_id": model_id,
        "voice_settings": voice_settings,
        "output_format": output_format,
        "remove_background_noise": "false",
    }
    files = {"audio": ("input.wav", wav_bytes, "audio/wav")}

    with requests.post(
        url, headers=headers, data=data, files=files, stream=True, timeout=timeout
    ) as r:
        if r.status_code != 200:
            try:
                err = r.json()
            except Exception:
                err = r.text
            raise RuntimeError(f"ElevenLabs API error {r.status_code}: {err}")
        leftover = b""
        for chunk in r.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            data = leftover + chunk
            # ensure even number of bytes for int16
            if len(data) % 2:
                leftover = data[-1:]
                data = data[:-1]
            else:
                leftover = b""
            yield np.frombuffer(data, dtype=np.int16)


def list_voices(api_key: str) -> list[dict]:
    """Fetch user's voices for GUI dropdown."""
    r = requests.get(
        f"{API_BASE}/voices",
        headers={"xi-api-key": api_key},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("voices", [])
```

### `settings.py`

```python
"""Persistent settings — API key, voice ID, last-used devices."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "ElevenVoiceChanger"

if sys.platform == "win32":
    CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
else:
    CONFIG_DIR = Path.home() / ".config" / "eleven-voice-changer"
SETTINGS_FILE = CONFIG_DIR / "settings.json"


def load() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
```

### `requirements.txt`

```
sounddevice>=0.4.6
numpy>=1.26
requests>=2.31
sv-ttk>=2.6
```

### `eleven_voice_changer.spec`

```python
# PyInstaller spec — builds ElevenVoiceChanger.exe (Windows) as one-folder bundle.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

block_cipher = None

hidden = []
datas = []
binaries = []

for mod in (
    "sounddevice",
    "_sounddevice_data",
    "numpy",
    "requests",
    "sv_ttk",
):
    try:
        hidden.extend(collect_submodules(mod))
    except Exception:
        pass

for mod in ("sounddevice", "_sounddevice_data", "certifi", "sv_ttk"):
    try:
        datas.extend(collect_data_files(mod))
    except Exception:
        pass

# PortAudio DLL ships with sounddevice on Windows
try:
    binaries.extend(collect_dynamic_libs("sounddevice"))
except Exception:
    pass

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "deepface", "insightface", "onnxruntime"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ElevenVoiceChanger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ElevenVoiceChanger",
)
```

### `.github/workflows/build-windows.yml`

```yaml
name: Build Windows app

on:
  workflow_dispatch:
  push:
    branches: [main, master]
    paths:
      - "**.py"
      - "eleven_voice_changer.spec"
      - "requirements.txt"
      - ".github/workflows/build-windows.yml"

permissions:
  contents: write

jobs:
  build:
    runs-on: windows-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install deps
        shell: bash
        run: |
          python -m pip install --upgrade pip wheel
          pip install -r requirements.txt
          pip install pyinstaller

      - name: Build with PyInstaller
        shell: bash
        run: pyinstaller --noconfirm eleven_voice_changer.spec

      - name: Show build size
        shell: bash
        run: du -sh dist/ElevenVoiceChanger || true

      - name: Package as ZIP
        shell: pwsh
        run: |
          cd dist
          Compress-Archive -Path ElevenVoiceChanger -DestinationPath ElevenVoiceChanger-windows.zip -CompressionLevel Optimal
          Get-ChildItem ElevenVoiceChanger-windows.zip

      - name: Publish to GitHub Release
        shell: bash
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "build-${{ github.run_number }}" \
            dist/ElevenVoiceChanger-windows.zip \
            --title "Windows build ${{ github.run_number }}" \
            --notes "Standalone Windows build of **ElevenLabs Voice Changer**.

          **Jak użyć:**
          1. Pobierz \`ElevenVoiceChanger-windows.zip\` poniżej.
          2. Rozpakuj. Wejdź do folderu \`ElevenVoiceChanger\` i odpal \`ElevenVoiceChanger.exe\`.
          3. W GUI wpisz **API key** z https://elevenlabs.io/app/settings/api-keys.
          4. Kliknij **Load voices** — wybierz swój wytrenowany głos z listy.
          5. **Output to** ustaw na **CABLE Input (VB-Audio Virtual Cable)** (po zainstalowaniu VB-Cable z https://vb-audio.com/Cable/).
          6. W Telegramie/Discordzie wybierz **CABLE Output** jako input mic.
          7. **Trzymaj** wielki niebieski przycisk (lub spację) podczas mówienia. Po puszczeniu audio leci do ElevenLabs i odtwarza się jako twój wytrenowany głos w wybranym output device → rozmówca słyszy zmieniony głos.

          Ustawienia (API key, voice ID, devices) zapisują się w \`%APPDATA%/ElevenVoiceChanger/settings.json\`."
```

## Endpoints ElevenLabs używane

- `GET https://api.elevenlabs.io/v1/voices` — lista głosów użytkownika (wymaga scope `voices_read`)
- `POST https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}/stream` — STS konwersja, streamowany PCM output (wymaga scope `speech_to_speech`)

Header auth: `xi-api-key: sk_xxxxx`.

Form data dla STS:
- `audio`: WAV file (multipart)
- `model_id`: `eleven_multilingual_sts_v2` lub `eleven_english_sts_v2`
- `voice_settings`: JSON `{"stability": 0.5, "similarity_boost": 0.8}`
- `output_format`: `pcm_44100` (16-bit LE)
- `remove_background_noise`: `"false"` / `"true"`

## Jak debugować lokalnie

```bash
# Mac/Linux
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py

# Windows
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Tkinter pokaże GUI, log w okienku pokaże co się dzieje przy każdym push-to-talk.

## Jak rebuildowac EXE

Commit + push do `main` → GitHub Actions buduje EXE → Release `build-<N>` na repo.

Manualnie: `pyinstaller --noconfirm eleven_voice_changer.spec` (Windows tylko).

## Setup pod Telegram (Windows)

1. Zainstaluj **VB-Audio Cable** (free): https://vb-audio.com/Cable/
2. W EXE — Output to: `CABLE Input (VB-Audio Virtual Cable)`
3. Telegram → Settings → Voice → Input device: `CABLE Output (VB-Audio Virtual Cable)`
4. Trzymaj button / SPACJA i gadaj — Telegram słyszy zmieniony głos przez virtual mic.
