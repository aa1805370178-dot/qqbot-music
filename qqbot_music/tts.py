"""说话克隆：Qwen3-TTS 0.6B Base 用 [voice] reference 那段录音克隆音色，把一段文字念出来——先听听克隆得像不像，再拿去唱。

有 N 卡用 faster-qwen3-tts（CUDA Graph 加速，GTX 1650 Ti 上 RTF 约 1），没有就用官方 qwen_tts 在 CPU 上跑（很慢）。
录音旁边放一个同名 .txt，写上录音里说的话 → 完整克隆（录音放进上下文，最像）；
没写，或者录音超过 [tts] icl_max_seconds → 只用音色特征（x-vector），不用对文字，像的程度差一点。
"""

import io
import os

import soundfile as sf
import torch


def ref_text_path(cfg) -> str:
    return os.path.splitext(cfg.voice.reference)[0] + ".txt"


def ref_text(cfg) -> str:
    p = ref_text_path(cfg)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def mode(cfg) -> str:
    """icl = 录音 + 文字一起用；xvec = 只用音色特征。"""
    if not ref_text(cfg) or not os.path.isfile(cfg.voice.reference):
        return "xvec"
    return "icl" if sf.info(cfg.voice.reference).duration <= cfg.tts.icl_max_seconds else "xvec"


def _stub_sox() -> None:
    """qwen_tts 导入时无条件 import sox（只有用不到的 25Hz tokenizer 要它）。
    真的 sox 包一导入就去找 sox.exe，找不到打一大段吓人的警告，这里放个空模块顶上。"""
    import importlib.machinery
    import sys
    import types
    if "sox" not in sys.modules:
        m = types.ModuleType("sox")
        m.__spec__ = importlib.machinery.ModuleSpec("sox", None)
        sys.modules["sox"] = m


def available(cfg) -> bool:
    return os.path.isfile(os.path.join(cfg.tts.model, "model.safetensors"))


class TTS:
    def __init__(self, cfg):
        self.cfg = cfg
        t = cfg.tts
        self.fast = torch.cuda.is_available()
        _stub_sox()
        if self.fast:
            from faster_qwen3_tts import FasterQwen3TTS
            self.model = FasterQwen3TTS.from_pretrained(t.model, dtype=torch.bfloat16, max_seq_len=t.max_seq_len)
        else:
            from qwen_tts import Qwen3TTSModel
            self.model = Qwen3TTSModel.from_pretrained(t.model, device_map="cpu", dtype=torch.float32)

    def reset_reference(self) -> None:
        """录音换了：faster-qwen3-tts 按文件路径缓存参考音，同一个路径换了内容要清掉。"""
        cache = getattr(self.model, "_voice_prompt_cache", None)
        if isinstance(cache, dict):
            cache.clear()

    @torch.no_grad()
    def __call__(self, text: str) -> tuple[bytes, str]:
        """返回 (16 位 WAV, 用的模式)。"""
        m = mode(self.cfg)
        kw = dict(text=text, language=self.cfg.tts.language, ref_audio=self.cfg.voice.reference)
        if self.fast:
            wavs, sr = self.model.generate_voice_clone(**kw, ref_text=ref_text(self.cfg) if m == "icl" else "", xvec_only=m == "xvec")
        else:
            wavs, sr = self.model.generate_voice_clone(**kw, ref_text=ref_text(self.cfg) if m == "icl" else None,
                                                       x_vector_only_mode=m == "xvec")
        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
        return buf.getvalue(), m
