# 接机器人

核心是 `python -m qqbot_music serve` 起的本地 HTTP 服务（默认 `http://127.0.0.1:8766`）。机器人那边只要会发 HTTP、会发语音就能接。

## MaiBot

插件单独一个仓库：[qqbot-music-maibot](https://github.com/aa1805370178-dot/qqbot-music-maibot)（也可以在 MaiBot 插件市场里装）。装好后麦麦会多一个 `sing` 工具，有人让她唱歌时调用，副歌一两分钟后以语音发出来。插件不 import 本仓库的代码，两边可以装在不同的 Python 环境。

## AstrBot

插件仓库：[astrbot_plugin_zeroshot_sing](https://github.com/aa1805370178-dot/astrbot_plugin_zeroshot_sing)。同样是一个 `sing` 工具，唱好后以语音发到原会话。

## DeepSeek Harness（大肥鱼）

npm 包 [dsh-zeroshot-sing](https://github.com/aa1805370178-dot/dsh-zeroshot-sing)：`dsh plugin --profile web add dsh-zeroshot-sing`。多 `sing`（唱好存 WAV 并在本机播放）和 `learn_song`（主动学歌）两个工具。

## 其他框架（NoneBot、Koishi、自己写的……）

照下面的接口写一个「唱歌」工具/指令即可。所有 JSON 都是 UTF-8。

| 接口 | 说明 |
|---|---|
| `GET /ping` | 返回 `ok` |
| `GET /find?title=歌名&artist=歌手` | 查会不会唱。返回 `{"status", "id", "title", "artist", "lyrics"}` |
| `GET /random?lang=zh&artist=歌手` | 从会唱的歌里随机挑一首（`lang` 可选 `zh` / `ja`，`artist` 可选），格式同 `/find` |
| `POST /learn {"title", "artist"}` | 不会就在后台学，马上返回状态 |
| `POST /sing {"id", "full": false}` | 返回 `audio/wav`（44.1k 立体声 16 位）。默认唱副歌，`full: true` 唱整首 |
| `POST /add {"path", "title", "artist"}` | 导入服务所在机器上的音频文件，后台学（不联网下载），状态同 `/learn` |
| `POST /cover {"path", "name"}` 或 `{"id"}` | 整首翻唱：本机音频文件（先分离）或曲库里会唱的歌（用已分离的音轨），后台做，返回 `{"id"}`，进度在曲库目录 `covers/<id>.json` |
| `POST /tts {"text"}` | 用音色录音克隆的声音念这段字，返回 `audio/wav`，响应头 `X-TTS-Mode` 是 `icl`（用了录音文字）或 `xvec`（只用音色特征） |
| `GET /learning` | 正在学和学失败的歌：`{"learning": [歌名], "failed": {歌名: 原因}}` |

`status` 的取值：

- `ready`：会唱，带 `id`，拿它去 `/sing`
- `learning`：正在学（下载 + 整首分离，几分钟到十几分钟，看显卡）
- `failed`：学不会，`error` 里有原因（常见：B 站没找到对得上原唱的版本）
- `unknown`：不会，也没在学

推荐流程：`/find` → `unknown`/`failed` 就 `/learn` 并告诉用户等一会儿 → `ready` 就 `/sing`（这一步要一两分钟，放后台做，唱好再发）。

发 QQ 语音前最好转成单声道 24k，文件小很多；[MaiBot 插件](https://github.com/aa1805370178-dot/qqbot-music-maibot/blob/master/plugin.py)里的 `to_voice_wav` 可以直接抄。

服务一次只唱一首，其他请求排队；闲置 `[service] idle_minutes` 分钟后自己退出，所以建议像 MaiBot 插件那样「连不上就拉起来」。
