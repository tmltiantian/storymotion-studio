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

需要 Python 3.12、Node.js、npm、FFmpeg 和 ffprobe。初始化脚本会验证 Node.js/npm，创建
Python 虚拟环境，并根据 `package-lock.json` 运行 `npm ci` 安装锁定的前端依赖：

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
GATEWAY_BASE_URL=https://gateway.example.invalid
ENABLE_GATEWAY_VIDEO=1
```

`gateway.example.invalid` 是不可路由的文档占位符。使用网关时必须在本机 `.env` 中显式替换
`GATEWAY_BASE_URL`；代码没有私有网关默认地址。

豆包 TTS 可使用 Speech API Key，或 AppID + Access Key 的流式凭据。真实密钥只放在 `.env`，不要提交到仓库。

不配置 Provider 也可以使用本机工作台、运行本地阶段、审核历史作品并执行离线测试。收费生成只有在视频预检、费用确认和显式提交全部完成后才会启动。

## 本机制作工作台

一个命令同时启动 Python 制作 API 和 React 工作台：

```bash
.venv/bin/python scripts/run_workbench.py
```

启动器只监听本机回环地址，自动避开占用端口，并在 API 与页面都可用后打印实际地址。`Ctrl+C`、`SIGINT` 或 `SIGTERM` 会一起关闭两个子进程；命令不会修改 `.env`。

工作台使用固定九阶段流程：

1. 在“制作项目”创建原创、小说改编或参考复刻项目，并选择快速、标准或严格审批模板。
2. 在项目工作区运行当前阶段，检查有版本和哈希绑定的成果，再确认通过或退回修改。
3. 局部修改先展示影响计划；只有再次确认“应用返修计划”后，受影响阶段和镜头才会失效，未受影响素材继续复用。
4. 视频阶段先核对 Provider、模型、镜头、时长和费用。测试镜头限制为 1 至 3 个；测试结果通过后再提交整批生成。
5. 交付审核完成后，版本进入“作品中心”，可查看母版、历史版本、EVAL、迭代说明和受控下载。

审批模板不会关闭收费视频门禁、客观 EVAL 门禁或最终交付确认。作业中断后优先恢复已有任务状态，局部返修也不会自动重新提交收费请求。

旧展示站的 7 个公开文件已按 SHA-256 迁入 `assets/workbench_archive/`：3 个音频归入历史音色作品，4 个未归类 SVG 保留在历史归档。页面会持续显示“发布权利尚未核验”；其中音频样本在权利得到书面确认或从发布内容排除前，不应进入公开仓库或再分发。因此后续 GitHub 发布默认使用私有仓库。

## 安全发布快照

源仓库的旧提交曾包含一项真实凭据。当前 tracked tree 已替换为明显虚构的测试哨兵，
但旧历史不得创建或推送到 GitHub。该凭据仍必须由账户持有人在外部撤销或轮换；仓库内的
代码和扫描无法完成这一步。

发布必须从当前已提交且干净的 tracked tree 创建单提交新历史：

```bash
.venv/bin/python scripts/release_security.py
.venv/bin/python scripts/export_clean_release.py /path/to/fresh-storymotion-release
```

导出器只读取 `git archive HEAD` 中的当前 tracked 文件，不复制 `.git` 或 ignored 产物，
在新目录初始化单个 release commit，并对当前内容和新历史执行确定性密钥扫描；本机存在
`gitleaks` 时还会同时扫描内容与历史。发布者必须从该新目录创建默认私有的 GitHub 仓库，
不得从本工作树或其既有 Git 历史推送。归档音频权利确认前不得改为公开仓库。

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
  --project-dir runs/approved-project \
  --shot-id H3-01 \
  --confirm-paid \
  --enable-live
```

`--confirm-paid` 会从已通过构思、剧本、分镜、素材和音频审批的项目中签发一次性确认令牌，且 `--shot-id` 的时长与分辨率必须和实际提交一致。任务 ID、轮询次数、MiniMax 返回的用量和预估人民币费用会写入生成报告。省略 `--enable-live` 只生成计划，不会发起云端请求。

MiniMax H3 使用官方结构化提示词，而不是把通用视频提示词原样发送：

- 无参考素材时输出 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。
- 有角色参考图时输出 Ref2VA 的六段结构，并用稳定的 `<Subject N>`、`<Picture N>` 和 `(S1)` 标签绑定角色与说话人。
- 中文对白原样放入 `<d>[Chinese] ...</d>`，画外旁白明确要求可见角色闭嘴，人物对白明确要求开口与音节同步并在句末闭嘴。
- H3 适配器支持 `first_frame`、`last_frame`、`reference_image` 三种图片角色；普通路径继续按角色参考图处理。

MiniMax H3 官方源码仅作为仓库外的规范参考，不是本项目运行依赖。当前发布音色仍以豆包 TTS 为准；H3 原生声音不会与最终音轨叠加。提示词负责自然开口，逐字精确口型仍需经过项目的口型后处理和 EVAL。

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
.venv/bin/ruff check factory factory_cli.py scripts tests
.venv/bin/python -m compileall -q factory factory_cli.py scripts
.venv/bin/pytest -q
cd sites/storymotion-studio
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test
```

这套验证使用本地夹具覆盖创建、阶段执行、审批、退回、影响计划、视频预检、任务恢复、作品和设置，不调用外部 Provider，不产生费用，也不修改 `.env`。

浏览器流程启动真实临时 FastAPI/WorkbenchService，并通过正式文件、作业、审核、返修、媒体
Range 和下载合同验收；只有付费视频渲染边界被离线夹具替代。发布前还要从 clean snapshot
重复执行本节矩阵和历史密钥扫描。

代码职责见 [docs/pipeline-code-map.md](docs/pipeline-code-map.md)，部署与密钥管理见 [docs/deployment.md](docs/deployment.md)，完整 bad case 与迭代过程保留在 [docs/iteration-log.md](docs/iteration-log.md)。
