"""唱一首：曲库里分离好的整首主唱 + 伴奏 → 换声 → 后处理 → 混音。副歌从整首里切，伴奏永远不变调。"""

import hashlib
import io
import json
import os

import numpy as np
import soundfile as sf

from .library import Library
from .post import finish

SR = 44100
PAD = 0.3  # 副歌前后各多留一点，免得切掉字头字尾


def cache_key(cfg) -> str:
    """影响唱出来效果的配置变了，旧缓存就不用了。"""
    parts = [vars(cfg.convert), vars(cfg.pitch), vars(cfg.post), cfg.voice.reference, cfg.voice.deess_reference]
    return hashlib.md5(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _read(path: str) -> np.ndarray:
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    if sr != SR:
        import librosa
        y = librosa.resample(y.T, orig_sr=sr, target_sr=SR).T
    return y.T  # (声道, 采样)


def sing(cfg, converter, sid: str, full: bool = False) -> bytes:
    """返回 16 位 WAV。converter 是 convert.Converter。"""
    lib = Library(cfg)
    e = lib.load()[sid]
    cache_dir = os.path.join(lib.dir, "sung")
    cache = os.path.join(cache_dir, f"{sid}_{'full' if full else 'chorus'}_{cache_key(cfg)}.wav")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    d = lib.stem_dir(sid)
    vocal = _read(os.path.join(d, "vocals.flac")).mean(0)
    inst = _read(os.path.join(d, "instrumental.flac"))
    if not full:
        a, b = e["chorus"]
        a, b = int(max(0.0, a - PAD) * SR), int((b + PAD) * SR)
        vocal, inst = vocal[a:b], inst[:, a:b]
    converted, _ = converter(vocal)
    out = finish(converted, vocal, inst, SR, cfg)
    buf = io.BytesIO()
    sf.write(buf, out.T, SR, format="WAV", subtype="PCM_16")
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache, "wb") as f:
        f.write(buf.getvalue())
    return buf.getvalue()
