"""管理页：python -m qqbot_music admin [--open]

浏览器里看曲库、学新歌、导入本地音频、试唱、改副歌、删歌、填 B 站 cookie。要登录（初始密码 123456）。
不碰显卡：学歌、试唱都转给唱歌服务（[service] 端口），服务没开就替你拉起来，和机器人共用同一个服务、同一把锁。
"""

import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import config, local_store
from .library import Library, language

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html")
SESSION_HOURS = 24 * 7
MAX_UPLOAD = 200 * 2**20
AUDIO_TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg"}
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 连本机服务不走系统代理


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class SingService:
    """唱歌服务的客户端；没开就拉起来（独立进程，管理页关了它照样给机器人用，闲置自己退）。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.url = f"http://{'127.0.0.1' if cfg.service.host in ('0.0.0.0', '') else cfg.service.host}:{cfg.service.port}"

    def call(self, path: str, body: dict | None = None, timeout: float = 10):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with _direct.open(req, timeout=timeout) as r:
            raw = r.read()
            return raw if r.headers.get_content_type().startswith("audio/") else json.loads(raw or b"{}")

    def tts(self, text: str) -> tuple[bytes, str]:
        """说话克隆：返回 (WAV, 模式 icl / xvec)。第一次要加载模型，慢。"""
        req = urllib.request.Request(self.url + "/tts", data=json.dumps({"text": text}, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
        with _direct.open(req, timeout=900) as r:
            return r.read(), r.headers.get("X-TTS-Mode", "")

    def alive(self) -> bool:
        try:
            with _direct.open(self.url + "/ping", timeout=0.8) as r:
                return r.read() == b"ok"
        except (OSError, urllib.error.URLError):
            return False

    def ensure(self, wait: float = 120) -> None:
        if self.alive():
            return
        os.makedirs(self.cfg.library.dir, exist_ok=True)  # 刚装好还没学过歌时曲库目录不存在
        logf = open(os.path.join(self.cfg.library.dir, "service_log.txt"), "ab")
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, "-m", "qqbot_music", "serve"], cwd=config.ROOT, stdout=logf, stderr=logf,
                         env={**os.environ, "PYTHONUTF8": "1"}, creationflags=flags)
        log("唱歌服务没开，已拉起")
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(2)
            if self.alive():
                return
        raise RuntimeError("唱歌服务没起来，看曲库目录下的 service_log.txt")


class Admin:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lib = Library(cfg)
        self.svc = SingService(cfg)
        self.sessions: dict[str, float] = {}
        self.fails: list[float] = []  # 最近登录失败的时间，防暴力试密码

    # ── 登录 ──────────────────────────────────────────

    def login(self, password: str) -> str | None:
        now = time.time()
        self.fails = [t for t in self.fails if now - t < 60]
        if len(self.fails) >= 5:
            raise PermissionError("密码错太多次，一分钟后再试")
        if not local_store.check_password(self.cfg, password):
            self.fails.append(now)
            return None
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + SESSION_HOURS * 3600
        return token

    def authed(self, token: str) -> bool:
        exp = self.sessions.get(token or "")
        return bool(exp and exp > time.time())

    # ── 曲库 ──────────────────────────────────────────

    def sung_file(self, sid: str, full: bool = False) -> str | None:
        from .sing import cache_key
        p = os.path.join(self.lib.dir, "sung", f"{sid}_{'full' if full else 'chorus'}_{cache_key(self.cfg)}.wav")
        return p if os.path.exists(p) else None

    def songs(self) -> dict:
        lib = self.lib.load()
        out = []
        for sid, e in lib.items():
            out.append({"id": sid, "title": e.get("title", ""), "artist": e.get("artist", ""), "status": e.get("status", ""),
                        "duration": e.get("duration"), "chorus": e.get("chorus"), "chorus_by": e.get("chorus_by", ""),
                        "lyrics": e.get("lyrics") or [], "lang": language(e), "source": e.get("source", ""),
                        "error": e.get("error", ""), "audio": bool(self.lib.audio_path(sid)),
                        "sung": bool(self.sung_file(sid))})
        alive = self.svc.alive()
        state = {"learning": [], "failed": {}}
        if alive:
            try:
                state = self.svc.call("/learning")
            except (OSError, ValueError, urllib.error.URLError):
                pass
        return {"songs": out, "service": {"alive": alive, "url": self.svc.url, **state}}

    def edit(self, sid: str, title: str, artist: str, chorus) -> None:
        lib = self.lib.load()
        if sid not in lib:
            raise KeyError("曲库里没有这首")
        e = lib[sid]
        fields = {"title": title or e["title"], "artist": artist}
        if chorus and list(map(float, chorus)) != list(map(float, e.get("chorus") or [0, 0])):
            a, b = float(chorus[0]), float(chorus[1])
            dur = float(e.get("duration") or 1e9)
            if not (0 <= a < b <= dur + 0.5 and 5 <= b - a <= 120):
                raise ValueError("副歌起止不对：要在歌曲时长内，长度 5–120 秒")
            fields.update(chorus=[round(a, 2), round(b, 2)], chorus_by="manual")
            self._drop_sung(sid, "chorus")
        self.lib.update(sid, **fields)

    def delete(self, sid: str) -> None:
        lib = self.lib.load()
        if sid not in lib:
            raise KeyError("曲库里没有这首")
        title = lib[sid].get("title", "")
        audio = self.lib.audio_path(sid)
        if audio:
            os.remove(audio)
        stems = self.lib.stem_dir(sid)
        for f in os.listdir(stems) if os.path.isdir(stems) else []:
            os.remove(os.path.join(stems, f))
        if os.path.isdir(stems):
            os.rmdir(stems)
        self._drop_sung(sid)
        lib = self.lib.load()  # 重新读，免得盖掉服务同时写的
        lib.pop(sid, None)
        self.lib.save(lib)
        log(f"删除 {sid} {title}")

    def _drop_sung(self, sid: str, kind: str = "") -> None:
        d = os.path.join(self.lib.dir, "sung")
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if f.startswith(f"{sid}_{kind}"):
                os.remove(os.path.join(d, f))

    def save_upload(self, name: str, data: bytes) -> str:
        d = os.path.join(self.lib.dir, "uploads")
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):  # 学完就复制进 audio/ 了，上传的原件留一天
            p = os.path.join(d, f)
            if time.time() - os.path.getmtime(p) > 86400:
                os.remove(p)
        base, ext = os.path.splitext(os.path.basename(name))
        if ext.lower() not in AUDIO_TYPES and ext.lower() not in (".aac", ".opus", ".webm", ".mp4"):
            raise ValueError("只收音频文件（mp3 / m4a / flac / wav / ogg 等）")
        safe = re.sub(r"[^\w.-]+", "_", base)[:60]
        path = os.path.join(d, f"{int(time.time())}_{safe}{ext.lower()}")
        with open(path, "wb") as f:
            f.write(data)
        return os.path.abspath(path)

    # ── 上传整首翻唱 ──────────────────────────────────

    def covers_dir(self) -> str:
        return os.path.join(self.lib.dir, "covers")

    def covers(self) -> list[dict]:
        d, out = self.covers_dir(), []
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        meta = json.load(fh)
                except (OSError, ValueError):
                    continue
                out.append({"id": f[:-5], **meta})
        return sorted(out, key=lambda m: m.get("created", 0), reverse=True)

    def cover_file(self, cid: str) -> str | None:
        if not re.fullmatch(r"[\w-]+", cid or ""):
            return None
        p = os.path.join(self.covers_dir(), cid + ".wav")
        return p if os.path.exists(p) else None

    def delete_cover(self, cid: str) -> None:
        if not re.fullmatch(r"[\w-]+", cid or ""):
            raise KeyError("没有这个翻唱")
        meta = next((c for c in self.covers() if c["id"] == cid), None)
        if meta and meta.get("status") in ("queued", "separating", "converting"):
            raise ValueError("还在做，做完再删")
        for ext in (".wav", ".json"):
            p = os.path.join(self.covers_dir(), cid + ext)
            if os.path.exists(p):
                os.remove(p)

    # ── 音色录音 ──────────────────────────────────────

    def voice_info(self) -> dict:
        from . import tts
        path = self.cfg.voice.reference
        base = {"path": path, "tts": tts.available(self.cfg), "text": tts.ref_text(self.cfg),
                "icl_max": self.cfg.tts.icl_max_seconds, "max_chars": self.cfg.tts.max_chars}
        if not os.path.exists(path):
            return {"set": False, **base}
        import soundfile as sf
        try:
            info = sf.info(path)
            dur = round(info.frames / info.samplerate, 1)
        except RuntimeError:
            dur = None
        return {"set": True, **base, "duration": dur, "mode": tts.mode(self.cfg),
                "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))}

    def save_voice_text(self, text: str) -> dict:
        """录音里说的话（说话克隆用）：填了就是完整克隆，空的就删掉。"""
        from . import tts
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 500:
            raise ValueError("太长了：录音里说的话，最多 500 字")
        p = tts.ref_text_path(self.cfg)
        if text:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        elif os.path.exists(p):
            os.remove(p)
        self._reload_service()
        return self.voice_info()

    def _reload_service(self) -> None:
        if self.svc.alive():
            try:
                self.svc.call("/reload", {}, timeout=600)
            except (OSError, ValueError, urllib.error.URLError):
                pass

    def save_voice(self, name: str, data: bytes) -> dict:
        """任意格式 → 44.1k 单声道 WAV，存到 [voice] reference。原来的留一份 .bak.wav。"""
        import tempfile
        import numpy as np
        import soundfile as sf
        from .separate import load_audio
        ext = os.path.splitext(name)[1].lower() or ".wav"
        fd, tmp = tempfile.mkstemp(suffix=ext)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                y = load_audio(tmp).mean(0)
            except Exception as ex:
                raise ValueError(f"读不了这个音频文件：{ex}") from ex
        finally:
            os.remove(tmp)
        dur = len(y) / 44100
        if dur < 5:
            raise ValueError(f"录音只有 {dur:.1f} 秒，至少要 5 秒（建议 10–25 秒）")
        if dur > 90:
            raise ValueError(f"录音有 {dur:.0f} 秒，太长了，剪成 10–25 秒最清楚的一段再传")
        peak = float(np.abs(y).max())
        if peak < 1e-3:
            raise ValueError("录音几乎没有声音")
        y = (y / peak * 0.9).astype(np.float32)  # 音量拉齐
        path = self.cfg.voice.reference
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.replace(path, os.path.splitext(path)[0] + ".bak.wav")
        from .tts import ref_text_path
        txt = ref_text_path(self.cfg)
        if os.path.exists(txt):  # 旧录音的文字对不上新录音了
            os.replace(txt, os.path.splitext(path)[0] + ".bak.txt")
        sf.write(path, y, 44100, subtype="PCM_16")
        self._drop_sung_all()
        self._reload_service()
        log(f"换了音色录音（{dur:.1f} 秒）")
        info = self.voice_info()
        if dur > 25:
            info["note"] = "只会用前 25 秒"
        return info

    def _drop_sung_all(self) -> None:
        d = os.path.join(self.lib.dir, "sung")
        for f in os.listdir(d) if os.path.isdir(d) else []:
            os.remove(os.path.join(d, f))

    # ── B 站 cookie ──────────────────────────────────

    def cookie_info(self) -> dict:
        v, src = local_store.sessdata(self.cfg)
        return {"set": bool(v), "source": src, "masked": local_store.mask(v) if v else ""}

    def cookie_check(self) -> dict:
        v, _ = local_store.sessdata(self.cfg)
        if not v:
            return {"ok": False, "msg": "还没填"}
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers={
            "Cookie": f"SESSDATA={v}", "Referer": "https://www.bilibili.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
        try:
            with _direct.open(req, timeout=10) as r:
                d = json.loads(r.read()).get("data") or {}
        except (OSError, ValueError, urllib.error.URLError) as ex:
            return {"ok": False, "msg": f"连不上 B 站：{ex}"}
        if d.get("isLogin"):
            return {"ok": True, "msg": f"有效，登录的是「{d.get('uname', '')}」"}
        return {"ok": False, "msg": "无效或已过期，重新从浏览器复制一份"}


def make_handler(adm: Admin):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200, headers: dict | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", headers)

        def _token(self) -> str:
            m = re.search(r"(?:^|;\s*)qm_admin=([\w-]+)", self.headers.get("Cookie", ""))
            return m.group(1) if m else ""

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _file(self, path: str) -> None:
            """带 Range 的文件下发（浏览器拖进度条要）。"""
            size = os.path.getsize(path)
            ctype = AUDIO_TYPES.get(os.path.splitext(path)[1].lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
            if start > end:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            self.send_response(206 if m else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if m:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(left, 1 << 16))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (ConnectionError, OSError):
                        return
                    left -= len(chunk)

        def do_GET(self) -> None:
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/me":
                ok = adm.authed(self._token())
                return self._json({"login": ok, "default_password": ok and local_store.is_default_password(adm.cfg)})
            if not adm.authed(self._token()):
                return self._json({"error": "没登录"}, 401)
            if u.path == "/api/songs":
                return self._json(adm.songs())
            if u.path == "/api/cookie":
                return self._json(adm.cookie_info())
            if u.path == "/api/voice":
                return self._json(adm.voice_info())
            if u.path == "/api/covers":
                return self._json({"covers": adm.covers()})
            if u.path == "/api/audio":
                if q.get("kind") == "cover":
                    p = adm.cover_file(q.get("id", ""))
                    return self._file(p) if p else self._send(404, b"", "text/plain")
                if q.get("kind") == "voice":
                    p = adm.cfg.voice.reference
                    return self._file(p) if os.path.exists(p) else self._send(404, b"", "text/plain")
                sid = q.get("id", "")
                if sid not in adm.lib.load():
                    return self._send(404, b"", "text/plain")
                path = adm.sung_file(sid, q.get("full") == "1") if q.get("kind") == "sung" else adm.lib.audio_path(sid)
                return self._file(path) if path else self._send(404, b"", "text/plain")
            self._send(404, b"", "text/plain")

        do_HEAD = do_GET

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if self.headers.get("X-QM") != "1":  # 只认管理页自己的 fetch，别的网站跨站提交不了
                return self._json({"error": "bad request"}, 400)
            try:
                if path == "/api/login":
                    token = adm.login(str(self._body().get("password", "")))
                    if not token:
                        return self._json({"error": "密码不对"}, 403)
                    return self._json({"ok": True, "default_password": local_store.is_default_password(adm.cfg)}, headers={
                        "Set-Cookie": f"qm_admin={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS * 3600}"})
                if not adm.authed(self._token()):
                    return self._json({"error": "没登录"}, 401)
                if path == "/api/logout":
                    adm.sessions.pop(self._token(), None)
                    return self._json({"ok": True}, headers={"Set-Cookie": "qm_admin=; Path=/; Max-Age=0"})
                if path == "/api/upload":
                    return self._upload()
                if path == "/api/cover":
                    if not os.path.exists(adm.cfg.voice.reference):
                        return self._json({"error": "还没设置音色：先在「设置」里上传一段录音"}, 400)
                    n = int(self.headers.get("Content-Length") or 0)
                    if not n or n > MAX_UPLOAD:
                        return self._json({"error": "文件为空或超过 200MB"}, 400)
                    fname = unquote(self.headers.get("X-Filename", "song.mp3"))
                    p = adm.save_upload(fname, self.rfile.read(n))
                    adm.svc.ensure()
                    name = unquote(self.headers.get("X-Name", "")).strip() or os.path.splitext(fname)[0]
                    return self._json(adm.svc.call("/cover", {"path": p, "name": name}))
                if path == "/api/cover/delete":
                    adm.delete_cover(str(self._body().get("id", "")))
                    return self._json({"ok": True})
                if path == "/api/voice":
                    n = int(self.headers.get("Content-Length") or 0)
                    if not n or n > 50 * 2**20:
                        return self._json({"error": "文件为空或超过 50MB"}, 400)
                    return self._json(adm.save_voice(unquote(self.headers.get("X-Filename", "voice.wav")), self.rfile.read(n)))
                b = self._body()
                if path == "/api/password":
                    if not local_store.check_password(adm.cfg, str(b.get("old", ""))):
                        return self._json({"error": "原密码不对"}, 403)
                    new = str(b.get("new", ""))
                    if len(new) < 6 or new == local_store.DEFAULT_PASSWORD:
                        return self._json({"error": "新密码至少 6 位，而且不能是 123456"}, 400)
                    local_store.set_password(adm.cfg, new)
                    adm.sessions = {self._token(): adm.sessions[self._token()]}  # 别的登录都踢掉
                    return self._json({"ok": True})
                if path == "/api/cookie":
                    v = local_store.parse_sessdata(str(b.get("value", "")))
                    if not v.isascii():
                        return self._json({"error": "SESSDATA 只有英文、数字和 % , * 这类符号，里面有中文或全角字符，像是复制错了"}, 400)
                    local_store.set_sessdata(adm.cfg, v)
                    return self._json(adm.cookie_info())
                if path == "/api/cookie/check":
                    return self._json(adm.cookie_check())
                if path == "/api/service/start":
                    adm.svc.ensure()
                    return self._json({"ok": True})
                if path == "/api/learn":
                    title = str(b.get("title", "")).strip()
                    if not title:
                        return self._json({"error": "要填歌名"}, 400)
                    adm.svc.ensure()
                    return self._json(adm.svc.call("/learn", {"title": title, "artist": str(b.get("artist", "")).strip()}))
                if path == "/api/voice/text":
                    return self._json(adm.save_voice_text(str(b.get("text", ""))))
                if path == "/api/tts":
                    if not os.path.exists(adm.cfg.voice.reference):
                        return self._json({"error": "还没设置音色：先在「设置」里上传一段录音"}, 400)
                    text = str(b.get("text", "")).strip()
                    if not 1 <= len(text) <= adm.cfg.tts.max_chars:
                        return self._json({"error": f"文字要在 1–{adm.cfg.tts.max_chars} 个字之间"}, 400)
                    adm.svc.ensure()
                    audio, mode = adm.svc.tts(text)
                    return self._send(200, audio, "audio/wav", {"X-TTS-Mode": mode})
                if path == "/api/cover/song":
                    if not os.path.exists(adm.cfg.voice.reference):
                        return self._json({"error": "还没设置音色：先在「设置」里上传一段录音"}, 400)
                    adm.svc.ensure()
                    return self._json(adm.svc.call("/cover", {"id": str(b.get("id", ""))}))
                if path == "/api/sing":
                    if not os.path.exists(adm.cfg.voice.reference):
                        return self._json({"error": "还没设置音色：先在「设置」里上传一段录音"}, 400)
                    adm.svc.ensure()
                    audio = adm.svc.call("/sing", {"id": str(b.get("id", "")), "full": bool(b.get("full"))}, timeout=1800)
                    return self._send(200, audio, "audio/wav")
                if path == "/api/edit":
                    adm.edit(str(b.get("id", "")), str(b.get("title", "")).strip(), str(b.get("artist", "")).strip(), b.get("chorus"))
                    return self._json({"ok": True})
                if path == "/api/delete":
                    sid = str(b.get("id", ""))
                    if adm.lib.load().get(sid, {}).get("status") == "pending":
                        return self._json({"error": "这首正在学，学完再删"}, 400)
                    adm.delete(sid)
                    return self._json({"ok": True})
                self._send(404, b"", "text/plain")
            except PermissionError as ex:
                self._json({"error": str(ex)}, 429)
            except urllib.error.HTTPError as ex:
                try:
                    msg = json.loads(ex.read()).get("error", str(ex))
                except ValueError:
                    msg = str(ex)
                self._json({"error": msg}, 502)
            except (KeyError, ValueError, RuntimeError, OSError) as ex:
                self._json({"error": f"{ex}"[:300]}, 400)

        def _upload(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n or n > MAX_UPLOAD:
                return self._json({"error": "文件为空或超过 200MB"}, 400)
            title = unquote(self.headers.get("X-Title", "")).strip()
            if not title:
                return self._json({"error": "要填歌名"}, 400)
            path = adm.save_upload(unquote(self.headers.get("X-Filename", "upload.mp3")), self.rfile.read(n))
            adm.svc.ensure()
            return self._json(adm.svc.call("/add", {"path": path, "title": title,
                                                    "artist": unquote(self.headers.get("X-Artist", "")).strip()}))

        def log_message(self, *args) -> None:
            pass

    return Handler


def serve(cfg, open_browser: bool = False) -> None:
    a = cfg.admin
    server = ThreadingHTTPServer((a.host, a.port), make_handler(Admin(cfg)))
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '') else a.host}:{a.port}"
    log(f"管理页：{url}  （初始密码 {local_store.DEFAULT_PASSWORD}，登录后请改掉）" if local_store.is_default_password(cfg)
        else f"管理页：{url}")
    log(f"曲库：{cfg.library.dir}")
    _no_quick_edit()
    if open_browser:  # 放后台线程：有的环境里打开浏览器会卡住，不能挡住服务启动
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    server.serve_forever()


def _no_quick_edit() -> None:
    """Windows 控制台的「快速编辑」模式下，点一下黑窗口程序就会停住（等你按回车），管理页跟着没反应。关掉它。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)  # 去掉 ENABLE_QUICK_EDIT_MODE，保留 EXTENDED_FLAGS
    except Exception:
        pass
