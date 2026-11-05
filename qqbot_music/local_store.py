"""管理页的本地存储：登录密码（加盐哈希）和 B 站 cookie。

文件位置是 [admin] store（默认和 config.toml 同目录的 admin_store.json），只在本机，不进仓库。
B 站 cookie 管理页里填了就用管理页的，没填才用 config.toml 的 [download] bilibili_sessdata。
"""

import hashlib
import hmac
import json
import os
import re
import secrets

DEFAULT_PASSWORD = "123456"
_ITER = 200_000


def _path(cfg) -> str:
    return cfg.admin.store


def load(cfg) -> dict:
    try:
        with open(_path(cfg), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(cfg, data: dict) -> None:
    path = _path(cfg)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ── 密码 ─────────────────────────────────────────────

def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def check_password(cfg, password: str) -> bool:
    d = load(cfg)
    if not d.get("password_hash"):
        return hmac.compare_digest(password, DEFAULT_PASSWORD)
    return hmac.compare_digest(_hash(password, d["salt"]), d["password_hash"])


def is_default_password(cfg) -> bool:
    return not load(cfg).get("password_hash")


def set_password(cfg, password: str) -> None:
    d = load(cfg)
    d["salt"] = secrets.token_hex(16)
    d["password_hash"] = _hash(password, d["salt"])
    save(cfg, d)


# ── B 站 cookie ──────────────────────────────────────

def parse_sessdata(text: str) -> str:
    """可以只贴 SESSDATA 的值，也可以贴整串 cookie（从里面挑出 SESSDATA）。"""
    text = (text or "").strip()
    m = re.search(r"(?:^|;\s*)SESSDATA=([^;\s]+)", text)
    return m.group(1) if m else text.split(";")[0].strip()


def set_sessdata(cfg, value: str) -> None:
    d = load(cfg)
    if value:
        d["bilibili_sessdata"] = value
    else:
        d.pop("bilibili_sessdata", None)
    save(cfg, d)


def sessdata(cfg) -> tuple[str, str]:
    """返回 (cookie, 来源)。来源：admin（管理页填的）/ config（config.toml）/ ""（都没填）。"""
    v = load(cfg).get("bilibili_sessdata", "").strip()
    if v:
        return v, "admin"
    v = (cfg.download.bilibili_sessdata or "").strip()
    return (v, "config") if v else ("", "")


def mask(value: str) -> str:
    return value[:6] + "…" + value[-4:] if len(value) > 12 else "已填写"
