"""换声后的处理：去齿音、噪声门、和伴奏混音。只用 numpy / librosa，pedalboard 可选。"""

import numpy as np
import librosa


def deess(y: np.ndarray, sr: int, limit: float = 20.0) -> np.ndarray:
    """齿音（s/x/sh）压下去：按帧看 5–11 kHz 能量占比，超过 limit% 的帧把 4.5 kHz 以上压回 limit%（相邻帧平滑）。
    参考音齿音偏尖时，换声会把它放大成持续的「滋滋」声；参考音和换声结果各过一遍。"""
    n_fft, hop = 2048, 512
    X = librosa.stft(y, n_fft=n_fft, hop_length=hop)
    P = np.abs(X) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    ratio = P[(f > 5000) & (f < 11000)].sum(0) / (P[(f > 80) & (f < 11000)].sum(0) + 1e-12)
    r = limit / 100
    g2 = np.ones_like(ratio)
    m = ratio > r
    g2[m] = r * (1 - ratio[m]) / (ratio[m] * (1 - r))  # 高频乘 g2 后占比正好是 r
    g = np.convolve(np.sqrt(np.clip(g2, 0.05, 1)), np.ones(3) / 3, "same")
    G = np.ones_like(P)
    G[f > 4500] = g
    return librosa.istft(X * G, hop_length=hop, length=len(y))


def gate(converted: np.ndarray, vocal: np.ndarray, sr: int, gate_db: float = -40, hold: int = 8) -> np.ndarray:
    """原唱主唱安静的地方把换声结果压掉：分离漏进主唱轨的伴奏会被换成杂音（间奏、句间最明显）。
    20ms 一帧，低于峰值 gate_db 就静音，前后各放宽 hold 帧、10ms 渐变，免得切掉字头字尾。"""
    hop = sr // 50
    n = min(len(converted), len(vocal))
    frames = n // hop
    level = np.sqrt(np.mean(vocal[:frames * hop].reshape(frames, hop) ** 2, axis=1)) + 1e-9
    keep = 20 * np.log10(level / level.max()) > gate_db
    keep = np.convolve(keep.astype(float), np.ones(2 * hold + 1), mode="same") > 0
    mask = np.convolve(np.repeat(keep.astype(float), hop), np.ones(hop // 2) / (hop // 2), mode="same")
    out = converted.copy()
    out[:len(mask)] *= mask
    out[len(mask):] = 0
    return out


def effects(y: np.ndarray, sr: int) -> np.ndarray:
    """高通 100Hz → 轻压缩 → 小混响。没装 pedalboard 就原样返回。"""
    try:
        from pedalboard import Compressor, HighpassFilter, Pedalboard, Reverb
    except ImportError:
        return y
    board = Pedalboard([HighpassFilter(100), Compressor(threshold_db=-18, ratio=2.5, attack_ms=5, release_ms=120),
                        Reverb(room_size=0.25, wet_level=0.12, dry_level=0.9, width=0.8)])
    return board(y.astype(np.float32)[None], sr)[0]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))) + 1e-9)


def mix(vocal: np.ndarray, instrumental: np.ndarray, over_db: float = 1.0) -> np.ndarray:
    """vocal 单声道、instrumental (声道, 采样)。人声响度按伴奏定：比伴奏响 over_db dB。
    不按原唱主唱响度对齐，因为分离器有时把主唱分走一部分到和声轨，原唱主唱轨本身就偏小。返回 (声道, 采样)，峰值不超过 0.95。"""
    n = min(len(vocal), instrumental.shape[1])
    vocal = vocal[:n] * (_rms(instrumental[:, :n]) * 10 ** (over_db / 20) / _rms(vocal[:n]))
    out = instrumental[:, :n] + vocal[None]
    return out / max(1.0, np.abs(out).max() / 0.95)


def finish(converted: np.ndarray, vocal: np.ndarray, instrumental: np.ndarray, sr: int, cfg) -> np.ndarray:
    """按配置 [post] 做完：去齿音 → 噪声门 → 效果 → 混伴奏。"""
    p = cfg.post
    if p.deess:
        converted = deess(converted, sr, p.deess_limit)
    converted = gate(converted, vocal, sr, p.gate_db)
    if p.effects:
        converted = effects(converted, sr)
    return mix(converted, instrumental, p.vocal_over_instrumental_db)
