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
