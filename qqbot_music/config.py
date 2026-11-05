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
    "admin": ["store"],
    "tts": ["model"],
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


def set_hf_cache(cfg) -> None:
    """Seed-VC / YingMusic 在 import 时才设 HF_HUB_CACHE（相对路径 ./checkpoints/hf_cache）。
    同一个进程里先加载了说话克隆（它先 import huggingface_hub，缓存目录就定死了），再加载换声会找不到模型。
    所以服务一启动、任何 HF 相关的包导入之前，按换声后端把绝对路径设好。"""
    d = cfg.convert.seedvc_dir if cfg.convert.backend == "seedvc" else cfg.separate.yingmusic_dir
    os.environ["HF_HUB_CACHE"] = os.path.join(d, "checkpoints", "hf_cache")


_cfg = None


def get() -> SimpleNamespace:
    global _cfg
    if _cfg is None:
        _cfg = load()
    return _cfg
