# 部署与运行

## 1. 环境

推荐 Python 3.12，并安装 FFmpeg/ffprobe。初始化：

```bash
scripts/bootstrap_factory.sh
cp .env.example .env
```

项目默认从仓库根目录运行。`config/factory.config.json` 使用相对路径；如 OpenMontage 位于其他位置，可修改 `sources.openMontage`，或把它放到 `external/OpenMontage`。

## 2. 凭据

凭据只写入 `.env`，该文件已被 Git 忽略。

网关：

```dotenv
GATEWAY_API_KEY=
GATEWAY_BASE_URL=https://ops-ai-gateway.yc345.tv
LLM_PROVIDER=gateway
IMAGE_PROVIDER=gateway
VIDEO_PROVIDER=gateway
ENABLE_GATEWAY_VIDEO=1
```

豆包 TTS 二选一：

```dotenv
DOUBAO_SPEECH_API_KEY=
DOUBAO_SPEECH_VOICE_TYPE=
```

或：

```dotenv
DOUBAO_TTS_APPID=
DOUBAO_TTS_ACCESS_KEY=
DOUBAO_TTS_SPEAKER=
```

用以下命令查看能力是否就绪；报告只记录密钥是否存在，不写出密钥内容：

```bash
.venv/bin/python factory_cli.py provider-report
```

## 3. 运行策略

默认运行不允许付费网络请求。推荐分两步：

```bash
.venv/bin/python factory_cli.py factory run <project> --through assets
.venv/bin/python factory_cli.py factory status <project>
```

素材和阶段报告确认后，再显式开启实时生成：

```bash
.venv/bin/python factory_cli.py factory run <project> --enable-live
```

中断后使用：

```bash
.venv/bin/python factory_cli.py factory resume <project> --enable-live
```

流水线按输入、配置和产物哈希判断是否复用；已经通过的阶段不会重复计费。失败报告会保留在项目目录，修正后从失败阶段继续。

## 4. 生产门禁

生成前：

- 角色参考图、场景锚点、道具状态已冻结。
- 同一角色的 voice ID、语速和表演提示已固定。
- 每个镜头有明确起止状态、说话角色和音频窗口。
- 付费调用有项目级 `--enable-live` 授权。

发布前：

- 视频、音频、字幕可解码，时长一致。
- 无对白重叠、爆音、异常静音和明显杂音。
- 说话角色可见时，嘴部运动覆盖对白窗口；非说话角色闭嘴。
- 动作、手脚/爪部、物品受力和场景变化符合物理逻辑。
- 镜头切换有动作或构图承接，不用过多转场掩盖连续性问题。
- EVAL 没有未关闭的阻断项。

## 5. 运维与备份

- `runs/`：可恢复的项目状态和生成中间件。
- `output/`：对外成片与小样。
- `.env`：本地凭据，单独安全备份。
- GitHub：仅保存代码、配置示例、测试和文档，不保存密钥与生成媒体。

发布版本时保留 `project.json`、`pipeline_state.json`、EVAL 报告、成片母版和版本记录，便于重现每次选择和返修原因。
