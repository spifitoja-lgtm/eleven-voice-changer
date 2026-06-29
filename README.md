# ElevenLabs Voice Changer

Push-to-talk voice modulator pod **Windows** wykorzystujący **ElevenLabs Speech-to-Speech** API.
Trzymasz przycisk, mówisz, puszczasz — twój głos leci do ElevenLabs i wraca jako **twój wytrenowany głos** (lub dowolny z biblioteki ElevenLabs).
Output kierujesz na **VB-Audio Cable** → Telegram/Discord/Zoom słyszy zmieniony głos.

## Setup (Windows)

1. **Pobierz EXE** z [Releases](../../releases) — `ElevenVoiceChanger-windows.zip`. Rozpakuj, odpal `ElevenVoiceChanger.exe`.

2. **Zainstaluj virtual mic** — [VB-Audio Cable](https://vb-audio.com/Cable/) (free). To stworzy "CABLE Input" (gdzie EXE wypycha audio) i "CABLE Output" (skąd Telegram odbiera).

3. **API key ElevenLabs** — wejdź na https://elevenlabs.io/app/settings/api-keys, skopiuj. W aplikacji wpisz w pole *API key*. Klucz zapisuje się lokalnie w `%APPDATA%/ElevenVoiceChanger/settings.json`.

4. **Wytrenuj głos w ElevenLabs**:
   - https://elevenlabs.io/app/voice-lab → **Add a New Voice** → *Instant Voice Cloning*
   - Wgraj 1-3 min próbkę audio osoby której głosem chcesz mówić
   - Po wytrenowaniu zobaczysz voice_id na karcie głosu

5. **W aplikacji**:
   - Kliknij **Load voices** → wybierz głos z dropdownu (albo wklej voice_id przez **Manual ID**)
   - **Input mic** = twój prawdziwy mikrofon
   - **Output to** = `CABLE Input (VB-Audio Virtual Cable)`

6. **W Telegramie** (Settings → Voice & Video):
   - **Input device** = `CABLE Output (VB-Audio Virtual Cable)`
   - **Output device** = twoje słuchawki (żebyś nie słyszał własnego echa)

7. **Klik na dzwon i gadaj** — trzymasz wielki niebieski przycisk (lub `SPACJA`), mówisz, puszczasz. Po ~0.5-1.5s rozmówca słyszy twój wytrenowany głos.

## Modele i ustawienia

- **Model**: `eleven_multilingual_sts_v2` (default) lub `eleven_english_sts_v2` (PL bierz multilingual).
- **Stability** (0-1, default 0.5): wyższe = stabilniejsze ale bardziej "monotonne".
- **Similarity** (0-1, default 0.8): wyższe = bliżej trenowanego głosu, niższe = więcej naturalności.

## Limitacje

- **Latencja**: ~500ms-1.5s na chunka. Push-to-talk model — nie pełny duplex. Dla casualowej rozmowy OK, dla szybkiej wymiany zdań niezręczne.
- **Credit burn**: każda sekunda STS = ~30-60 creditsy z planu ElevenLabs. Plan Starter (30k/mc) = ~10-15 min rozmów / dzień.
- **Mac / Linux**: kod działa cross-platform (sounddevice + sv_ttk), ale EXE budujemy tylko dla Windows. Na Macu uruchom z venv (`python app.py`) + BlackHole jako virtual mic.

## Uruchomienie z kodu (dev)

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

## Privacy

API key + voice_id + ustawienia urządzeń zapisują się **lokalnie** w `%APPDATA%/ElevenVoiceChanger/settings.json` (Windows) lub `~/.config/eleven-voice-changer/settings.json` (Mac/Linux). Plain JSON — nikt poza tobą nie ma dostępu.

Audio leci tylko: lokalny mikrofon → ElevenLabs API → lokalne audio out. Brak innych third-party.
