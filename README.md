# qqbot-music

本地的**零样本 AI 翻唱工作台**：给一段 10–25 秒的录音当音色，输入歌名它自己去找原唱、学会，或者直接上传一首歌，用这个音色唱出来。

- **零样本**：不用训练，换一段录音就是换一个音色。
- **先听再唱**：上传录音后可以先打一段字，用克隆出来的声音念给你听（Qwen3-TTS），像了再唱。
- **浏览器里操作**：一键安装后打开翻唱工作台，搜歌学歌、试唱副歌、整首翻唱、下载都在页面上点。
- **全本地**：模型和曲库都在你自己电脑上，4G 显存的笔记本卡（GTX 1650 Ti）也能跑，就是慢点。
- **可选：接 QQ 机器人**：工作台背后是一个本地 HTTP 服务，装个插件，机器人就能在群里自己学歌、唱歌（MaiBot、AstrBot、DeepSeek Harness 大肥鱼）。不接机器人也能单独用。

> **0.2.0**：新增翻唱工作台（浏览器管理页，密码保护，存 B 站 cookie）、Windows 一键安装（`安装.bat`）、说话克隆试听（Qwen3-TTS，和换声共用一个环境）、曲库歌曲整首翻唱；修了 4G 显存整首翻唱比副歌慢一倍多的问题（音高分段算、预处理后释放显存）。机器人插件接口没变，不用更新插件。

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

## 一键安装（Windows + NVIDIA 显卡，推荐）

不用自己装 Python，不用敲命令：

1. 点本页右上角绿色的 **Code → Download ZIP**，解压到一个**纯英文、没有空格**的路径，比如 `D:\qqbot-music`
2. 双击 **`安装.bat`**：直接回车就装在当前文件夹；想装到别处（比如另一个空间大的盘）输入 `2`，在弹出的窗口里选文件夹。然后等它跑完（下载约 9 GB，网速快的话半小时左右；中途断了再双击会接着装）
3. 装好会自动打开管理页（初始密码 `123456`），按页面提示：改密码 → 上传音色录音 → 听听克隆的声音 → 填 B 站 cookie → 学一首歌、试唱

以后双击 `启动管理页.bat`（或桌面快捷方式）就能打开。

安装脚本会做这些事：

- **先检查电脑**：Windows 10/11 64 位、磁盘空间（约需 15 GB）、显卡型号 / 显存 / 驱动版本（按驱动选 CUDA 12.4 或 11.8 版的 PyTorch；没有 NVIDIA 显卡会提示只能用很慢的 CPU 版）、安装路径有没有中文和空格
- **全部装进这个文件夹**：独立的 Python 3.10 和依赖装在 `env\`，Seed-VC / YingMusic-SVC 代码和模型在 `third_party\`。**不改动你电脑上已有的 Python、conda、CUDA**，不写注册表、不改 PATH；卸载就是删掉整个文件夹
- **国内直连**：Python 走 npmmirror，PyTorch 走阿里云镜像，其余依赖走清华 PyPI，模型全部走 ModelScope（和 Hugging Face 官方文件逐个核对过 sha256）；依赖版本锁定成作者机器上跑通的那一套（`install/requirements-lock.txt`）
- **换声和说话克隆共用一个环境**：Qwen3-TTS 要 transformers 4.57.3，Seed-VC 原本锁 4.46.3，但它只用 Whisper 编码器，实测换版本前后同一段歌换出来几乎一样（相关系数 0.999，只差半精度舍入），所以统一用 4.57.3，不用装两套
- **装完自检**：真的加载一遍换声模型、说话克隆模型和分离器，过了才算装好；安装记录在 `env\install_log.txt`，装不上提 issue 时请附上

> 双击 bat 时 Windows 可能弹「已保护你的电脑」，点「更多信息 → 仍要运行」。
> AMD / Intel 显卡、Mac 暂不支持 GPU 加速。

## 翻唱工作台（管理页）

一键安装完会自动打开；以后双击 `启动管理页.bat`（或桌面快捷方式），手动安装的用 `python -m qqbot_music admin --open`。地址默认 http://127.0.0.1:8767 。

- **🗣 先听听克隆出来的声音**：打一段字，用你的音色录音克隆的声音念出来。「设置」里填上录音里说的话（录音 20 秒以内）是完整克隆，更像；不填只用音色特征
- **① 搜歌名，自动学**：填歌名（和歌手），它去 QQ 音乐定原唱、B 站找对得上的版本、分离、定副歌，学会的歌进曲库
- **② 上传一首歌，整首翻唱**：手里有音频文件就直接传，整首换成你的音色，做好了在页面上播放、下载（不进曲库）
- **③ 本地音频放进曲库**：B 站找不到的歌，自己传进曲库
- **曲库**：按华语 / 日语筛、搜歌名歌手歌词；听原曲副歌、**试唱**副歌、**整首翻唱**（用学歌时分离好的音轨，不用再分离，结果在「整首翻唱」里）、下载；副歌位置不对可以边听边改；删歌会把原曲、分离结果、缓存一起删
- **设置**：上传 / 试听音色录音、填录音里说的话；填 B 站 cookie（SESSDATA）并检测是否有效；改密码

要登录，**初始密码 `123456`，第一次登录后请在「设置」里改掉**。密码（加盐哈希）和页面里填的 cookie 存在本机的 `admin_store.json`，不进仓库；页面里填了 cookie 就优先用它，没填才用 `config.toml` 里的。默认只监听本机，`[admin] host` 改成 `0.0.0.0` 局域网里的手机也能开。

页面本身不占显卡，学歌、试唱、翻唱、说话克隆都交给后台的翻唱服务（没开就自动拉起，闲置 10 分钟自己退出），和机器人插件用的是同一个服务，不会抢显存。

## 可选：接 QQ 机器人

```bash
python -m qqbot_music serve      # 默认 http://127.0.0.1:8766，闲置 10 分钟自己退出
```

- **MaiBot**：装插件 [qqbot-music-maibot](https://github.com/aa1805370178-dot/qqbot-music-maibot)（插件市场或克隆到 `plugins/`）。
- **AstrBot**：装插件 [astrbot_plugin_zeroshot_sing](https://github.com/aa1805370178-dot/astrbot_plugin_zeroshot_sing)。
- **DeepSeek Harness（大肥鱼）**：`dsh plugin --profile web add dsh-zeroshot-sing`，见 [dsh-zeroshot-sing](https://github.com/aa1805370178-dot/dsh-zeroshot-sing)。
- **其他框架**：调 HTTP 接口就行，接口说明也在 [adapters/README.md](adapters/README.md)。

Windows 可以直接双击 `启动唱歌服务.bat`（一般不用：插件和管理页连不上服务会自己拉起）。它会依次找 `env\venv`（一键安装的）、`third_party\seed-vc\env`、PATH 里的 python。

## 手动安装（Linux / 想自己控制环境）

需要 Python 3.10、带 CUDA 的 PyTorch（没有显卡也能跑，非常慢）。

```bash
git clone https://github.com/aa1805370178-dot/qqbot-music && cd qqbot-music

# 1. 换声模型 Seed-VC（按它的 README 装好环境，下面都用这个环境）
git clone https://github.com/Plachtaa/seed-vc third_party/seed-vc

# 2. 分离器（也是 YingMusic 换声后端）
git clone https://github.com/GiantAILab/YingMusic-SVC third_party/YingMusic-SVC
#    从 ModelScope giantailab/YingMusic-SVC 下载 bs_roformer.ckpt 放到 third_party/YingMusic-SVC/ckpt/
#    （要用 YingMusic 换声再下 YingMusic-SVC-full.pt）

# 3. 本工具自己的依赖（说话克隆另外几个包见 requirements.txt 里的注释）
pip install -r requirements.txt

# 4. 配置：复制一份，至少改 [voice] reference
cp config.example.toml config.toml
#    把你的音色录音放到 voice/reference.wav

# 5. 试一下
python -m qqbot_music learn 晴天 周杰伦        # 打印出 id
python -m qqbot_music sing <id> out.wav        # 唱副歌
python -m qqbot_music cover 某首歌.mp3 out.wav  # 不进曲库，直接整首翻唱
```

第一次运行会从 Hugging Face 下载 Whisper、BigVGAN、RMVPE、CAMPPlus（共 2G 左右，国内要代理；也可以先跑一遍 `python install/setup_models.py` 从 ModelScope 下）。下完以后设环境变量 `HF_HUB_OFFLINE=1`，否则每次启动都会去联网检查更新，网络不好时会卡住。

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
| 换声 Seed-VC 15 步 | 0.05 倍实时，2.7G | 约 2.1–2.7 倍实时（25 秒副歌一分钟左右，5 分钟整首约 13 分钟），2.7G |
| 换声 YingMusic 100 步 | 0.22 倍实时，2.7G | 没测完整的，估计 9–10 倍实时 |
| 说话克隆 Qwen3-TTS 0.6B | – | 约 1.1 倍实时（加载 + 第一句预热半分钟），2.9G |

- **GTX 16 系不要开半精度**：没有 Tensor Core，分离开半精度反而慢 3.7 倍。`[separate] half = "auto"` 会自己判断。
- 显存小于 8G 时分离、换声、说话克隆轮流占显存：要用哪个先卸掉别的，下次用到再加载（期间别的请求排队）。
- 说话克隆没有 N 卡时用官方 qwen_tts 跑 CPU，非常慢。
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
  tts.py         说话克隆（Qwen3-TTS）
  service.py     HTTP 服务
  admin.py       翻唱工作台（管理页）
  __main__.py    命令行
adapters/README.md                 接机器人的 HTTP 接口说明
docs/原理与踩坑.md
```

曲库目录（`[library] dir`，默认 `library/`）：`library.json` 索引、`audio/` 原曲、`stems/<id>/` 分离结果、`sung/` 唱过的缓存。

## 注意

- 下载的歌、分离结果、你的音色录音都只在本地，**不要传到公开仓库**（`.gitignore` 已经排除了 `library/`、`voice/`、`third_party/`、`config.toml`）。
- 用别人的声音做音色参考前请先取得同意；翻唱内容请遵守平台和版权规定。

## 用到的项目

- [Seed-VC](https://github.com/Plachtaa/seed-vc)（代码和模型 GPL-3.0）：零样本换声
- [YingMusic-SVC](https://github.com/GiantAILab/YingMusic-SVC)（代码 MIT，**模型权重 CC-BY-NC-4.0**）：BS-Roformer 三轨分离器、换声模型
- [BigVGAN](https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x)（MIT）、RMVPE、Whisper、CAMPPlus：经由上面两个项目使用
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)（代码和模型 Apache-2.0）：说话克隆；加速用 [faster-qwen3-tts](https://pypi.org/project/faster-qwen3-tts/)

本仓库不包含任何模型权重，都是安装时从各自的官方源下载。

## 许可证

本仓库代码 GPL-3.0（和 Seed-VC 一致）。

**注意：分离用的 `bs_roformer.ckpt` 和 YingMusic 换声模型来自 YingMusic-SVC，许可证是 CC-BY-NC-4.0（禁止商用）**，所以用这套工具做出来的东西只能个人非商业使用，不能拿去做收费服务或商用。
