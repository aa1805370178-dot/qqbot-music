"""命令行：python -m qqbot_music <命令>

  serve                         启动 HTTP 唱歌服务（给机器人调）
  admin [--open]                启动管理页（曲库、学歌、试唱、B 站 cookie；初始密码 123456），--open 顺便打开浏览器
  learn 歌名 [歌手]              搜原唱版本、下载、整首分离、定副歌
  add 音频文件 歌名 [歌手]        导入本地音频（不联网下载）
  process                       把曲库里 pending 的都学完
  find 歌名 [歌手]               查会不会唱
  sing id 输出.wav [--full]      唱一首（默认只唱副歌）
  cover 音频文件 输出.wav         不进曲库，直接把一首歌翻唱成参考音的音色（整首）
  separate 音频文件 输出目录       只分离，得到 vocals.flac + instrumental.flac

配置：config.toml（没有就用 config.example.toml），或者环境变量 QQBOT_MUSIC_CONFIG 指定。
"""

import json
import os
import sys

import soundfile as sf

from . import config
from .library import Library

SR = 44100


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cfg = config.get()
    cmd, args = argv[0], argv[1:]
    lib = Library(cfg)
    if cmd == "serve":
        from .service import serve
        serve(cfg)
    elif cmd == "admin":
        from .admin import serve as serve_admin
        serve_admin(cfg, "--open" in args)
    elif cmd == "learn":
        try:
            print(lib.learn(args[0], args[1] if len(args) > 1 else ""))
        except LookupError as ex:
            print(ex)
            sys.exit(2)
    elif cmd == "add":
        print(lib.add(args[0], args[1], args[2] if len(args) > 2 else ""))
    elif cmd == "process":
        lib.process_pending()
    elif cmd == "find":
        sid = lib.find(args[0], args[1] if len(args) > 1 else "")
        print(json.dumps({sid: lib.load()[sid]} if sid else {}, ensure_ascii=False, indent=1))
    elif cmd == "sing":
        from .convert import Converter
        from .sing import sing
        data = sing(cfg, Converter(cfg), args[0], "--full" in args)
        with open(args[1], "wb") as f:
            f.write(data)
    elif cmd == "separate":
        from .separate import Separator, load_audio
        vocals, inst = Separator(cfg)(load_audio(args[0]))
        os.makedirs(args[1], exist_ok=True)
        sf.write(os.path.join(args[1], "vocals.flac"), vocals, SR)
        sf.write(os.path.join(args[1], "instrumental.flac"), inst.T, SR)
    elif cmd == "cover":
        # 分离和换声的代码有同名包，分离放子进程
        import subprocess
        import tempfile
        from .convert import Converter
        from .post import finish
        src, out = os.path.abspath(args[0]), os.path.abspath(args[1])
        tmp = tempfile.mkdtemp(prefix="qqbot_music_")
        subprocess.run([sys.executable, "-m", "qqbot_music", "separate", src, tmp], cwd=config.ROOT, check=True)
        vocal = sf.read(os.path.join(tmp, "vocals.flac"), dtype="float32")[0]
        inst = sf.read(os.path.join(tmp, "instrumental.flac"), dtype="float32")[0].T
        converted, report = Converter(cfg)(vocal)
        moved = [r for r in report if r["八度"]]
        print(f"{len(report)} 句，挪了八度的 {len(moved)} 句")
        sf.write(out, finish(converted, vocal, inst, SR, cfg).T, SR, subtype="PCM_16")
    else:
        print(f"没有这个命令：{cmd}\n{__doc__}")
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
