# 部署与运行

## 1. 环境

推荐 Python 3.12，并安装 Node.js、npm、FFmpeg/ffprobe。初始化脚本会验证 Node.js/npm，
创建 Python 虚拟环境，并依据前端 lockfile 执行 `npm ci`：

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
GATEWAY_BASE_URL=https://gateway.example.invalid
LLM_PROVIDER=gateway
IMAGE_PROVIDER=gateway
VIDEO_PROVIDER=gateway
ENABLE_GATEWAY_VIDEO=1
```

`gateway.example.invalid` 只是不可以路由的示例域名。网关没有内置生产默认地址；实际使用时
必须在被 Git 忽略的本机 `.env` 中显式配置真实 `GATEWAY_BASE_URL`。

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

## 6. GitHub 发布门禁

源仓库旧历史曾包含真实凭据，禁止直接推送。账户持有人必须在仓库外撤销或轮换该凭据；
替换测试文本和清理当前文件不能撤销已经签发的访问能力。

完成所有验证并提交后，从干净 tracked tree 导出单提交新历史：

```bash
.venv/bin/python scripts/release_security.py
.venv/bin/python scripts/export_clean_release.py /path/to/fresh-storymotion-release
```

导出器排除源 `.git`、ignored 依赖、缓存、生成媒体和本机 `.env`，初始化一个新的 release
commit，并扫描当前内容及新 Git 历史。GitHub 仓库只能从这个 clean snapshot 创建，默认
保持私有；历史音频权利确认或从发布内容排除后，才能另行评估公开发布。
