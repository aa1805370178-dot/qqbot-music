"""换声：单声道 44.1k 人声 → 参考音的音色。两个后端共用一套推理循环（YingMusic-SVC 本来就是从 Seed-VC 改的）：

- seedvc：Seed-VC 的 seed-uvit-whisper-base（f0 版，44.1k），15 步、不开 CFG 就够用，快。
- yingmusic：YingMusic-SVC-full.pt，多了风格残差，100 步音色更像，慢 4 倍左右。

两个仓库都有顶层的 modules 包，一个进程只能加载一个后端。
音高不交给模型自己定：先测原唱 f0，用 pitch.py 处理过再送进去（伴奏不变调，所以只挪整八度）。
"""

import os
import sys
import types
from types import SimpleNamespace

import librosa
import numpy as np
import torch
import torchaudio

from . import pitch
from .post import deess

SR, HOP = 44100, 512
MAX_CONTEXT = SR // HOP * 30  # 参考音 + 每段原唱共 30 秒
OVERLAP = 16


class Converter:
    def __init__(self, cfg):
        self.cfg = cfg
        c = cfg.convert
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.style = c.backend == "yingmusic"  # YingMusic 的 length_regulator / cfm 多一个风格残差
        if self.style:
            (self.model, self.semantic_fn, self.f0_fn, self.vocoder_fn, self.campplus, self.mel_fn, _) = _load_yingmusic(cfg, self.device)
        elif c.backend == "seedvc":
            (self.model, self.semantic_fn, self.f0_fn, self.vocoder_fn, self.campplus, self.mel_fn, _) = _load_seedvc(cfg)
        else:
            raise ValueError(f"[convert] backend 只能是 seedvc 或 yingmusic，现在是 {c.backend!r}")
        self.semantic_no_cudnn = False
        self.set_reference(cfg.voice.reference)

    @torch.no_grad()
    def set_reference(self, path: str) -> None:
        """参考音只算一次（截前 25 秒）。"""
        y = librosa.load(path, sr=SR)[0][:SR * 25]
        if self.cfg.voice.deess_reference:
            y = deess(y, SR, self.cfg.post.deess_limit)
        ref = torch.tensor(y).unsqueeze(0).float().to(self.device)
        ref16 = torchaudio.functional.resample(ref, SR, 16000)
        self.mel2 = self.mel_fn(ref.float())
        feat = torchaudio.compliance.kaldi.fbank(ref16, num_mel_bins=80, dither=0, sample_frequency=16000)
        self.style2 = self.campplus((feat - feat.mean(dim=0, keepdim=True)).unsqueeze(0))
        f0_ref = self.f0_fn(ref16[0], thred=0.03)
        self.ref_hz = pitch.median_hz(f0_ref)
        out = self.model.length_regulator(self._whisper(ref16), ylens=torch.LongTensor([self.mel2.size(2)]).to(self.device),
                                          n_quantizers=3, f0=torch.from_numpy(f0_ref).to(self.device)[None], **self._style_kw())
        self.prompt = out[0]
        self.style_prompt = out[-1] if self.style else None

    def _style_kw(self) -> dict:
        return {"style": self.style2, "return_style_residual": True} if self.style else {}

    def _whisper(self, x16: torch.Tensor) -> torch.Tensor:
        """Whisper 编码器（半精度）。torch 2.7.1 自带的 cuDNN 9.7 在 GTX 1650 Ti 上半精度 Conv1d 会整段出 NaN（2.6 的 cuDNN 9.1 正常），
        换出来是满幅噪音；别的显卡没法逐个验证，所以第一次（算参考音时）发现 NaN 就换成不用 cuDNN 重算，之后一直这样。"""
        if not self.semantic_no_cudnn:
            s = self.semantic_fn(x16)
            if not torch.isnan(s).any():
                return s
            print("[convert] 这块显卡上 Whisper 用 cuDNN 算出 NaN，改成不用 cuDNN 算（只影响 Whisper 这一步）", flush=True)
            self.semantic_no_cudnn = True
        with torch.backends.cudnn.flags(enabled=False):
            return self.semantic_fn(x16)

    def _semantic(self, src16: torch.Tensor) -> torch.Tensor:
        if src16.size(-1) <= 16000 * 30:
            return self._whisper(src16)
        parts, buf, pos = [], None, 0  # Whisper 一次最多 30 秒：重叠 5 秒分段
        while pos < src16.size(-1):
            chunk = src16[:, pos:pos + 16000 * 30] if buf is None else torch.cat([buf, src16[:, pos:pos + 16000 * 25]], dim=-1)
            s = self._whisper(chunk)
            parts.append(s if pos == 0 else s[:, 50 * 5:])
            buf = chunk[:, -16000 * 5:]
            pos += 30 * 16000 if pos == 0 else chunk.size(-1) - 16000 * 5
        return torch.cat(parts, dim=1)

    def _f0(self, x16: torch.Tensor) -> np.ndarray:
        """RMVPE 一次吃整首时显存随歌长涨（5 分钟的歌峰值 +1.5G）：按 30 秒一段算，每段两边多带 1 秒上下文再裁掉。
        RMVPE 每 160 个采样一帧（center=True 末尾多一帧），拼回来和整首一次算的帧数一样。"""
        n, seg, ctx, hop = x16.size(-1), 16000 * 30, 16000, 160
        if n <= seg * 2:
            return self.f0_fn(x16, thred=0.03)
        parts = []
        for a in range(0, n, seg):
            b = min(a + seg, n)
            lo, hi = max(0, a - ctx), min(n, b + ctx)
            f = self.f0_fn(x16[lo:hi], thred=0.03)
            s = (a - lo) // hop
            parts.append(f[s:s + (b - a) // hop + (1 if b == n else 0)])
        return np.concatenate(parts)

    @torch.no_grad()
    def __call__(self, vocal: np.ndarray) -> tuple[np.ndarray, list[dict]]:
        """返回 (换好的人声, 音高处理记录)。"""
        c = self.cfg.convert
        src = torch.tensor(vocal).unsqueeze(0).float().to(self.device)
        src16 = torchaudio.functional.resample(src, SR, 16000)
        s_alt = self._semantic(src16)
        mel = self.mel_fn(src.float())
        f0, report = pitch.prepare(self._f0(src16[0]), self.ref_hz, self.cfg.pitch)
        f0 = torch.from_numpy(f0).float().to(self.device)[None]
        out = self.model.length_regulator(s_alt, ylens=torch.LongTensor([mel.size(2)]).to(self.device), n_quantizers=3,
                                          f0=f0, **self._style_kw())
        cond, style_cond = out[0], (out[-1] if self.style else None)
        # 整首的中间结果用完就丢、缓存还给显卡：不然 4G 卡后面分块时会挤进共享内存，整首慢一倍多
        del src, src16, s_alt, mel, f0, out
        if self.device.type == "cuda":
            torch.cuda.empty_cache()

        window = MAX_CONTEXT - self.mel2.size(2)
        overlap_wave = OVERLAP * HOP
        chunks, prev, done = [], None, 0
        while done < cond.size(1):
            cat = torch.cat([self.prompt, cond[:, done:done + window]], dim=1)
            kw = {}
            if self.style:
                kw["style_r"] = torch.cat([self.style_prompt, style_cond[:, done:done + window]], dim=1)
            last = done + window >= cond.size(1)
            with torch.autocast(device_type=self.device.type, dtype=torch.float16, enabled=c.fp16):
                mel_out = self.model.cfm.inference(cat, torch.LongTensor([cat.size(1)]).to(self.device), self.mel2,
                                                   self.style2, None, c.steps, inference_cfg_rate=c.cfg_rate, **kw)
            mel_out = mel_out[:, :, self.mel2.size(-1):]
            wave = self.vocoder_fn(mel_out.float()).squeeze().cpu().numpy()
            body = wave if last else wave[:-overlap_wave]
            chunks.append(body if prev is None else _crossfade(prev, body, overlap_wave))
            if last:
                break
            prev = wave[-overlap_wave:]
            done += mel_out.size(2) - OVERLAP
        return np.concatenate(chunks), report


def _crossfade(a: np.ndarray, b: np.ndarray, n: int) -> np.ndarray:
    b = b.copy()
    n = min(n, len(b))
    fade_out = np.cos(np.linspace(0, np.pi / 2, len(a))) ** 2
    b[:n] = b[:n] * (1 - fade_out[:n]) + (a * fade_out)[:n]
    return b


def _load_seedvc(cfg):
    d = cfg.convert.seedvc_dir
    if not os.path.isdir(d):
        raise FileNotFoundError(f"[convert] seedvc_dir 不存在：{d}（git clone https://github.com/Plachtaa/seed-vc）")
    os.chdir(d)  # Seed-VC 按相对路径找 checkpoints
    sys.path.insert(0, d)
    import inference as sv
    return sv.load_models(SimpleNamespace(f0_condition=True, checkpoint=None, config=None, fp16=cfg.convert.fp16))


def _load_yingmusic(cfg, device):
    d = cfg.separate.yingmusic_dir
    if not os.path.isdir(d):
        raise FileNotFoundError(f"[convert] yingmusic_dir 不存在：{d}（git clone https://github.com/GiantAILab/YingMusic-SVC）")
    os.chdir(d)  # 它把 HF 缓存设在 ./checkpoints
    sys.path.insert(0, d)
    sys.modules["Remix.auger"] = types.SimpleNamespace(echo_then_reverb_save=None)  # 它的混音要 sox，我们自己混
    import my_inference as mi
    import yaml
    with open(os.path.join(d, "configs", "YingMusic-SVC.yml"), encoding="utf-8") as f:
        # 上游 load_models_api 用了没定义的 bigvgan_cache（写完 from_pretrained(bigvgan_name) 又加载一次），补上同一个名字
        mi.bigvgan_cache = yaml.safe_load(f)["model_params"]["vocoder"]["name"]
    args = SimpleNamespace(checkpoint=cfg.convert.yingmusic_checkpoint, config=os.path.join(d, "configs", "YingMusic-SVC.yml"),
                           fp16=cfg.convert.fp16)
    return mi.load_models_api(args, device=device)
