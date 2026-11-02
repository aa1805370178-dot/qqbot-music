"""qqbot-music 的 MaiBot 插件：给麦麦一个 sing 工具，用你配置的音色唱歌、发语音。

插件本身不跑模型，只通过 HTTP 调 qqbot-music 的唱歌服务（python -m qqbot_music serve）。
服务没开时，如果配置了 [service] start_command，就用它拉起来；没配置就告诉麦麦唱不了。
唱歌要一两分钟，工具先返回「在唱了」，唱好后在后台把语音发到同一个会话。
"""

import asyncio
import base64
import io
import shlex
import subprocess
import time
import wave
from typing import Any, Dict

import httpx
import numpy as np

from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=False, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class ServiceConfig(PluginConfigBase):
    __ui_label__ = "唱歌服务"
    __ui_icon__ = "music"
    __ui_order__ = 1

    url: str = Field(default="http://127.0.0.1:8766", description="qqbot-music 唱歌服务地址")
    start_command: str = Field(default="", description="服务没开时用来启动它的命令（留空就不自动启动）")
    start_cwd: str = Field(default="", description="启动命令的工作目录（qqbot-music 目录）")
    start_seconds: int = Field(default=120, description="等服务启动的最长秒数")


class SingConfig(PluginConfigBase):
    __ui_label__ = "唱歌"
    __ui_icon__ = "mic"
    __ui_order__ = 2

    allow_full: bool = Field(default=True, description="是否允许唱整首（整首要好几分钟）")
    chorus_timeout: int = Field(default=600, description="唱副歌的超时秒数")
    full_timeout: int = Field(default=3600, description="唱整首的超时秒数")
    voice_rate: int = Field(default=24000, description="发出去的语音采样率（QQ 语音会转 silk，24k 单声道足够）")


class QQBotMusicSingConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    sing: SingConfig = Field(default_factory=SingConfig)


class QQBotMusicSingPlugin(MaiBotPlugin):
    config_model = QQBotMusicSingConfig

    def __init__(self) -> None:
        super().__init__()
        self._starting = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def on_load(self) -> None:
        pass

    async def on_unload(self) -> None:
        for t in self._tasks:
            t.cancel()

    # ── 服务 ──────────────────────────────────────────

    async def _alive(self, client: httpx.AsyncClient) -> bool:
        try:
            return (await client.get(f"{self.config.service.url}/ping", timeout=3)).status_code == 200
        except httpx.HTTPError:
            return False

    async def _ensure(self, client: httpx.AsyncClient) -> None:
        s = self.config.service
        async with self._starting:
            if await self._alive(client):
                return
            if not s.start_command:
                raise RuntimeError("唱歌服务没开")
            # 独立进程：机器人重启不带走它，闲置了它自己退
            subprocess.Popen(shlex.split(s.start_command, posix=False), cwd=s.start_cwd or None,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.time() + s.start_seconds
            while time.time() < deadline:
                await asyncio.sleep(3)
                if await self._alive(client):
                    return
            raise RuntimeError("唱歌服务没起来")

    async def _call(self, method: str, path: str, timeout: float = 15, **kw) -> httpx.Response:
        async with httpx.AsyncClient(trust_env=False) as client:
            await self._ensure(client)
            return await client.request(method, f"{self.config.service.url}{path}", timeout=timeout, **kw)

    # ── 工具 ──────────────────────────────────────────

    @Tool(
        "sing",
        description=(
            "用你自己的声音唱歌，发一条唱歌的语音。只有别人明确要你唱歌时才用，平时聊天不要主动唱。"
            "默认只唱副歌；对方明确要整首才把 full 设成 true。对方没指定唱哪首时 title 留空，会从你会唱的歌里随机挑；"
            "只说了歌手就填 artist、title 留空。不会的歌会先去学，学的时候告诉对方要等几分钟，学会了再让对方点一次。"
            "会不会唱只有调用这个工具才知道，不要凭聊天记录里自己说过的话判断。歌名你没听过也原样填，不要自己改成别的歌。"
        ),
        parameters=[
            ToolParameterInfo(name="title", param_type=ToolParamType.STRING, description="歌名，不带书名号；没指定就留空", required=False),
            ToolParameterInfo(name="artist", param_type=ToolParamType.STRING, description="歌手，对方说了才填", required=False),
            ToolParameterInfo(name="full", param_type=ToolParamType.BOOLEAN, description="对方明确要整首时为 true", required=False),
        ],
    )
    async def handle_sing(self, title: str = "", artist: str = "", full: bool = False, **kwargs: Any) -> Dict[str, str]:
        stream_id = str(kwargs.get("stream_id") or "")
        title = (title or "").strip().strip("《》「」\"'")
        full = full is True and self.config.sing.allow_full
        try:
            if title:
                found = (await self._call("GET", "/find", params={"title": title, "artist": artist})).json()
                if found["status"] in ("unknown", "failed"):
                    found = (await self._call("POST", "/learn", json={"title": title, "artist": artist})).json()
            else:
                found = (await self._call("GET", "/random", params={"artist": artist})).json()
        except Exception as ex:  # 服务问题告诉麦麦，让她自己跟对方说
            return self._result(f"现在唱不了：{ex}")
        status = found.get("status")
        if status == "learning":
            return self._result(f"你还不会唱「{title}」，已经在学了，要几分钟，学会后对方再点一次就能唱。")
        if status == "failed":
            return self._result(f"「{title}」学不会：{found.get('error', '')}")
        if status != "ready":
            return self._result("你会唱的歌里没有符合要求的。" if not title else f"你不会唱「{title}」。")
        if not stream_id:
            return self._result("不知道发到哪个会话，唱不了。")
        task = asyncio.create_task(self._sing_and_send(found["id"], full, stream_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        lyrics = "／".join(found.get("lyrics") or [])
        return self._result(f"开始唱《{found['title']}》（{found['artist']}）{'整首' if full else '副歌'}，"
                            f"唱好会自动发语音，不用你再发。{'副歌歌词：' + lyrics if lyrics and not full else ''}")

    async def _sing_and_send(self, sid: str, full: bool, stream_id: str) -> None:
        c = self.config.sing
        try:
            r = await self._call("POST", "/sing", json={"id": sid, "full": full},
                                 timeout=c.full_timeout if full else c.chorus_timeout)
            if r.status_code != 200:
                raise RuntimeError(r.text[:120])
            voice = to_voice_wav(r.content, c.voice_rate)
            await self.ctx.send.custom("voice", base64.b64encode(voice).decode(), stream_id)
        except Exception as ex:
            await self.ctx.send.text(f"（没唱出来：{type(ex).__name__} {ex}）"[:150], stream_id)

    @staticmethod
    def _result(text: str) -> Dict[str, str]:
        return {"name": "sing", "content": text}


def to_voice_wav(data: bytes, rate: int) -> bytes:
    """服务给的 44.1k 立体声 WAV → 单声道 rate Hz 16 位（小很多，发 QQ 语音够用）。"""
    with wave.open(io.BytesIO(data)) as src:
        sr, ch = src.getframerate(), src.getnchannels()
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype=np.int16).astype(np.float32)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    if sr != rate:
        try:  # 多相重采样带抗混叠；线性插值会把高频折回来变成嘶嘶声
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(rate, sr)
            pcm = resample_poly(pcm, rate // g, sr // g)
        except ImportError:
            rate = sr  # 没装 scipy 就不降采样
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        dst.writeframes(np.clip(pcm, -32768, 32767).astype(np.int16).tobytes())
    return out.getvalue()


def create_plugin() -> QQBotMusicSingPlugin:
    return QQBotMusicSingPlugin()
