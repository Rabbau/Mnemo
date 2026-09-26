"""App settings stored in data/config.json and detection of Obsidian vaults."""
import json
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "vault_path": "",
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "",
    "embed_model": "nomic-embed-text",
    "temperature": 0.4,
    "context_chunks": 6,
    "context_length": 8192,   # num_ctx for the chat model; 8k fits a 7B model into 6 GB of VRAM
    "chat_provider": "ollama",  # "ollama" (local) or "cloud" (OpenAI-compatible API)
    "cloud_preset": "openrouter",
    "cloud": {},              # preset -> {"base_url", "api_key", "model"}
}


def cloud_profile(cfg: dict, preset: str | None = None) -> dict:
    preset = preset or cfg.get("cloud_preset") or "openrouter"
    prof = dict((cfg.get("cloud") or {}).get(preset) or {})
    prof.setdefault("base_url", "")
    prof.setdefault("api_key", "")
    prof.setdefault("model", "")
    return prof


def public(cfg: dict) -> dict:
    """Config for the browser: API keys are never sent back, only a masked hint."""
    out = {k: v for k, v in cfg.items() if k != "cloud"}
    out["cloud"] = {}
    for preset, prof in (cfg.get("cloud") or {}).items():
        key = prof.get("api_key") or ""
        out["cloud"][preset] = {
            "base_url": prof.get("base_url", ""), "model": prof.get("model", ""),
            "has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) >= 8 else ("задан" if key else ""),
        }
    return out


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _obsidian_config_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", "")) / "obsidian"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian"
    return Path.home() / ".config" / "obsidian"


def detect_obsidian_vaults() -> list:
    """Vaults known to the Obsidian app (read from its obsidian.json)."""
    try:
        data = json.loads((_obsidian_config_dir() / "obsidian.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    for v in (data.get("vaults") or {}).values():
        p = v.get("path")
        if p and Path(p).is_dir():
            found.append({"path": p, "name": Path(p).name, "ts": v.get("ts", 0)})
    found.sort(key=lambda x: x["ts"], reverse=True)
    return found
