"""换声前处理原唱音高（f0，单位 Hz，0 = 无声）。伴奏不变调，所以人声只做整八度的挪动，挪了也还在调上。

1. fix_octave_jumps：音高提取器偶尔把一帧测高/低一个八度，换出来就是一声怪叫。
   跟前后 0.4 秒有声帧的中位差了 900 音分以上的帧，折回那个八度。
2. move_phrases：原唱太低（男声低音给女声唱会变成挤出来的公鸭嗓）或太高的乐句，整句挪一个八度。
   乐句 = 有声段之间空隙不超过 phrase_gap 秒的连成一句；一句的音高中位离参考音的中位超过 move_semitones 个半音，
   就往参考音那边挪一个八度。按整句挪而不是按音挪，句子里的旋律走向不会被拆散。

只用 numpy，和用哪个换声模型无关。
"""

import numpy as np


def _cents(f0: np.ndarray) -> np.ndarray:
    c = np.full(f0.shape, np.nan)
    v = f0 > 1
    c[v] = 1200 * np.log2(f0[v] / 440.0)
    return c


def fix_octave_jumps(f0: np.ndarray, fps: float = 100.0, window_s: float = 0.4, limit_cents: float = 900.0) -> np.ndarray:
    f0 = np.asarray(f0, dtype=np.float64).copy()
    c = _cents(f0)
    w = int(window_s * fps)
    fixed = c.copy()
    for i in np.where(f0 > 1)[0]:
        seg = c[max(0, i - w):i + w]
        med = np.nanmedian(seg)
        while fixed[i] - med > limit_cents:
            fixed[i] -= 1200
        while med - fixed[i] > limit_cents:
            fixed[i] += 1200
    v = f0 > 1
    f0[v] = 440.0 * 2 ** (fixed[v] / 1200)
    return f0


def phrases(f0: np.ndarray, fps: float = 100.0, gap_s: float = 0.25) -> list[tuple[int, int]]:
    """有声帧连成乐句，返回 [(起始帧, 结束帧)]（含结束帧）。"""
    idx = np.where(np.asarray(f0) > 1)[0]
    if not len(idx):
        return []
    cuts = np.where(np.diff(idx) > gap_s * fps)[0]
    return [(int(seg[0]), int(seg[-1])) for seg in np.split(idx, cuts + 1)]


def move_phrases(f0: np.ndarray, target_hz: float, move_semitones: float = 9.0, fps: float = 100.0,
                 gap_s: float = 0.25) -> tuple[np.ndarray, list[dict]]:
    """返回 (挪过的 f0, 每句的记录 [{起, 止, 原中位Hz, 挪了几个八度}])。"""
    f0 = np.asarray(f0, dtype=np.float64).copy()
    report = []
    for a, b in phrases(f0, fps, gap_s):
        seg = f0[a:b + 1]
        med = float(np.median(seg[seg > 1]))
        dist = 12 * np.log2(med / target_hz)  # 离参考音多少个半音，正 = 偏高
        octaves = -1 if dist > move_semitones else 1 if dist < -move_semitones else 0
        if octaves:
            seg[seg > 1] *= 2.0 ** octaves
        report.append({"起": a / fps, "止": b / fps, "原中位Hz": round(med), "八度": octaves})
    return f0, report


def median_hz(f0: np.ndarray) -> float:
    v = np.asarray(f0)[np.asarray(f0) > 1]
    return float(np.median(v)) if len(v) else 0.0


def prepare(f0: np.ndarray, target_hz: float, cfg) -> tuple[np.ndarray, list[dict]]:
    """按配置 [pitch] 做完整的处理。cfg 有 move_semitones、phrase_gap、fix_octave_jumps。"""
    if cfg.fix_octave_jumps:
        f0 = fix_octave_jumps(f0)
    return move_phrases(f0, target_hz, cfg.move_semitones, gap_s=cfg.phrase_gap)
