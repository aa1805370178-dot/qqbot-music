"""HTTP 唱歌服务，给聊天机器人调（任何框架都行，只要能发 HTTP）。

  GET  /ping                           → ok
  GET  /find?title=歌名&artist=歌手      → {"status": "ready"/"learning"/"failed"/"unknown", "id", "title", "artist", "lyrics", "error"}
  GET  /random?lang=zh|ja&artist=歌手    → 会唱的歌里随机挑一首，格式同 /find
  POST /learn {"title", "artist"}       → 不会就在后台学（子进程：搜索 → 下载 → 整首分离 → 定副歌），马上返回状态
  POST /sing  {"id", "full": false}     → audio/wav；默认只唱副歌
  POST /add   {"path", "title", "artist"} → 导入本机的音频文件，后台学（不联网下载），状态同 /learn
  GET  /learning                       → {"learning": [歌名], "failed": {歌名: 原因}}（管理页用）
  POST /reload                         → 音色录音换了，重新算参考音（管理页用）
  POST /cover {"path", "name"}         → 本机一个音频文件整首翻唱（不进曲库），后台做，马上返回 {"id"}；
  POST /cover {"id"}                   → 曲库里会唱的歌整首翻唱（用分离好的音轨，不用再分离），同上；
                                         进度和结果在曲库目录 covers/<id>.json、covers/<id>.wav
  POST /tts   {"text"}                 → audio/wav：用音色录音克隆的声音念这段字（Qwen3-TTS），响应头 X-TTS-Mode: icl / xvec

一次只用一个模型，其他请求排队。显存小于 8G 时换声、说话克隆、分离轮流占显存：要用哪个先卸掉别的，下次用到再加载。
闲置 [service] idle_minutes 分钟自己退出。
"""

import json
import os
import random
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config
from .library import Library, norm


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def free_ports(ports: list[int]) -> None:
    """结束监听这些端口的进程（比如同卡上的 TTS 服务），给换声让显存。"""
    for port in ports:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout
            for line in out.splitlines():
                p = line.split()
                if len(p) >= 5 and p[1].endswith(f":{port}") and p[3] == "LISTENING":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", p[4]], capture_output=True)
                    log(f"结束端口 {port} 上的进程 {p[4]}")
        else:
            subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lib = Library(cfg)
        self.lock = threading.Lock()  # 一次只用一次 GPU（唱或学）
        self.converter = None
        self.tts = None
        self.learning: dict[str, str] = {}  # norm(歌名) → learning / failed
        self.errors: dict[str, str] = {}
        self.titles: dict[str, str] = {}  # norm(歌名) → 原样的歌名（管理页显示）
        self.last_used = time.time()
        self.covers_running = 0
        self._mark_stale_covers()

    @staticmethod
    def _small_gpu() -> bool:
        import torch
        return torch.cuda.is_available() and torch.cuda.get_device_properties(0).total_memory < 8 * 2**30

    def _drop(self, *names: str) -> None:
        """小显存：卸掉这些模型（converter / tts），给要用的腾地方。"""
        names = [n for n in names if getattr(self, n) is not None]
        if not names or not self._small_gpu():
            return
        import gc
        import torch
        for n in names:
            setattr(self, n, None)
        gc.collect()
        torch.cuda.empty_cache()
        label = {"converter": "换声", "tts": "说话克隆"}
        log("显存不够同时放，先卸掉" + "、".join(label[n] for n in names) + "模型")

    def _converter(self):
        if self.converter is None:
            self._drop("tts")
            free_ports(self.cfg.service.free_ports)
            from .convert import Converter
            log(f"加载换声模型（{self.cfg.convert.backend}）")
            self.converter = Converter(self.cfg)
        return self.converter

    def _tts(self):
        if self.tts is None:
            self._drop("converter")
            free_ports(self.cfg.service.free_ports)
            from .tts import TTS
            log("加载说话克隆模型（Qwen3-TTS）")
            self.tts = TTS(self.cfg)
        return self.tts

    def _unload(self) -> None:
        """分离在子进程里跑，先把本进程占的显存让出来。"""
        self._drop("converter", "tts")

    def say(self, text: str) -> tuple[bytes, str]:
        with self.lock:
            try:
                t = time.time()
                data, mode = self._tts()(text)
                log(f"说话克隆 {len(text)} 字（{mode}），用时 {time.time() - t:.0f}s")
                return data, mode
            finally:
                self.last_used = time.time()

    # ── 上传整首翻唱 ──────────────────────────────────

    def _covers_dir(self) -> str:
        d = os.path.join(self.lib.dir, "covers")
        os.makedirs(d, exist_ok=True)
        return d

    def _cover_meta(self, cid: str, **fields) -> None:
        p = os.path.join(self._covers_dir(), cid + ".json")
        meta = {}
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                meta = json.load(f)
        meta.update(fields)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def _mark_stale_covers(self) -> None:
        """上次服务退出时没做完的，标成失败（不然管理页一直显示在做）。"""
        d = os.path.join(self.lib.dir, "covers")
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if f.endswith(".json"):
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    meta = json.load(fh)
                if meta.get("status") in ("queued", "separating", "converting"):
                    self._cover_meta(f[:-5], status="failed", error="服务中途退出了，重新上传再试")

    def cover(self, path: str, name: str) -> dict:
        if not os.path.isfile(path):
            return {"error": "文件不存在"}
        return self._new_cover(name or os.path.basename(path), self._cover_file, path)

    def cover_song(self, sid: str) -> dict:
        e = self.lib.load().get(sid)
        if not e or e.get("status") != "ready":
            return {"error": f"曲库里没有会唱的 {sid}"}
        name = e["title"] + (f" - {e['artist']}" if e.get("artist") else "")
        return self._new_cover(name, self._cover_song, sid, sid=sid)

    def _new_cover(self, name: str, job, arg: str, **meta) -> dict:
        cid = time.strftime("%Y%m%d_%H%M%S") + f"_{random.randint(100, 999)}"
        self._cover_meta(cid, name=name, status="queued", error="", created=time.time(), **meta)
        self.covers_running += 1
        threading.Thread(target=self._cover_run, args=(cid, job, arg), daemon=True).start()
        return {"id": cid}

    def _cover_run(self, cid: str, job, arg: str) -> None:
        t = time.time()
        try:
            with self.lock:
                duration = job(cid, arg)
            self._cover_meta(cid, status="done", seconds=round(time.time() - t), duration=duration)
            log(f"整首翻唱 {cid} 做好了，用时 {time.time() - t:.0f}s")
        except Exception as ex:
            self._cover_meta(cid, status="failed", error=f"{type(ex).__name__}: {ex}"[:300])
            log(f"整首翻唱 {cid} 失败：{ex}")
        finally:
            self.covers_running -= 1
            self.last_used = time.time()

    def _cover_song(self, cid: str, sid: str) -> float:
        """曲库里的歌：音轨已经分离好，直接整首换声（结果也进唱过的缓存，QQ 里要整首能直接用）。"""
        import soundfile as sf
        from .sing import sing
        self._cover_meta(cid, status="converting")
        data = sing(self.cfg, self._converter(), sid, full=True)
        out = os.path.join(self._covers_dir(), cid + ".wav")
        with open(out, "wb") as f:
            f.write(data)
        return round(sf.info(out).duration, 1)

    def _cover_file(self, cid: str, path: str) -> float:
        """上传的文件：先分离（子进程），再换声、混音。"""
        import tempfile
        import soundfile as sf
        from .post import finish
        self._unload()
        self._cover_meta(cid, status="separating")
        tmp = tempfile.mkdtemp(prefix="qqbot_music_cover_")
        r = subprocess.run([sys.executable, "-m", "qqbot_music", "separate", path, tmp], cwd=config.ROOT,
                           env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if r.returncode != 0:
            raise RuntimeError("分离失败：" + (r.stdout + r.stderr).strip()[-300:])
        self._cover_meta(cid, status="converting")
        vocal = sf.read(os.path.join(tmp, "vocals.flac"), dtype="float32")[0]
        inst = sf.read(os.path.join(tmp, "instrumental.flac"), dtype="float32")[0].T
        converted, _ = self._converter()(vocal)
        out = finish(converted, vocal, inst, 44100, self.cfg)
        sf.write(os.path.join(self._covers_dir(), cid + ".wav"), out.T, 44100, subtype="PCM_16")
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
        return round(len(vocal) / 44100, 1)

    def reload_voice(self) -> None:
        """管理页换了音色录音（或录音文字）：已加载的模型重新算参考音（没加载的下次加载时自然用新的）。"""
        with self.lock:
            if self.converter is not None:
                self.converter.set_reference(self.cfg.voice.reference)
            if self.tts is not None:
                self.tts.reset_reference()
            log("音色录音换了，已重新加载")

    def entry(self, sid: str) -> dict:
        e = self.lib.load()[sid]
        return {"status": "ready", "id": sid, "title": e["title"], "artist": e["artist"], "lyrics": e.get("lyrics", [])}

    def status(self, title: str, artist: str = "") -> dict:
        sid = self.lib.find(title, artist)
        if sid and self.lib.load()[sid]["status"] == "ready":
            return self.entry(sid)
        st = self.learning.get(norm(title))
        if st == "learning" or sid and self.lib.load()[sid]["status"] == "pending":
            return {"status": "learning"}
        if st == "failed":
            return {"status": "failed", "error": self.errors.get(norm(title), "")}
        return {"status": "unknown"}

    def random(self, lang: str = "", artist: str = "") -> dict:
        songs = self.lib.ready(lang, artist)
        return self.entry(random.choice(songs)) if songs else {"status": "unknown"}

    def learn(self, title: str, artist: str = "") -> dict:
        st = self.status(title, artist)
        if st["status"] in ("unknown", "failed") and title:  # 上次学不会的，再点就再学一次
            self._start(title, artist, ["learn", title, artist])
            st = {"status": "learning"}
        return st

    def add(self, path: str, title: str, artist: str = "") -> dict:
        """导入本地音频：同样放后台子进程学，和学歌共用一把锁。"""
        if not (title and os.path.isfile(path)):
            return {"status": "failed", "error": "要有歌名和存在的音频文件"}
        if self.learning.get(norm(title)) != "learning":
            self._start(title, artist, ["add", path, title, artist])
        return {"status": "learning"}

    def learning_state(self) -> dict:
        return {"learning": [self.titles.get(k, k) for k, v in self.learning.items() if v == "learning"],
                "failed": {self.titles.get(k, k): self.errors.get(k, "") for k, v in self.learning.items() if v == "failed"}}

    def _start(self, title: str, artist: str, cmd: list[str]) -> None:
        self.learning[norm(title)] = "learning"
        self.titles[norm(title)] = title
        threading.Thread(target=self._learn_bg, args=(title, artist, cmd), daemon=True).start()

    def _learn_bg(self, title: str, artist: str, cmd: list[str]) -> None:
        """子进程学：分离器和换声后端的代码有同名包，不能放一个进程里。"""
        key = norm(title)
        with self.lock:
            self._unload()
            r = subprocess.run([sys.executable, "-m", "qqbot_music", *cmd],
                               cwd=config.ROOT, env={**os.environ, "PYTHONUTF8": "1"},
                               capture_output=True, text=True, encoding="utf-8", errors="ignore")
            self.last_used = time.time()
        sid = self.lib.find(title, artist)
        ok = bool(sid) and self.lib.load()[sid]["status"] == "ready"
        self.learning[key] = "ready" if ok else "failed"
        if not ok:
            e = self.lib.load().get(sid, {}) if sid else {}
            self.errors[key] = e.get("error") or (r.stdout + r.stderr).strip()[-300:]
        log(f"学「{title}」：{'会了' if ok else '失败 ' + self.errors[key]}")

    def sing(self, sid: str, full: bool) -> bytes:
        from .sing import sing
        with self.lock:
            try:
                t = time.time()
                data = sing(self.cfg, self._converter(), sid, full)
                log(f"唱 {sid}{'（整首）' if full else ''}，用时 {time.time() - t:.0f}s")
                return data
            finally:
                self.last_used = time.time()


def make_handler(svc: Service):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: dict, code: int = 200) -> None:
            self._reply(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/ping":
                self._reply(200, b"ok", "text/plain")
            elif u.path == "/find":
                self._json(svc.status(q.get("title", ""), q.get("artist", "")))
            elif u.path == "/random":
                self._json(svc.random(q.get("lang", ""), q.get("artist", "")))
            elif u.path == "/learning":
                self._json(svc.learning_state())
            else:
                self._reply(404, b"", "text/plain")

        def do_POST(self) -> None:
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "body 要是 JSON"}, 400)
            if self.path == "/learn":
                return self._json(svc.learn(str(body.get("title", "")).strip(), str(body.get("artist", "")).strip()))
            if self.path == "/cover":
                if body.get("id"):
                    return self._json(svc.cover_song(str(body["id"])))
                return self._json(svc.cover(str(body.get("path", "")), str(body.get("name", "")).strip()))
            if self.path == "/tts":
                return self._say(str(body.get("text", "")).strip())
            if self.path == "/reload":
                svc.reload_voice()
                return self._json({"ok": True})
            if self.path == "/add":
                return self._json(svc.add(str(body.get("path", "")), str(body.get("title", "")).strip(),
                                          str(body.get("artist", "")).strip()))
            if self.path != "/sing":
                return self._reply(404, b"", "text/plain")
            sid = str(body.get("id", ""))
            if svc.lib.load().get(sid, {}).get("status") != "ready":
                return self._json({"error": f"曲库里没有会唱的 {sid}"}, 400)
            try:
                audio = svc.sing(sid, bool(body.get("full")))
            except Exception as ex:  # 唱失败告诉调用方，服务本身不退
                return self._json({"error": f"{type(ex).__name__}: {ex}"[:300]}, 500)
            self._reply(200, audio, "audio/wav")

        def _say(self, text: str) -> None:
            from .tts import available
            if not text or len(text) > svc.cfg.tts.max_chars:
                return self._json({"error": f"文字要在 1–{svc.cfg.tts.max_chars} 个字之间"}, 400)
            if not available(svc.cfg):
                return self._json({"error": "没有说话克隆模型（重新双击 安装.bat 会补下）"}, 400)
            if not os.path.isfile(svc.cfg.voice.reference):
                return self._json({"error": "还没有音色录音"}, 400)
            try:
                audio, mode = svc.say(text)
            except Exception as ex:
                return self._json({"error": f"{type(ex).__name__}: {ex}"[:300]}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("X-TTS-Mode", mode)
            self.end_headers()
            self.wfile.write(audio)

        def log_message(self, *args) -> None:
            pass

    return Handler


def serve(cfg) -> None:
    config.set_hf_cache(cfg)  # 先于任何 HF 包导入，见 config.set_hf_cache
    svc = Service(cfg)
    s = cfg.service
    server = ThreadingHTTPServer((s.host, s.port), make_handler(svc))

    def watch_idle() -> None:
        while s.idle_minutes:
            time.sleep(60)
            busy = svc.lock.locked() or "learning" in svc.learning.values() or svc.covers_running > 0
            if not busy and time.time() - svc.last_used > s.idle_minutes * 60:
                log(f"闲置 {s.idle_minutes} 分钟，退出让出显存")
                server.shutdown()
                return

    threading.Thread(target=watch_idle, daemon=True).start()
    log(f"唱歌服务就绪：http://{s.host}:{s.port}")
    server.serve_forever()
