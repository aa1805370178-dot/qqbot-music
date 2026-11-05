"""曲库：找原唱、下载、整首分离、定副歌。

目录（[library] dir）：
  library.json            索引 {id: {title, artist, source, duration, status, chorus, lyrics, chorus_by, error}}
  audio/<id>.<扩展名>      原曲
  stems/<id>/vocals.flac  主唱（单声道）；instrumental.flac 伴奏（立体声）。整首存，副歌从整首里切
  sung/                   唱过的结果缓存

status：pending（登记了还没学）/ ready（会唱）/ failed（学失败，error 里有原因）。

联网：B 站搜索（建议填自己账号的 SESSDATA，未登录常被风控）+ playurl 拿音频，QQ 音乐搜索定原唱，网易云歌词定副歌。
不想联网就用 add 导入本地音频。
"""

import hashlib
import json
import os
import re
import shutil
import time
from urllib.parse import urlencode

import numpy as np
import requests
import soundfile as sf

SR = 44100
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# 搜索挑版本时排除的标题词：现场 / 翻唱 / 改编 / 伴奏 / 教学 / 加速等都不是原唱录音室版
BAD_WORDS = ["live", "现场", "翻唱", "cover", "dj", "remix", "伴奏", "纯音乐", "教学", "教唱", "nightcore", "加速", "降调",
             "升调", "片段", "钢琴", "吉他", "演奏", "reaction", "合集", "串烧", "倍速", "8d", "ai", "鬼畜", "混剪",
             "街唱", "街头", "路演", "直播", "演唱会", "音乐节", "饭拍", "弹唱", "清唱", "合唱"]

http = requests.Session()
http.trust_env = False  # 国内站直连，系统代理常常会断
http.headers["User-Agent"] = UA


class Library:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg.library.dir
        self.index = os.path.join(self.dir, "library.json")

    # ── 索引 ──────────────────────────────────────────

    def load(self) -> dict:
        if os.path.exists(self.index):
            with open(self.index, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save(self, lib: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.index + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.index)

    def update(self, sid: str, **fields) -> None:
        lib = self.load()  # 重新读，免得覆盖别的进程同时写的
        lib.setdefault(sid, {}).update(fields)
        self.save(lib)

    def stem_dir(self, sid: str) -> str:
        return os.path.join(self.dir, "stems", sid)

    def find(self, title: str, artist: str = "") -> str | None:
        """歌名去掉括号和标点后完全一致（互相包含会把《真英雄》认成《英雄》），给了歌手就要歌手也对上；会唱的优先。"""
        q, qa = norm(title), norm(artist)
        if not q:
            return None
        hits = [(k, v) for k, v in self.load().items() if norm(v["title"]) == q and (not qa or qa in norm(v["artist"]))]
        hits.sort(key=lambda kv: kv[1]["status"] != "ready")
        return hits[0][0] if hits else None

    def ready(self, lang: str = "", artist: str = "") -> list[str]:
        a = norm(artist)
        return [k for k, v in self.load().items()
                if v["status"] == "ready" and (not lang or language(v) == lang) and (not a or a in norm(v["artist"]))]

    # ── 学歌 ──────────────────────────────────────────

    def learn(self, title: str, artist: str = "") -> str:
        """搜原唱版本 → 下载 → 学。返回 id；找不到原唱版本抛 LookupError。"""
        hit = self.find(title, artist)
        if hit and self.load()[hit]["status"] == "ready":
            return hit
        got = pick_bilibili(title, artist, self.cfg.download.verify_original)
        if not got:
            raise LookupError(f"B 站没找到对得上原唱的「{title}」")
        v, singer = got
        sid = f"{v['bvid']}_p1"
        self.update(sid, title=title, artist=singer, source=f"https://www.bilibili.com/video/{v['bvid']}", status="pending")
        self.process(sid)
        return sid

    def add(self, path: str, title: str, artist: str = "") -> str:
        """导入本地音频（不联网下载，副歌还是会试着去网易云拿歌词定，拿不到就按响度找）。"""
        with open(path, "rb") as f:
            sid = "local_" + hashlib.md5(f.read()).hexdigest()[:10]
        os.makedirs(os.path.join(self.dir, "audio"), exist_ok=True)
        shutil.copyfile(path, os.path.join(self.dir, "audio", sid + os.path.splitext(path)[1].lower()))
        self.update(sid, title=title, artist=artist, source=os.path.abspath(path), status="pending")
        self.process(sid)
        return sid

    def audio_path(self, sid: str) -> str | None:
        d = os.path.join(self.dir, "audio")
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if os.path.splitext(f)[0] == sid:
                return os.path.join(d, f)
        return None

    def process(self, sid: str, separator=None) -> None:
        """学一首：下载（没有的话）→ 整首分离 → 定副歌。失败记进 error，不抛出。"""
        from .separate import Separator, load_audio
        e = self.load()[sid]
        t = time.time()
        try:
            path = self.audio_path(sid) or download_bilibili(e["source"], os.path.join(self.dir, "audio", sid + ".m4a"))
            mix = load_audio(path)
            duration = mix.shape[1] / SR
            out = self.stem_dir(sid)
            if not os.path.exists(os.path.join(out, "instrumental.flac")):
                vocals, inst = (separator or Separator(self.cfg))(mix)
                os.makedirs(out, exist_ok=True)
                sf.write(os.path.join(out, "vocals.flac"), vocals, SR)
                sf.write(os.path.join(out, "instrumental.flac"), inst.T, SR)
            lines = netease_lrc(e["title"], e["artist"], duration)
            c = self.cfg.sing
            seg = chorus_from_lrc(lines, duration, c.chorus_min, c.chorus_max)
            by = "lyrics" if seg else "energy"
            start, end, words = seg or chorus_from_energy(mix.mean(0), duration)
            self.update(sid, duration=round(duration, 1), chorus=[round(start, 2), round(end, 2)], lyrics=words,
                        chorus_by=by, status="ready", error="")
            print(f"会唱 {sid} {e['title']} - {e['artist']}  副歌 {start:.0f}-{end:.0f}s（{by}）用时 {time.time() - t:.0f}s", flush=True)
        except Exception as ex:  # 单首失败不影响别的
            self.update(sid, status="failed", error=f"{type(ex).__name__}: {ex}"[:300])
            print(f"失败 {sid} {e['title']}：{type(ex).__name__}: {ex}", flush=True)

    def process_pending(self) -> None:
        """把 pending 的逐首学完，分离器只加载一次。"""
        todo = [k for k, v in self.load().items() if v["status"] == "pending"]
        if not todo:
            return
        from .separate import Separator
        sep = Separator(self.cfg)
        for sid in todo:
            self.process(sid, sep)


# ── 工具函数 ─────────────────────────────────────────

def norm(s: str) -> str:
    s = re.sub(r"[（(\[【].*?[）)\]】]", "", (s or "").lower())
    return re.sub(r"[\s\W_]+", "", s)


KANA, HAN, HANGUL = re.compile(r"[぀-ヿ]"), re.compile(r"[一-鿿]"), re.compile(r"[가-힯]")


def language(e: dict) -> str:
    """看副歌歌词：有韩文算 other（网易云歌词常夹中文翻译，所以先看韩文和假名）、有假名算 ja、有汉字算 zh。"""
    words = "".join(e.get("lyrics") or [])
    if HANGUL.search(words):
        return "other"
    return "ja" if KANA.search(words) else "zh" if HAN.search(words) else "other"


# ── 找原唱 ───────────────────────────────────────────

def original_version(title: str, artist: str = "") -> tuple[list[str], float] | None:
    """QQ 音乐搜索排第一的通常就是原唱（网易云搜热门歌第一页常是翻唱）。歌名一致、不是 live，给了歌手要对上。
    返回 (歌手列表, 秒数)。"""
    r = http.get("https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                 params={"w": f"{title} {artist}".strip(), "format": "json", "n": 10, "p": 1, "cr": 1},
                 headers={"Referer": "https://y.qq.com/"}, timeout=15).json()
    a = norm(artist)
    for s in r.get("data", {}).get("song", {}).get("list", []) or []:
        singers = [x["name"] for x in s["singer"] if norm(x["name"])]
        if norm(s["songname"]) != norm(title) or "live" in s["songname"].lower():
            continue
        if a and not any(norm(x) in a or a in norm(x) for x in singers):
            continue
        return singers, float(s["interval"])
    return None


_MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
          37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
_BILI = {"Referer": "https://www.bilibili.com"}
_warm = False


def _warmup() -> None:
    global _warm
    if not _warm:
        http.get("https://www.bilibili.com", headers=_BILI, timeout=10)  # 拿 buvid3，搜索接口要
        from . import config, local_store
        if sessdata := local_store.sessdata(config.get())[0]:  # 自己账号的登录 cookie，未登录搜索常被风控（管理页填的优先）
            http.cookies.set("SESSDATA", sessdata, domain=".bilibili.com", path="/")
        _warm = True


def _wbi(params: dict) -> dict:
    """B 站新版接口的 wbi 签名（/nav 未登录也会给 wbi_img）。"""
    _warmup()
    img = http.get("https://api.bilibili.com/x/web-interface/nav", headers=_BILI, timeout=10).json()["data"]["wbi_img"]
    raw = "".join(u.rsplit("/", 1)[-1].split(".")[0] for u in (img["img_url"], img["sub_url"]))
    key = "".join(raw[i] for i in _MIXIN if i < len(raw))[:32]
    p = {**params, "wts": int(time.time())}
    qs = urlencode({k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(p.items())})
    p["w_rid"] = hashlib.md5((qs + key).encode()).hexdigest()
    return p


def search_bilibili(keyword: str, pages: int = 2) -> list[dict]:
    """先新版 wbi 接口，拿不到再试旧版。未登录时常被风控（只回 v_voucher 或 412），配置里填 bilibili_sessdata 最稳。"""
    _warmup()
    out = []
    for page in range(1, pages + 1):
        params = {"search_type": "video", "keyword": keyword, "page": page, "page_size": 20, "order": "click"}
        batch = []
        for url, p in (("https://api.bilibili.com/x/web-interface/wbi/search/type", _wbi(params)),
                       ("https://api.bilibili.com/x/web-interface/search/type", params)):
            try:
                batch = (http.get(url, headers=_BILI, params=p, timeout=15).json().get("data") or {}).get("result") or []
            except ValueError:  # 被限流时回的是网页不是 JSON
                batch = []
            if batch:
                break
        out += batch
        if not batch:
            break
        time.sleep(0.8)
    return out


def pick_bilibili(title: str, artist: str = "", verify: bool = True) -> tuple[dict, str] | None:
    """B 站挑原唱版本：标题含歌名、不含排除词，播放量最高。
    verify 时先去 QQ 音乐定原唱：标题或 UP 主要带原唱歌手名、时长和原曲差 15 秒内（光看标题会挑到标题带歌名的搞笑视频）；
    QQ 音乐没有的歌才退回 2–7 分钟。返回 (视频, 歌手)。"""
    original = original_version(title, artist) if verify else None
    singers = original[0] if original else ([artist] if artist else [])
    best = None
    for v in search_bilibili(f"{title} {singers[0] if singers else ''}".strip()):
        name = re.sub(r"<[^>]+>", "", v.get("title", ""))
        mm, _, ss = str(v.get("duration", "0:0")).partition(":")
        secs = int(mm or 0) * 60 + int(ss or 0)
        if norm(title) not in norm(name) or any(w in name.lower() for w in BAD_WORDS):
            continue
        if original and abs(secs - original[1]) > 15 or not original and not 120 <= secs <= 420:
            continue
        if singers and not any(norm(x) in norm(name + v.get("author", "")) for x in singers):
            continue
        if best is None or v.get("play", 0) > best[0].get("play", 0):
            best = (v, "、".join(singers) or v.get("author", ""))
    return best


def download_bilibili(url: str, out: str) -> str:
    """playurl 取最高码率音频；跳过 mcdn（PCDN 节点常 SSL 断开），从正规 CDN 下。"""
    m = re.search(r"(BV\w+)(?:\?p=(\d+))?", url)
    bvid, page = m.group(1), int(m.group(2) or 1)
    pages = http.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}, headers=_BILI, timeout=15).json()["data"]["pages"]
    cid = next(p["cid"] for p in pages if p["page"] == page)
    audio = http.get("https://api.bilibili.com/x/player/playurl", params={"bvid": bvid, "cid": cid, "fnval": 16},
                     headers=_BILI, timeout=15).json()["data"]["dash"]["audio"]
    best = max(audio, key=lambda a: a["bandwidth"])
    urls = [u for u in [best["baseUrl"], *(best.get("backupUrl") or [])] if "mcdn" not in u]
    if not urls:
        raise RuntimeError("B 站只给了 mcdn 节点")
    r = http.get(urls[0], headers=_BILI, timeout=300)
    r.raise_for_status()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(r.content)
    return out


# ── 定副歌 ───────────────────────────────────────────

def netease_lrc(title: str, artist: str, duration: float) -> list[tuple[float, str]]:
    """网易云搜歌，时长差 10 秒内、歌手对得上的优先，返回 [(秒, 歌词)]；拿不到返回 []。"""
    h = {"Referer": "https://music.163.com/"}
    try:
        r = http.get("https://music.163.com/api/search/get/web", params={"s": f"{title} {artist}", "type": 1, "limit": 8},
                     headers=h, timeout=15).json()
    except (requests.RequestException, ValueError):
        return []
    songs = [s for s in (r.get("result") or {}).get("songs", []) or []
             if "live" not in s["name"].lower() and abs(s["duration"] / 1000 - duration) <= 10]
    a = norm(artist)
    songs.sort(key=lambda s: (not a or not any(norm(x["name"]) and (norm(x["name"]) in a or a in norm(x["name"])) for x in s["artists"]),
                              abs(s["duration"] / 1000 - duration)))
    for song in songs[:4]:  # 时长最近的可能是没歌词的翻唱，没歌词就试下一个
        lrc = http.get("https://music.163.com/api/song/lyric", params={"id": song["id"], "lv": 1}, headers=h, timeout=15).json()
        lines = []
        for raw in lrc.get("lrc", {}).get("lyric", "").splitlines():
            text = re.sub(r"\[.*?\]", "", raw).strip()
            if not text or re.search(r"[:：]", text):  # 空行和「作词 : xxx」
                continue
            lines += [(int(m) * 60 + float(s), text) for m, s in re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)]
        if len(lines) >= 4:
            return sorted(lines)
    return []


def chorus_from_lrc(lines, duration, seg_min=15.0, seg_max=32.0):
    """副歌 = 重复句最多的一段：从一句重复句起，往后取到不超过 seg_max 秒（整句结束），按段里重复句数打分，同分取靠前；
    起点不早于全曲 15%。返回 (起, 止, 歌词) 或 None。"""
    if len(lines) < 4:
        return None
    count = {}
    for _, t in lines:
        count[norm(t)] = count.get(norm(t), 0) + 1
    nxt = lambda k: lines[k + 1][0] if k + 1 < len(lines) else min(lines[k][0] + 6, duration)
    best = None
    for i, (t0, text0) in enumerate(lines):
        if t0 < duration * 0.15 or count[norm(text0)] < 2:
            continue
        j = i
        while j + 1 < len(lines) and lines[j + 1][0] - t0 <= seg_max:
            j += 1
        while j > i and nxt(j) - t0 > seg_max + 4:
            j -= 1
        end = min(nxt(j), t0 + seg_max + 4)
        if end - t0 < seg_min:
            continue
        score = sum(1 for _, t in lines[i:j + 1] if count[norm(t)] >= 2)
        if best is None or score > best[0]:
            best = (score, t0, end, [t for _, t in lines[i:j + 1]])
    return (best[1], best[2], best[3]) if best and best[0] else None


def chorus_from_energy(y: np.ndarray, duration: float, length: int = 25):
    """没歌词的退路：全曲 20%–85% 里最响的 25 秒。"""
    rms = np.array([np.sqrt(np.mean(y[i:i + SR] ** 2)) for i in range(0, len(y) - SR, SR)])
    lo = int(duration * 0.2)
    hi = max(int(duration * 0.85) - length, lo + 1)
    sums = [rms[s:s + length].sum() for s in range(lo, min(hi, len(rms) - length))]
    s = lo + int(np.argmax(sums)) if sums else lo
    return float(s), float(s + length), []
