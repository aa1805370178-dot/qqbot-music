"""分离：YingMusic-SVC 自带的 BS-Roformer 三轨分离器（主唱 / 和声 / 伴奏），只留主唱和伴奏。

不用它的 inference.py（会带上训练依赖 loralib、wandb 等），只借模型定义和 demix。
半精度：有 Tensor Core 的卡（算力 7.0 以上，GTX 16 系除外）才开；GTX 1650 开了反而慢 3–4 倍。
"""

import os
import sys
import types

import numpy as np
import torch

SR = 44100


def load_audio(path: str, sr: int = SR) -> np.ndarray:
    """任意格式 → (2, 采样) float32 立体声。优先 PyAV（不需要装 ffmpeg），没装就用 librosa。"""
    try:
        import av
    except ImportError:
        import librosa
        y = librosa.load(path, sr=sr, mono=False)[0]
    else:
        with av.open(path) as c:
            rs = av.AudioResampler(format="fltp", layout="stereo", rate=sr)
            frames = [fr.to_ndarray() for frame in c.decode(audio=0) for fr in rs.resample(frame)]
            frames += [fr.to_ndarray() for fr in rs.resample(None)]
        y = np.concatenate(frames, axis=1)
    return np.stack([y, y]) if y.ndim == 1 else y.astype(np.float32)


def _use_half(setting, device: str) -> bool:
    if isinstance(setting, bool):
        return setting
    if device != "cuda":
        return False
    major, _ = torch.cuda.get_device_capability()
    return major >= 7 and "GTX 16" not in torch.cuda.get_device_name()


class Separator:
    def __init__(self, cfg):
        s = cfg.separate
        root = os.path.join(s.yingmusic_dir, "accom_separation")
        if not os.path.isdir(root):
            raise FileNotFoundError(f"[separate] yingmusic_dir 下没有 accom_separation：{s.yingmusic_dir}"
                                    "（git clone https://github.com/GiantAILab/YingMusic-SVC）")
        if not os.path.exists(s.checkpoint):
            raise FileNotFoundError(f"[separate] 找不到 checkpoint：{s.checkpoint}（ModelScope giantailab/YingMusic-SVC 的 bs_roformer.ckpt）")
        sys.path.insert(0, root)
        for name in ("loralib", "utils.muon"):  # model_utils 顶部 import 的训练用模块，分离用不到
            sys.modules.setdefault(name, types.SimpleNamespace(SingleDeviceMuonWithAuxAdam=None))
        import yaml
        from ml_collections import ConfigDict
        from models.bs_roformer import BSRoformer
        from utils.model_utils import demix
        self._demix = demix

        self.device = ("cuda" if torch.cuda.is_available() else "cpu") if s.device == "auto" else s.device
        with open(os.path.join(root, "ckpt", "bs_roformer", "config_bd_roformer.yaml"), encoding="utf-8") as f:
            self.mcfg = ConfigDict(yaml.load(f, Loader=yaml.FullLoader))
        self.mcfg.training.use_amp = _use_half(s.half, self.device)
        self.mcfg.inference.batch_size = s.batch_size
        self.mcfg.inference.num_overlap = s.num_overlap
        if self.device == "cpu":
            torch.set_num_threads(os.cpu_count())
        self.model = BSRoformer(**dict(self.mcfg.model))
        sd = torch.load(s.checkpoint, map_location="cpu", weights_only=True)
        self.model.load_state_dict(sd.get("state", sd.get("state_dict", sd)))
        self.model.to(self.device).eval()

    @torch.no_grad()
    def __call__(self, mix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(2, 采样) 44.1k → (主唱 单声道, 伴奏 (2, 采样))。和声那轨不要（不垫和声）。"""
        res = self._demix(self.mcfg, self.model, mix, torch.device(self.device), model_type="bs_roformer", pbar=False)
        return res["vocals"].mean(0), res["instrumental"]

    def unload(self) -> None:
        """分离完让出显存给换声（4G 卡上两个放不下）。"""
        self.model.cpu()
        del self.model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
