"""一键安装的第二段（装好 Python 环境之后由 install.ps1 调用）：下载模型 + 自检。

  python install/setup_models.py [--with-yingmusic]

1. 分离模型 bs_roformer.ckpt：ModelScope（国内直连）下载，断点续传，校验 sha256。
2. 换声要的 9 个文件（Seed-VC、Whisper、BigVGAN、RMVPE、CAMPPlus）：也从 ModelScope 下，
   按 sha256 核对过和 Hugging Face 官方的是同一个文件，直接放进 Seed-VC 找模型的 Hugging Face 缓存目录结构里。
   ModelScope 不通才去 huggingface.co 下（要能连上，一般得开代理）。hf-mirror.com 现在会把模型请求跳回 huggingface.co，不用它。
3. 说话克隆模型 Qwen3-TTS 0.6B Base：ModelScope 官方仓库，同样逐个校验 sha256，放在 models/。
4. 离线加载一遍换声模型、说话克隆念一句、分离器分离 3 秒静音，确认显卡和环境都能跑。
5. 全部通过写 env/install_ok.json，启动脚本看到它就开离线模式（不再联网检查模型）。

可以重复跑：已经下好的不会重下。
"""

import hashlib
import json
import os
import subprocess
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDVC = os.path.join(ROOT, "third_party", "seed-vc")
YING = os.path.join(ROOT, "third_party", "YingMusic-SVC")
MS_REPO = "giantailab/YingMusic-SVC"

# (Seed-VC 下的缓存目录, HF 仓库, HF 版本, 文件名, ModelScope 仓库, ModelScope 里的路径, sha256)
# sha256 和作者机器上从 Hugging Face 下的文件逐个核对过。Seed-VC 用 hf_hub_download / from_pretrained 找模型，
# 按 HF 缓存结构（models--作者--仓库/snapshots/<版本>/文件 + refs/main）放好，离线模式下就直接用。
SEEDVC_FILES = [
    ("checkpoints", "Plachta/Seed-VC", "257283f9f41585055e8f858fba4fd044e5caed6e",
     "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth", "AI-ModelScope/SeedVC",
     "Seed-VC/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
     "42aef93ffe65857c840d270252fa040f7ba04514945ec460f3ac1ac2a96de684"),
    ("checkpoints", "Plachta/Seed-VC", "257283f9f41585055e8f858fba4fd044e5caed6e",
     "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml", "dxhklck/Seed-VC", "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
     "ff86f343b08c2def405f075022eeb10dafba23c4eda36d151010f4ca6d7a1a0a"),
    ("checkpoints", "lj1995/VoiceConversionWebUI", "e6d0c1a17da07c33557852f9dfa2bd44cc75737d",
     "rmvpe.pt", "jaman21/rmvpe", "rmvpe.pt",
     "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"),
    ("checkpoints", "funasr/campplus", "e4b6ede7ce16997aff4ae69fbca1f0175e2afede",
     "campplus_cn_common.bin", "iic/speech_campplus_sv_zh-cn_16k-common", "campplus_cn_common.bin",
     "3388cf5fd3493c9ac9c69851d8e7a8badcfb4f3dc631020c4961371646d5ada8"),
    ("checkpoints/hf_cache", "openai/whisper-small", "973afd24965f72e36ca33b3055d56a652f456b4d",
     "config.json", "openai-mirror/whisper-small", "config.json",
     "e6a2b489da1b5aed65a8eb8d1e7466fa867ad5643a8bc138ba708bd56b2875c4"),
    ("checkpoints/hf_cache", "openai/whisper-small", "973afd24965f72e36ca33b3055d56a652f456b4d",
     "model.safetensors", "openai-mirror/whisper-small", "model.safetensors",
     "1d7734884874f1a1513ed9aa760a4f8e97aaa02fd6d93a3a85d27b2ae9ca596b"),
    ("checkpoints/hf_cache", "openai/whisper-small", "973afd24965f72e36ca33b3055d56a652f456b4d",
     "preprocessor_config.json", "openai-mirror/whisper-small", "preprocessor_config.json",
     "9b5cd03a36fbb8a627c64d98a5b5b126ead95a77720723944487311f0110b666"),
    ("checkpoints/hf_cache", "nvidia/bigvgan_v2_44khz_128band_512x", "95a9d1dcb12906c03edd938d77b9333d6ded7dfb",
     "bigvgan_generator.pt", "nv-community/bigvgan_v2_44khz_128band_512x", "bigvgan_generator.pt",
     "d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357"),
    ("checkpoints/hf_cache", "nvidia/bigvgan_v2_44khz_128band_512x", "95a9d1dcb12906c03edd938d77b9333d6ded7dfb",
     "config.json", "nv-community/bigvgan_v2_44khz_128band_512x", "config.json",
     "65b7f487bfaf15256056a75f6667ef5f640214c981922a111927873705ea4ddb"),
]


# 说话克隆：Qwen3-TTS 0.6B Base（Apache-2.0），自带 speech_tokenizer，不用另下 Tokenizer 仓库。
# (文件, sha256)，和作者机器上跑通的那份逐个核对过；ModelScope 上是 Qwen 官方账号传的。
TTS_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
TTS_DIR = os.path.join(ROOT, "models", "Qwen3-TTS-12Hz-0.6B-Base")
TTS_FILES = [
    ("config.json", "2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011"),
    ("configuration.json", "c502b6328c67638b401df8dd05de89e9e8d1cff9cd0ada10dfbdbe13556c20de"),
    ("generation_config.json", "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa"),
    ("merges.txt", "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    ("model.safetensors", "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6"),
    ("preprocessor_config.json", "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119"),
    ("tokenizer_config.json", "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670"),
    ("vocab.json", "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
    ("speech_tokenizer/config.json", "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167"),
    ("speech_tokenizer/configuration.json", "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd"),
    ("speech_tokenizer/model.safetensors", "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
    ("speech_tokenizer/preprocessor_config.json", "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb"),
]


def tts_models() -> None:
    for name, sha in TTS_FILES:
        download([f"https://modelscope.cn/models/{TTS_REPO}/resolve/master/{name}",
                  f"https://huggingface.co/{TTS_REPO}/resolve/main/{name}"], os.path.join(TTS_DIR, name), sha)


def seedvc_models() -> None:
    for cache, repo, commit, name, ms_repo, ms_path, sha in SEEDVC_FILES:
        base = os.path.join(SEEDVC, cache, "models--" + repo.replace("/", "--"))
        out = os.path.join(base, "snapshots", commit, name)
        download([f"https://modelscope.cn/models/{ms_repo}/resolve/master/{ms_path}",
                  f"https://huggingface.co/{repo}/resolve/{commit}/{name}"], out, sha)
        os.makedirs(os.path.join(base, "refs"), exist_ok=True)
        with open(os.path.join(base, "refs", "main"), "w") as f:
            f.write(commit)

http = requests.Session()
http.trust_env = True  # 用户自己开了代理就用


def say(msg: str) -> None:
    print(msg, flush=True)


def modelscope_sha(name: str) -> str:
    try:
        r = http.get(f"https://modelscope.cn/api/v1/models/{MS_REPO}/repo/files", params={"Recursive": "true"}, timeout=20)
        for f in r.json()["Data"]["Files"]:
            if f["Path"] == name:
                return f.get("Sha256", "")
    except Exception as ex:  # 拿不到就不校验，只看大小
        say(f"  （拿不到校验值：{ex}）")
    return ""


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def download(urls: list[str], out: str, expect_sha: str = "") -> None:
    """断点续传下载，失败换下一个地址，每个地址试 3 次。"""
    if os.path.exists(out) and (not expect_sha or sha256(out) == expect_sha):
        say(f"  已有 {os.path.basename(out)}，跳过")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    part = out + ".part"
    for url in urls:
        for attempt in range(3):
            try:
                have = os.path.getsize(part) if os.path.exists(part) else 0
                headers = {"Range": f"bytes={have}-"} if have else {}
                with http.get(url, headers=headers, stream=True, timeout=30) as r:
                    if r.status_code == 416:  # 已经下完
                        break
                    r.raise_for_status()
                    if have and r.status_code != 206:  # 服务器不支持续传，从头来
                        have = 0
                    total = have + int(r.headers.get("Content-Length") or 0)
                    t0, last = time.time(), 0.0
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            have += len(chunk)
                            if time.time() - last > 2:
                                speed = (have - (total - int(r.headers.get("Content-Length") or 0))) / max(time.time() - t0, 1e-3)
                                say(f"  {os.path.basename(out)}  {have / 2**20:.0f}/{total / 2**20:.0f} MB  {speed / 2**20:.1f} MB/s")
                                last = time.time()
                break
            except Exception as ex:
                say(f"  下载出错（{url.split('/')[2]}，第 {attempt + 1} 次）：{ex}")
                time.sleep(3)
        else:
            continue
        if expect_sha and sha256(part) != expect_sha:
            say("  校验不对，删掉重下")
            os.remove(part)
            continue
        os.replace(part, out)
        say(f"  下好了 {os.path.basename(out)}")
        return
    raise RuntimeError(f"{os.path.basename(out)} 下载失败。网络不好可以挂代理后重新双击 安装.bat（会接着下）")


def separator_ckpt(name: str) -> None:
    out = os.path.join(YING, "ckpt", name)
    download([f"https://modelscope.cn/models/{MS_REPO}/resolve/master/{name}",
              f"https://huggingface.co/GiantAILab/YingMusic-SVC/resolve/main/{name}"], out, modelscope_sha(name))


def run_check(title: str, code: str, cwd: str, env_extra: dict) -> bool:
    say(f"  {title}…")
    env = {**os.environ, "PYTHONUTF8": "1", **env_extra}
    r = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env)
    return r.returncode == 0


SEEDVC_CHECK = """
import torch
from types import SimpleNamespace
import inference as sv
sv.load_models(SimpleNamespace(f0_condition=True, checkpoint=None, config=None, fp16=True))
print('  换声模型加载成功，设备：' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'))
"""

SEPARATOR_CHECK = """
import numpy as np
from qqbot_music import config
from qqbot_music.separate import Separator
cfg = config.load()
v, i = Separator(cfg)(np.zeros((2, 44100 * 3), dtype='float32'))
print('  分离 3 秒静音成功')
"""


TTS_CHECK = """
import os, tempfile
from types import SimpleNamespace
import numpy as np, soundfile as sf
from qqbot_music import config
from qqbot_music.tts import TTS
cfg = config.load()
ref = os.path.join(tempfile.gettempdir(), 'qqbot_music_tts_check.wav')
t = np.arange(44100 * 3) / 44100
sf.write(ref, (0.3 * np.sin(2 * np.pi * 220 * t)).astype('float32'), 44100)
cfg.voice = SimpleNamespace(**{**vars(cfg.voice), 'reference': ref})
wav, mode = TTS(cfg)('你好。')
os.remove(ref)
print('  说话克隆模型能用')
"""


def main() -> None:
    with_ying = "--with-yingmusic" in sys.argv
    import torch
    cuda = torch.cuda.is_available()
    say(f"torch {torch.__version__}，显卡：{torch.cuda.get_device_name(0) if cuda else '无（用 CPU，会很慢）'}")

    say("\n[模型 1/4] 分离模型 bs_roformer.ckpt（1.3G，ModelScope）")
    separator_ckpt("bs_roformer.ckpt")
    if with_ying:
        say("  YingMusic 换声模型 YingMusic-SVC-full.pt（730M）")
        separator_ckpt("YingMusic-SVC-full.pt")

    say("\n[模型 2/4] 换声模型（约 2.4G，ModelScope）")
    seedvc_models()
    if not run_check("离线加载换声模型", SEEDVC_CHECK, SEEDVC, {"HF_HUB_OFFLINE": "1"}):
        raise RuntimeError("换声模型加载失败。把上面的报错发 issue")

    say("\n[模型 3/4] 说话克隆模型 Qwen3-TTS 0.6B（约 2.5G，ModelScope）")
    tts_models()
    if not run_check("加载说话克隆模型、念一句", TTS_CHECK, ROOT, {"HF_HUB_OFFLINE": "1"}):
        raise RuntimeError("说话克隆模型自检失败，把上面的报错发 issue")

    say("\n[模型 4/4] 分离器自检")
    if not run_check("加载分离器、分离 3 秒静音", SEPARATOR_CHECK, ROOT, {"HF_HUB_OFFLINE": "1"}):
        raise RuntimeError("分离器自检失败，把上面的报错发 issue")

    info = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "torch": torch.__version__, "cuda": cuda,
            "gpu": torch.cuda.get_device_name(0) if cuda else "", "yingmusic": with_ying}
    with open(os.path.join(ROOT, "env", "install_ok.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    say("\n模型和自检都通过了。")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        say(f"\n出错了：{ex}")
        sys.exit(1)
