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
