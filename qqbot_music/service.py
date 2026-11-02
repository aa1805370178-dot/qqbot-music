"""HTTP 唱歌服务，给聊天机器人调（任何框架都行，只要能发 HTTP）。

  GET  /ping                           → ok
  GET  /find?title=歌名&artist=歌手      → {"status": "ready"/"learning"/"failed"/"unknown", "id", "title", "artist", "lyrics", "error"}
  GET  /random?lang=zh|ja&artist=歌手    → 会唱的歌里随机挑一首，格式同 /find
  POST /learn {"title", "artist"}       → 不会就在后台学（子进程：搜索 → 下载 → 整首分离 → 定副歌），马上返回状态
  POST /sing  {"id", "full": false}     → audio/wav；默认只唱副歌

一次只唱一首，其他请求排队。显存小于 8G 时学歌会先卸掉换声模型（分离 + 换声放不下），学完下次唱再加载。
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
        self.learning: dict[str, str] = {}  # norm(歌名) → learning / failed
        self.errors: dict[str, str] = {}
        self.last_used = time.time()

    def _converter(self):
        if self.converter is None:
            free_ports(self.cfg.service.free_ports)
            from .convert import Converter
            log(f"加载换声模型（{self.cfg.convert.backend}）")
            self.converter = Converter(self.cfg)
        return self.converter

    def _unload(self) -> None:
        import torch
        if self.converter is not None and torch.cuda.is_available() and torch.cuda.get_device_properties(0).total_memory < 8 * 2**30:
            self.converter = None
            import gc
            gc.collect()
            torch.cuda.empty_cache()
            log("显存不够同时放分离和换声，先卸掉换声模型")

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
            self.learning[norm(title)] = "learning"
            threading.Thread(target=self._learn_bg, args=(title, artist), daemon=True).start()
            st = {"status": "learning"}
        return st

    def _learn_bg(self, title: str, artist: str) -> None:
        """子进程学：分离器和换声后端的代码有同名包，不能放一个进程里。"""
        key = norm(title)
        with self.lock:
            self._unload()
            r = subprocess.run([sys.executable, "-m", "qqbot_music", "learn", title, artist],
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
            else:
                self._reply(404, b"", "text/plain")

        def do_POST(self) -> None:
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "body 要是 JSON"}, 400)
            if self.path == "/learn":
                return self._json(svc.learn(str(body.get("title", "")).strip(), str(body.get("artist", "")).strip()))
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

        def log_message(self, *args) -> None:
            pass

    return Handler


def serve(cfg) -> None:
    svc = Service(cfg)
    s = cfg.service
    server = ThreadingHTTPServer((s.host, s.port), make_handler(svc))

    def watch_idle() -> None:
        while s.idle_minutes:
            time.sleep(60)
            busy = svc.lock.locked() or "learning" in svc.learning.values()
            if not busy and time.time() - svc.last_used > s.idle_minutes * 60:
                log(f"闲置 {s.idle_minutes} 分钟，退出让出显存")
                server.shutdown()
                return

    threading.Thread(target=watch_idle, daemon=True).start()
    log(f"唱歌服务就绪：http://{s.host}:{s.port}")
    server.serve_forever()
