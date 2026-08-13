# 漫剧工厂

把原创构思、小说文本或参考视频统一转成有声短剧的本地生产系统。

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
- 网关文本、图像和 Seedance 视频生成，所有付费调用都需要显式 `--enable-live`。
- 豆包多角色 TTS 与本地语音回退；角色音色在项目内固定。
- OpenMontage/FFmpeg 剪辑、字幕、响度和成片导出。
- EVAL 检查角色/场景一致性、动作物理、镜头连续性、口型、音频重叠、杂音和交付完整性。
- 保留宠物原创短剧与参考复刻的专业执行器；它们共用九阶段主流程。

LumenX 不再是运行依赖。旧版 LumenX handoff 仍可由网关批处理只读兼容，新的项目统一生成 `video_handoff.json`。

## 安装

```bash
cd /path/to/manju-factory-next
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

网关与质量检查也保留独立诊断命令：

```bash
.venv/bin/python factory_cli.py provider-report
.venv/bin/python factory_cli.py gateway-video-batch --help
.venv/bin/python factory_cli.py quality-visual-qc --help
```

## 开发验证

```bash
.venv/bin/python -m ruff check factory tests factory_cli.py
.venv/bin/python -m pytest -q
```

代码职责见 [docs/pipeline-code-map.md](docs/pipeline-code-map.md)，部署与密钥管理见 [docs/deployment.md](docs/deployment.md)，完整 bad case 与迭代过程保留在 [docs/iteration-log.md](docs/iteration-log.md)。
