# StoryMotion Studio

从构思、小说和参考视频到有声 AI 漫剧的一站式生产系统。

核心流程只有一条：

`构思 -> 剧本 -> 分镜 -> 素材 -> 音频 -> 视频 -> 剪辑 -> EVAL -> 交付`

三种项目模式只改变输入解析和少量阶段策略：

| 模式 | 输入 | 重点约束 |
| --- | --- | --- |
| `original` | 一句话构思 | 补全剧情、角色和节拍 |
| `novel` | TXT 小说/故事 | 提炼剧情，改编成可拍对白与镜头 |
| `replica` | 参考视频 | 保留节奏和事件关系，替换角色资产并检查物理连续性 |

## 能力边界

- 统一项目状态、阶段产物、审批、恢复运行和发布门禁。
- 网关文本、图像、Seedance 与 MiniMax H3 视频生成，所有付费调用都需要显式 `--enable-live`。
- 豆包多角色 TTS 与本地语音回退；角色音色在项目内固定。
- OpenMontage/FFmpeg 剪辑、字幕、响度和成片导出。
- EVAL 检查角色/场景一致性、动作物理、镜头连续性、口型、音频重叠、杂音和交付完整性。
- 保留宠物原创短剧与参考复刻的专业执行器；它们共用九阶段主流程。

LumenX 不再是运行依赖。旧版 LumenX handoff 仍可由网关批处理只读兼容，新的项目统一生成 `video_handoff.json`。

## 安装

```bash
cd /path/to/storymotion-studio
scripts/bootstrap_factory.sh
cp .env.example .env
```

至少配置一种生成路线。网关示例：

```dotenv
LLM_PROVIDER=gateway
IMAGE_PROVIDER=gateway
VIDEO_PROVIDER=gateway
GATEWAY_API_KEY=your-key
ENABLE_GATEWAY_VIDEO=1
```

豆包 TTS 可使用 Speech API Key，或 AppID + Access Key 的流式凭据。真实密钥只放在 `.env`，不要提交到仓库。

MiniMax H3 视频路线：

```dotenv
VIDEO_PROVIDER=minimax
MINIMAX_API_KEY=your-key
MINIMAX_API_BASE=https://api.minimaxi.com
MINIMAX_VIDEO_MODEL=MiniMax-H3
ENABLE_MINIMAX_VIDEO=1
```

单镜头试生成使用统一入口；H3 支持 4-15 秒和 `768P`/`2K`：

```bash
.venv/bin/python factory_cli.py video-generate \
  --prompt "一只黑白猫自然抬起右前爪，固定机位，无转场" \
  --duration 4 \
  --ratio 9:16 \
  --resolution 768P \
  --output output/h3_probe.mp4 \
  --enable-live
```

任务 ID、轮询次数、MiniMax 返回的用量和预估人民币费用会写入生成报告。省略 `--enable-live` 只生成计划，不会发起云端请求。

MiniMax H3 使用官方结构化提示词，而不是把通用视频提示词原样发送：

- 无参考素材时输出 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。
- 有角色参考图时输出 Ref2VA 的六段结构，并用稳定的 `<Subject N>`、`<Picture N>` 和 `(S1)` 标签绑定角色与说话人。
- 中文对白原样放入 `<d>[Chinese] ...</d>`，画外旁白明确要求可见角色闭嘴，人物对白明确要求开口与音节同步并在句末闭嘴。
- H3 适配器支持 `first_frame`、`last_frame`、`reference_image` 三种图片角色；普通路径继续按角色参考图处理。

官方源码独立检出在 `/Users/tml/Desktop/MiniMax-H3`，仅用于跟踪规范、示例和上游更新，不作为本项目运行依赖。当前发布音色仍以豆包 TTS 为准；H3 原生声音不会与最终音轨叠加。提示词负责自然开口，逐字精确口型仍需经过项目的口型后处理和 EVAL。

生产核心还统一执行以下约束：

- 每个阶段都有显式实现版本；实现变化会使当前阶段及下游缓存失效。
- 分镜显式记录出镜角色，视频素材保留首帧、尾帧和角色参考图的语义角色。
- 对白镜头从最终 TTS 时间线切出逐镜音频，并把音频摘要写入视频任务签名。
- 云端片段先统一帧率、分辨率、像素格式和时长，再无损接入项目时间线；最终音频按目标时长补齐或裁切。
- EVAL 先检查媒体有效性、音轨、时长漂移、对白重叠、镜头绑定和生成失败，再进入解剖、表情、连续性等人工审核。

## 快速开始

原创：

```bash
.venv/bin/python factory_cli.py factory create \
  --mode original \
  --project cat_episode_01 \
  --title 窗边的声音 \
  --idea "两只猫调查窗帘后的声音" \
  --duration 65 \
  --shots 8
```

小说改编：

```bash
.venv/bin/python factory_cli.py factory create \
  --mode novel \
  --project novel_episode_01 \
  --title 旧城来信 \
  --input samples/sample_novel.txt \
  --shots 8
```

参考复刻：

```bash
.venv/bin/python factory_cli.py factory create \
  --mode replica \
  --project replica_episode_01 \
  --title 面试日 \
  --input /absolute/path/to/reference.mp4 \
  --shots 10 \
  --character-assets /absolute/path/to/characters.json
```

先运行到不产生云端费用的阶段：

```bash
.venv/bin/python factory_cli.py factory run cat_episode_01 --through assets
.venv/bin/python factory_cli.py factory status cat_episode_01
```

允许云端生成并继续：

```bash
.venv/bin/python factory_cli.py factory run cat_episode_01 --enable-live
```

某阶段要求人工确认时，先检查 EVAL/阶段报告，再附证据批准：

```bash
.venv/bin/python factory_cli.py factory approve cat_episode_01 \
  --stage assets \
  --note "角色正面、侧面和花纹一致" \
  --evidence /absolute/path/to/review.json
```

最终检查与交付：

```bash
.venv/bin/python factory_cli.py factory review cat_episode_01
.venv/bin/python factory_cli.py factory publish cat_episode_01
```

也可以使用统一启动脚本：

```bash
MODE=novel PROJECT=sample_episode scripts/start_factory.sh
```

## 主要产物

每个项目位于 `runs/<project>/`：

```text
project.json               项目契约
pipeline_state.json        九阶段状态与阻塞原因
stages/                    各阶段结构化产物
video_handoff.json         中立视频生成任务
openmontage_package.json   剪辑时间线与素材路径
eval/                      自动检查、人工复核和返修建议
delivery/                  母版、报告和版本记录
```

生成媒体位于 `runs/` 或 `output/`，默认不进入 Git。

## 专业入口

九阶段主流程是默认入口。调试宠物专业能力时仍可直接使用：

```bash
.venv/bin/python factory_cli.py pet-replica --help
.venv/bin/python factory_cli.py pet-sitcom --help
```

视频提供方与质量检查也保留独立诊断命令：

```bash
.venv/bin/python factory_cli.py provider-report
.venv/bin/python factory_cli.py video-batch --help
.venv/bin/python factory_cli.py quality-visual-qc --help
```

## 开发验证

```bash
.venv/bin/python -m ruff check factory tests factory_cli.py
.venv/bin/python -m pytest -q
```

代码职责见 [docs/pipeline-code-map.md](docs/pipeline-code-map.md)，部署与密钥管理见 [docs/deployment.md](docs/deployment.md)，完整 bad case 与迭代过程保留在 [docs/iteration-log.md](docs/iteration-log.md)。
