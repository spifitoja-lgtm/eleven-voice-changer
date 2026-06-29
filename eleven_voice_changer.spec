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
