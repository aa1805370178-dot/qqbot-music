"""配置：config.example.toml 是默认值，config.toml（或 QQBOT_MUSIC_CONFIG 指定的文件）覆盖它。"""

import os
from types import SimpleNamespace

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10（Seed-VC 的环境）
    import tomli as tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH_KEYS = {
    "voice": ["reference"],
    "library": ["dir"],
    "separate": ["yingmusic_dir", "checkpoint"],
    "convert": ["seedvc_dir", "yingmusic_checkpoint"],
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: str | None = None) -> SimpleNamespace:
    with open(os.path.join(ROOT, "config.example.toml"), "rb") as f:
        data = tomllib.load(f)
    path = path or os.environ.get("QQBOT_MUSIC_CONFIG") or os.path.join(ROOT, "config.toml")
    base_dir = ROOT
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = _merge(data, tomllib.load(f))
        base_dir = os.path.dirname(os.path.abspath(path))
    for section, keys in PATH_KEYS.items():
        for k in keys:
            v = data[section][k]
            if v and not os.path.isabs(v):
                data[section][k] = os.path.normpath(os.path.join(base_dir, v))
    return SimpleNamespace(**{k: SimpleNamespace(**v) for k, v in data.items()})


_cfg = None


def get() -> SimpleNamespace:
    global _cfg
    if _cfg is None:
        _cfg = load()
    return _cfg
