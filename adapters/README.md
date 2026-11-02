# 接机器人

核心是 `python -m qqbot_music serve` 起的本地 HTTP 服务（默认 `http://127.0.0.1:8766`）。机器人那边只要会发 HTTP、会发语音就能接。

## MaiBot

1. 把 `maibot/qqbot_music_sing` 整个文件夹复制到 MaiBot 的 `plugins/` 下。
2. 改 `plugins/qqbot_music_sing/config.toml`：

   ```toml
   [service]
   url = "http://127.0.0.1:8766"
   # 想让插件在服务没开时自己拉起来，就填启动命令和 qqbot-music 目录；留空就要你自己先开服务
   start_command = "D:/xxx/seed-vc/env/Scripts/python.exe -m qqbot_music serve"
   start_cwd = "D:/xxx/qqbot-music"
   ```

3. 重启 MaiBot。麦麦会多一个 `sing` 工具：有人让她唱歌时她会调用，副歌一两分钟后以语音发出来。

插件只依赖 MaiBot 环境里本来就有的 `httpx`、`numpy`（`scipy` 有就用来做降采样，没有也行），不 import 本仓库的代码，两边可以装在不同的 Python 环境。

## 其他框架（AstrBot、NoneBot、自己写的……）

照下面的接口写一个「唱歌」工具/指令即可。所有 JSON 都是 UTF-8。

| 接口 | 说明 |
|---|---|
| `GET /ping` | 返回 `ok` |
| `GET /find?title=歌名&artist=歌手` | 查会不会唱。返回 `{"status", "id", "title", "artist", "lyrics"}` |
| `GET /random?lang=zh&artist=歌手` | 从会唱的歌里随机挑一首（`lang` 可选 `zh` / `ja`，`artist` 可选），格式同 `/find` |
| `POST /learn {"title", "artist"}` | 不会就在后台学，马上返回状态 |
| `POST /sing {"id", "full": false}` | 返回 `audio/wav`（44.1k 立体声 16 位）。默认唱副歌，`full: true` 唱整首 |

`status` 的取值：

- `ready`：会唱，带 `id`，拿它去 `/sing`
- `learning`：正在学（下载 + 整首分离，几分钟到十几分钟，看显卡）
- `failed`：学不会，`error` 里有原因（常见：B 站没找到对得上原唱的版本）
- `unknown`：不会，也没在学

推荐流程：`/find` → `unknown`/`failed` 就 `/learn` 并告诉用户等一会儿 → `ready` 就 `/sing`（这一步要一两分钟，放后台做，唱好再发）。

发 QQ 语音前最好转成单声道 24k，文件小很多；MaiBot 插件里的 `to_voice_wav` 可以直接抄。

服务一次只唱一首，其他请求排队；闲置 `[service] idle_minutes` 分钟后自己退出，所以建议像 MaiBot 插件那样「连不上就拉起来」。
