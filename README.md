# qqbot-music

让 QQ 机器人用**你指定的音色**唱歌：点歌 → 自己去 B 站找原唱 → 分离人声和伴奏 → 换成你的音色 → 发语音。

- 零样本：只要一段 10–25 秒的干净录音当音色参考，不用训练。
- 不绑框架：核心是一个本地 HTTP 服务，MaiBot、AstrBot 或者任何能发 HTTP 的机器人都能用。仓库里带了 MaiBot 插件。
- 4G 显存的笔记本卡（GTX 1650 Ti）也能跑，就是慢点。

> 这是一个自用流程整理出来的小工具，不是什么完整产品。换声和分离的模型都是别人的（见文末「用到的项目」），这里做的是把它们串起来，以及踩坑后补上的那些处理。

## 流程

```
点歌 ──► 曲库里有？──否──► 学歌：QQ 音乐定原唱 → B 站找对得上的版本 → 下载
                │                 → 整首分离（主唱 / 伴奏，和声丢掉）→ 网易云歌词定副歌
                是
                ▼
       切副歌（或整首）→ 音高处理 → 换声 → 去齿音 / 噪声门 → 按伴奏定人声响度混音 → WAV
```

几个关键处理（为什么要做见 [docs/原理与踩坑.md](docs/原理与踩坑.md)）：

| 处理 | 解决什么 |
|---|---|
| 伴奏永远不变调，人声只按**乐句整八度**挪 | 伴奏变调失真很明显；原唱男声低音直接换成女声会变成挤出来的「公鸭嗓」 |
| 八度跳变修正 | 音高提取偶尔把一帧测错一个八度，换出来是一声怪叫 |
| 参考音 + 换声结果两道去齿音 | 参考音齿音偏尖时，换声会把它放大成持续的「滋滋」声 |
| 噪声门 | 分离漏到主唱轨的伴奏会被换成杂音 |
| 人声响度按伴奏定 | 分离器有时把主唱分走一部分，按原唱主唱轨对齐会偏小 |
| 整首分离、副歌从整首里切 | 只分离一小段时边界处分离质量差 |

## 快速开始

需要 Python 3.10、带 CUDA 的 PyTorch（没有显卡也能跑，非常慢）。

```bash
git clone https://github.com/aa1805370178-dot/qqbot-music && cd qqbot-music

# 1. 换声模型 Seed-VC（按它的 README 装好环境，下面都用这个环境）
git clone https://github.com/Plachtaa/seed-vc third_party/seed-vc

# 2. 分离器（也是 YingMusic 换声后端）
git clone https://github.com/GiantAILab/YingMusic-SVC third_party/YingMusic-SVC
#    从 ModelScope giantailab/YingMusic-SVC 下载 bs_roformer.ckpt 放到 third_party/YingMusic-SVC/ckpt/
#    （要用 YingMusic 换声再下 YingMusic-SVC-full.pt）

# 3. 本工具自己的依赖
pip install -r requirements.txt

# 4. 配置：复制一份，至少改 [voice] reference
cp config.example.toml config.toml
#    把你的音色录音放到 voice/reference.wav

# 5. 试一下
python -m qqbot_music learn 晴天 周杰伦        # 打印出 id
python -m qqbot_music sing <id> out.wav        # 唱副歌
python -m qqbot_music cover 某首歌.mp3 out.wav  # 不进曲库，直接整首翻唱
```

第一次运行会从 Hugging Face 下载 Whisper、BigVGAN、RMVPE、CAMPPlus（共 2G 左右，国内要代理）。下完以后设环境变量 `HF_HUB_OFFLINE=1`，否则每次启动都会去联网检查更新，网络不好时会卡住。

### 接机器人

```bash
python -m qqbot_music serve      # 默认 http://127.0.0.1:8766，闲置 10 分钟自己退出
```

- **MaiBot**：把 `adapters/maibot/qqbot_music_sing` 整个复制到 MaiBot 的 `plugins/` 下，改里面的 `config.toml`。详见 [adapters/README.md](adapters/README.md)。
- **其他框架**：调 HTTP 接口就行，接口说明也在 [adapters/README.md](adapters/README.md)。

Windows 可以直接双击 `启动唱歌服务.bat`（先在里面填好 Python 路径）。

## 配置

全部在 `config.toml`，每一项的含义写在 [config.example.toml](config.example.toml) 的注释里。没写的项用 example 里的默认值。常改的：

| 项 | 说明 |
|---|---|
| `[voice] reference` | 音色参考录音 |
| `[convert] backend` | `seedvc`（默认，快）或 `yingmusic`（音色更像，慢 4 倍左右） |
| `[convert] steps` | seedvc 15；yingmusic 建议 100（30 也能用） |
| `[pitch] move_semitones` | 乐句音高离参考音超过几个半音就挪八度，默认 9 |
| `[download] bilibili_sessdata` | 你自己 B 站账号的登录 cookie；不填的话搜索常被风控，学不了新歌（`add` 导入本地文件不受影响） |
| `[service] free_ports` | 启动前结束这些端口上的进程，给换声腾显存（比如同一张卡上的 TTS） |

## 速度和显存（实测）

| | RTX 4090 | GTX 1650 Ti 4G |
|---|---|---|
| 分离 | 25 秒音频 3 秒，显存 3.8G | 约 2 倍实时（4 分钟的歌 8 分钟左右），显存 2.2G |
| 换声 Seed-VC 15 步 | 0.05 倍实时，2.7G | 约 2.1 倍实时（25 秒副歌一分钟左右），2.7G |
| 换声 YingMusic 100 步 | 0.22 倍实时，2.7G | 没测完整的，估计 9–10 倍实时 |

- **GTX 16 系不要开半精度**：没有 Tensor Core，分离开半精度反而慢 3.7 倍。`[separate] half = "auto"` 会自己判断。
- 4G 显存放不下分离 + 换声：服务学歌时会先卸掉换声模型，学完下次唱再加载（期间点歌要排队）。
- 纯 CPU 分离 33 秒的音频要 8 分钟，不建议。

### Seed-VC 还是 YingMusic？

YingMusic 的优势要到 100 步左右才出来，代价是比 Seed-VC 15 步慢 4 倍左右。我们自己控制变量听过对比后选了 Seed-VC 15 步；卡好的话可以试 YingMusic 100 步（`backend = "yingmusic"`、`steps = 100`、`cfg_rate = 0.7`）。分离器两种情况都用 YingMusic 自带的 BS-Roformer，清晰度比 UVR 系列好很多。

## 目录

```
qqbot_music/
  config.py      读配置
  library.py     曲库：找原唱、下载、定副歌
  separate.py    分离（BS-Roformer 三轨，只留主唱和伴奏）
  pitch.py       音高处理（八度跳变修正、乐句挪八度）—— 只用 numpy，和换声模型无关
  convert.py     换声（seedvc / yingmusic 两个后端共用一套推理循环）
  post.py        去齿音、噪声门、效果、混音
  sing.py        唱一首（带缓存）
  service.py     HTTP 服务
  __main__.py    命令行
adapters/maibot/qqbot_music_sing/   MaiBot 插件
docs/原理与踩坑.md
```

曲库目录（`[library] dir`，默认 `library/`）：`library.json` 索引、`audio/` 原曲、`stems/<id>/` 分离结果、`sung/` 唱过的缓存。

## 注意

- 下载的歌、分离结果、你的音色录音都只在本地，**不要传到公开仓库**（`.gitignore` 已经排除了 `library/`、`voice/`、`third_party/`、`config.toml`）。
- 用别人的声音做音色参考前请先取得同意；翻唱内容请遵守平台和版权规定。

## 用到的项目

- [Seed-VC](https://github.com/Plachtaa/seed-vc)（GPL-3.0）：零样本换声
- [YingMusic-SVC](https://github.com/GiantAILab/YingMusic-SVC)：BS-Roformer 三轨分离器、换声模型
- RMVPE、BigVGAN、Whisper、CAMPPlus：经由上面两个项目使用

## 许可证

GPL-3.0（和 Seed-VC 一致）。
