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
        if pcm is None or len(pcm) < CAPTURE_SAMPLERATE // 4:  # < 0.25s
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
        threading.Thread(target=self._send_worker, args=(api, voice_id, pcm), daemon=True).start()

    def _send_worker(self, api: str, voice_id: str, pcm) -> None:
        out_index = self._get_output_index()
        model = self.model_var.get()
        stability = float(self.stability_var.get())
        similarity = float(self.similarity_var.get())
        try:
            self.log_q.put(f"[api] sending {len(pcm) / CAPTURE_SAMPLERATE:.1f}s of audio…")
            with Player(out_index, samplerate=OUTPUT_SAMPLERATE) as player:
                total = 0
                for chunk in convert_stream(
                    api, voice_id, pcm, CAPTURE_SAMPLERATE,
                    model_id=model,
                    stability=stability,
                    similarity_boost=similarity,
                    output_samplerate=OUTPUT_SAMPLERATE,
                ):
                    player.write(chunk)
                    total += len(chunk)
            secs = total / OUTPUT_SAMPLERATE
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
