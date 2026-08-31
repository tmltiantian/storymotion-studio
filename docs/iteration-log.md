# 迭代日志

> 英文原文保留在 [`iteration-log.en.md`](iteration-log.en.md)。本文件是对应的中文版本；命令、文件路径、版本号和技术标识保持原样，方便核对。

## 2026-07-09 — v0.1 干跑包

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode --title 旧城来信 --shots 8
```

结果：

- `5 passed`。
- 生成 `runs/sample_episode/episode.json`。
- 生成 `runs/sample_episode/openmontage_package.json`。
- 生成 `runs/sample_episode/status.json`。

发现的问题：

- 第一版规划器把短文本过度合并成了一个镜头。
- 根因是：即使句子数已经低于目标镜头数，`split_story_beats` 仍按字符数合并句子。
- 修复方式：当 `len(raw_parts) <= target_count` 时，直接返回句子级节拍。

下一步：

- 增加启动脚本。
- 增加 LumenX 服务启动检查。
- 增加 OpenMontage 包校验器。

## 2026-07-09 — v0.2 运行时探测与交接校验

命令：

```bash
scripts/start_factory.sh
```

结果：

- `7 passed`。
- 生成 `runs/runtime_probe.json`。
- 重新生成 `runs/sample_episode/episode.json`。
- 重新生成 `runs/sample_episode/openmontage_package.json`。

新增：

- 为 LumenX、AIComicBuilder 和 OpenMontage 增加静态运行时探测。
- 增加 OpenMontage 包校验器。
- 启动脚本现在会依次运行测试、运行时探测和样例规划。

下一步：

- 从剧集包生成最小预览产物。
- 在包契约稳定后接入真实 OpenMontage 执行。

## 2026-07-09 — v0.3 预览产物与对白提取

命令：

```bash
scripts/start_factory.sh
```

结果：

- `10 passed`。
- 生成 `runs/sample_episode/storyboard_preview.md`。
- 生成 `runs/sample_episode/subtitles.srt`。
- 第 4 条字幕现在使用小说中的实际对白：`最后一班车不是开往城外，而是开往十年前`。

新增：

- 分镜 Markdown 预览。
- SRT 字幕生成。
- 剧集快照 JSON。
- 从“说，……”文本中提取基础口语对白。

下一步：

- 创建占位视觉预览视频或 OpenMontage 渲染计划执行器。
- 用更聪明的场景化反应替换重复对白回退方案。

## 2026-07-09 — v0.4 占位预览 MP4

命令：

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries format=duration -show_streams -of json runs/sample_episode/placeholder_preview.mp4
```

结果：

- `12 passed`。
- 生成 `runs/sample_episode/placeholder_preview.mp4`。
- ffprobe 确认：
  - 1080x1920 H.264 视频；
  - 30 fps；
  - 45.0 秒；
  - AAC 立体声音轨；
  - mov_text 字幕轨。

新增：

- 占位 FFmpeg 渲染器。
- 从镜头时长计算运行时总时长。
- 在 `factory_cli.py plan` 中生成 MP4 预览。

下一步：

- 为社交媒体预览直接烧录可见字幕。
- 图片生成配置完成后，用逐镜静帧卡或 OpenMontage 场景替换占位色块视频。

## 2026-07-09 — v0.5 逐镜分镜卡预览

命令：

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries format=duration -show_streams -of json runs/sample_episode/card_preview.mp4
```

结果：

- `15 passed`。
- 生成 `runs/sample_episode/cards/shot_001.png` 至 `shot_006.png`。
- 生成 `runs/sample_episode/cards/cards.ffconcat`。
- 生成 `runs/sample_episode/card_preview.mp4`。
- ffprobe 确认：1080x1920 H.264 视频、30 fps、45.0 秒、AAC 立体声音轨和 mov_text 字幕轨。
- 目视检查 `shot_001.png`，确认中文文字可读，布局未超出卡片范围。

新增：

- 基于 Pillow 的分镜卡渲染器。
- 用于逐镜卡片时序的 FFmpeg concat 清单。
- 在 `factory_cli.py plan` 中生成卡片预览 MP4。

下一步：

- 增加 LumenX 交接／导出 JSON，让干跑包可被生产流程消费。
- 图片生成配置完成后，用生成的首帧替换静态卡片。

## 2026-07-09 — v0.6 LumenX 交接契约

命令：

```bash
scripts/start_factory.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
```

结果：

- `19 passed`。
- 生成 `runs/sample_episode/lumenx_handoff.json`。
- 交接包包含：
  - `create_project`：`POST /projects?skip_analysis=true`；
  - `add_characters`：2 个请求；
  - `add_scenes`：1 个请求；
  - `add_frames`：6 个请求；
  - `update_frames`：6 个带 `requires_frame_index` 映射提示的请求。
- 已验证交接包保留角色、帧提示词、对白、镜头、时长与 LumenX `r2v` 工作流模式。

新增：

- `factory/lumenx_adapter.py`。
- 类脚本形式的 LumenX 离线导出。
- 供后续真实服务接入使用的 LumenX API 执行计划。

下一步：

- 增加可选的 LumenX 在线健康检查和干跑 API 执行器。
- 在尝试真实生成前增加 API 密钥／环境状态报告。

## 2026-07-09 — v0.7 LumenX 在线健康桥接

命令：

```bash
scripts/start_factory.sh
cat runs/lumenx_health.json
```

结果：

- `22 passed`。
- 生成 `runs/lumenx_health.json`。
- 当前在线 LumenX 状态：不可用。
- 已记录错误：`http://localhost:17177/health` 返回 `Connection refused`。
- 离线工厂生成仍成功完成。

新增：

- `factory/lumenx_live.py`。
- `factory_cli.py lumenx-health`。
- 部署脚本会报告 LumenX 在线就绪情况，但不会阻断干跑输出。

下一步：

- 增加环境／API 密钥就绪报告。
- 在依赖安装或文档齐备后，受控地尝试启动 LumenX 后端。

## 2026-07-09 — v0.8 环境就绪状态报告

命令：

```bash
scripts/start_factory.sh
cat runs/env_readiness.json
```

结果：

- `26 passed`。
- 生成 `runs/env_readiness.json`。
- 当前生成就绪状态：`ready_for_lumenx_generation=false`。
- 缺少必需密钥：`DASHSCOPE_API_KEY`。
- 缺少可选密钥：MuleRun、Kling、Vidu、OSS、阿里云相关密钥。
- 离线工厂生成仍成功完成。

新增：

- `factory/env_readiness.py`。
- `factory_cli.py env-report`。
- 部署脚本现在会报告缺失的必需／可选生成凭据，但不会输出密钥值。

下一步：

- 增加受控的 LumenX 后端启动说明／检查。
- 配置 `DASHSCOPE_API_KEY` 后，使用 `lumenx_handoff.json` 尝试在线创建项目。

## 2026-07-09 — v0.9 LumenX 后端启动诊断

命令：

```bash
scripts/start_factory.sh
cat runs/lumenx_bootstrap.json
```

结果：

- `30 passed`。
- 生成 `runs/lumenx_bootstrap.json`。
- LumenX 源码根目录、`requirements.txt`、`start_backend.sh`、`package.json` 和 `src/apps/comic_gen/api.py` 均存在。
- 当前后端启动就绪状态：`ready_to_start_backend=false`。
- 缺少的后端 Python 模块包括 `fastapi`、`uvicorn`、`python-multipart`、`dashscope`、`pydantic-settings`、`PyJWT`、阿里云 SDK 包、`oss2`、`demucs` 和 `soundfile`。
- `package.json` 引用的根目录 Node 辅助脚本在精简检出中不存在；但 Python 依赖安装后，仍可直接通过 uvicorn 启动。
- 在线健康检查仍显示 `http://localhost:17177/health` 返回 `Connection refused`。

新增：

- `factory/lumenx_bootstrap.py`。
- `factory_cli.py lumenx-bootstrap`。
- 部署脚本会在在线健康检查前写入后端启动诊断。

下一步：

- 为 LumenX 创建或选择独立的 Python 虚拟环境，不再复用 OpenMontage 的虚拟环境。
- 在该环境安装 LumenX 后端依赖，并重新受控地尝试启动本地后端。
- 后端健康检查成功且已配置 `DASHSCOPE_API_KEY` 后，根据 `lumenx_handoff.json` 尝试在线创建项目。

## 2026-07-09 — v1.0 LumenX 后端在线冒烟验证

命令：

```bash
scripts/bootstrap_lumenx_backend.sh
scripts/start_lumenx_backend.sh
scripts/start_factory.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-execute-handoff --handoff runs/sample_episode/lumenx_handoff.json
```

结果：

- `35 passed`。
- 在 `external/lumenx/.venv` 创建独立的 LumenX Python 3.12 虚拟环境。
- 安装 API 启动所需后端依赖，未改动 OpenMontage 的虚拟环境。
- `runs/lumenx_bootstrap.json` 现在报告 `ready_to_start_backend=true`。
- LumenX 后端已在 `http://127.0.0.1:17177` 启动。
- `runs/lumenx_health.json` 报告 `available=true`。
- 已针对在线 LumenX 后端执行 `runs/sample_episode/lumenx_handoff.json`。
- 生成 `runs/sample_episode/lumenx_live_execution.json`。
- 在线 LumenX 项目 ID：`6824176a-34b1-430f-b01f-dc043ff6db00`。
- 最终在线数量：2 个角色、1 个场景、6 帧。

新增：

- `scripts/bootstrap_lumenx_backend.sh`。
- `scripts/start_lumenx_backend.sh`。
- `factory/lumenx_live_executor.py`。
- `factory_cli.py lumenx-execute-handoff`。
- 含本地 ID 至 LumenX ID 映射的在线执行报告。

仍待解决：

- 仍未配置 `DASHSCOPE_API_KEY`，因此尚未尝试真实图片／视频／TTS 生成。
- Demucs 和桌面 WebView 支持等完整运行时包在 API 启动虚拟环境中仍为可选／缺失项。

下一步：

- 增加受保护的生成步骤：凭据未配置或用户未明确启用真实生成时，拒绝消耗额度。
- 配置 `DASHSCOPE_API_KEY` 后，接入首帧／图片生成。

## 2026-07-10 — v1.1 受保护的真实生成门禁

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
cat runs/sample_episode/lumenx_generation_report.json
```

结果：

- `40 passed`。
- 部署脚本完成执行，受保护生成步骤以仅规划模式启用。
- 生成 `runs/sample_episode/lumenx_generation_report.json`。
- 规划的真实生成端点：
  - `POST /projects/{script_id}/generate_assets`；
  - `POST /projects/{script_id}/generate_storyboard`；
  - `POST /projects/{script_id}/generate_audio`；
  - `POST /projects/{script_id}/generate_video`。
- 默认报告为 `executed=false` 且 `requests=[]`。
- 当前阻塞原因明确：真实生成未启用，且缺少 `DASHSCOPE_API_KEY`。

新增：

- `factory/lumenx_generation_guard.py`。
- `factory_cli.py lumenx-generate-live`。
- 仅规划模式、缺少凭据、启用执行与 CLI 退出码的门禁测试。
- 当在线执行报告存在时，`scripts/start_factory.sh` 会写入生成门禁报告。

下一步：

- 在 `external/lumenx/.env` 或进程环境中配置 `DASHSCOPE_API_KEY`。
- LumenX 后端运行时，先执行小范围受保护阶段，建议使用 `--stages audio --enable-real-generation`，再进行完整图片／视频生成。

## 2026-07-10 — v1.2 端到端就绪验证器

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
cat runs/end_to_end_readiness.json
```

结果：

- `44 passed`。
- 部署脚本现在写入 `runs/end_to_end_readiness.json`。
- 当前就绪状态：
  - `overall_status=demo_ready_blocked_for_real_generation`；
  - `demo_ready=true`；
  - `goal_ready=false`；
  - 通过检查：11 项；
  - 警告：1 项（`lumenx_backend_health`，因为后端当前未运行）；
  - 失败检查：0 项；
  - 被阻塞检查：2 项（`environment_credentials`、`real_generation`）。

新增：

- `factory/end_to_end_readiness.py`。
- `factory_cli.py readiness`。
- 就绪报告生成与 CLI 行为测试。
- `scripts/start_factory.sh` 现在会在样例生成后输出最终就绪摘要。

下一步：

- 提供 `DASHSCOPE_API_KEY`。
- 启动 LumenX 后端，并运行 `factory_cli.py lumenx-generate-live --execution runs/sample_episode/lumenx_live_execution.json --stages audio --enable-real-generation`。
- 重新运行 `factory_cli.py readiness --project sample_episode`，确认阻塞项减少后再启用分镜／视频生成。

## 2026-07-10 — v1.3 本地带配音预览

命令：

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of json runs/sample_episode/card_preview_voiced.mp4
ffmpeg -hide_banner -i runs/sample_episode/card_preview_voiced.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | rg 'mean_volume|max_volume'
```

结果：

- `50 passed`。
- 生成 `runs/sample_episode/card_preview_voiced.mp4`。
- 生成 `runs/sample_episode/voiceover/voiceover.m4a`。
- 生成 `runs/sample_episode/voiceover/voiceover_script.txt`。
- `ffprobe` 确认视频、AAC 音频与字幕流均存在。
- `volumedetect` 确认旁白不是静音：
  - `mean_volume: -15.0 dB`；
  - `max_volume: -0.5 dB`。
- 端到端就绪检查现已通过 14 项，`demo_ready=true`、`goal_ready=false`。

新增：

- `factory/local_voiceover.py`。
- 本地 macOS `say` 语音提示规划。
- 逐句 AIFF 片段生成、延时 FFmpeg 混音以及带配音 MP4 封装。
- 针对带配音预览视频、旁白音频和旁白脚本的就绪检查。

下一步：

- 配置 `DASHSCOPE_API_KEY` 后，用 LumenX／DashScope TTS 替换本地 macOS 配音。
- 保留本地配音，作为零成本剧情与节奏审看回退方案。

## 2026-07-10 — v1.4 OpenMontage 后期最终预览

命令：

```bash
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode/final_preview.mp4
ffmpeg -hide_banner -i output/sample_episode/final_preview.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | rg 'mean_volume|max_volume'
```

结果：

- `55 passed`。
- 生成 `output/sample_episode/final_preview.mp4`。
- 生成 `runs/sample_episode/openmontage_post_report.json`。
- `ffprobe` 确认最终预览包含 H.264 视频、AAC 音频和 mov_text 字幕。
- 最终预览时长为 45.0 秒。
- `volumedetect` 确认最终预览音频不是静音：
  - `mean_volume: -15.1 dB`；
  - `max_volume: -0.5 dB`。
- 端到端就绪状态现在包含 `openmontage_post_report` 与 `final_preview_video`，且 `demo_ready=true`、`goal_ready=false`。

新增：

- `factory/openmontage_post.py`。

`factory_cli.py plan` 现在会在本地配音后写入最终预览交付文件。

- OpenMontage 后期报告包含已探测到的 OpenMontage 路径、候选工具、时间线数量、源预览和最终预览路径。
- 增加 OpenMontage 后期报告与最终预览视频的就绪检查。

仍待解决：

- 当前后期步骤使用工厂的 FFmpeg 收尾器，同时保留 OpenMontage 交接包并报告探测到的 OpenMontage 候选工具。
- 选定稳定的 OpenMontage CLI 契约后，可用真正的 OpenMontage 内部渲染替换该收尾器。
- 仍缺少 `DASHSCOPE_API_KEY`，所以真实 LumenX 图片／视频／TTS 生成仍被阻塞。

下一步：

- 在对照 `openmontage_package.json` 验证命令契约后，接入一个明确的 OpenMontage 工具路径，可能是字幕烧录或 Remotion 合成器。
- 配置 `DASHSCOPE_API_KEY` 后，先执行一个小型在线 LumenX 阶段，再启用完整视频生成。

## 2026-07-10 — v1.5 OpenMontage Remotion 字幕烧录

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
ffprobe -v error -show_entries stream=index,codec_type,codec_name,width,height,duration:format=duration,bit_rate -of json output/sample_episode/final_preview.mp4
ffmpeg -hide_banner -i output/sample_episode/final_preview.mp4 -map 0:a:0 -af volumedetect -f null - 2>&1 | rg 'mean_volume|max_volume'
ffmpeg -y -i output/sample_episode/final_preview.mp4 -vf "fps=1/5,scale=270:-1,tile=3x3" -frames:v 1 runs/sample_episode/final_preview_contact_sheet.jpg
```

结果：

- `58 passed`。
- `scripts/start_factory.sh` 通过 OpenMontage 的 `remotion_caption_burn` 工具生成 `output/sample_episode/final_preview.mp4`。
- `runs/sample_episode/openmontage_post_report.json` 现在报告 `mode=openmontage_remotion_caption_burn`。
- 最终预览为 1080x1920 H.264 视频、AAC 音频，时长 45.056 秒。
- `volumedetect` 确认最终预览音频不是静音：
  - `mean_volume: -18.1 dB`；
  - `max_volume: -3.1 dB`。
- 已目视检查 `runs/sample_episode/final_preview_frame_20s.png` 与 `runs/sample_episode/final_preview_contact_sheet.jpg`。
- 第一版 Remotion 字幕因 SRT 行不含空格而叠加出过大的中文字幕；工厂现会在调用 OpenMontage 前将 SRT 提示拆为短中文字幕片段。

新增：

- 在 `factory/openmontage_post.py` 中接入 OpenMontage `RemotionCaptionBurn`。
- OpenMontage 字幕烧录不可用时，回退到现有 FFmpeg 流复制收尾器。
- 用于中文字幕分段计时的 `build_caption_segments_from_srt`。
- `factory_cli.py plan` 现在将 `subtitles.srt` 传给 OpenMontage 后期步骤。

仍待解决：

- 视觉层仍是工厂分镜卡预览，并非 LumenX 生成的 AI 角色／视频帧。
- 仍缺少 `DASHSCOPE_API_KEY`，所以真实 LumenX 图片／视频／TTS 生成仍被阻塞。

下一步：

- 配置 `DASHSCOPE_API_KEY`、启动 LumenX 后端，先执行音频等小型真实阶段，再进行完整分镜／视频生成。
- LumenX 帧生成后，将其接入同一 OpenMontage Remotion 后期步骤。

## 2026-07-10 — v1.6 OpenMontage 后的 LumenX 在线复验

命令：

```bash
scripts/start_lumenx_backend.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-health --base-url http://127.0.0.1:17177 --timeout 2
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-execute-handoff --handoff runs/sample_episode/lumenx_handoff.json --base-url http://127.0.0.1:17177 --timeout 20
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-generate-live --execution runs/sample_episode/lumenx_live_execution.json --stages assets,storyboard,audio,video
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py readiness --project sample_episode
```

结果：

- LumenX 后端健康检查在 `http://127.0.0.1:17177/health` 通过。
- 已针对在线 LumenX 重新执行 `runs/sample_episode/lumenx_handoff.json`。
- 在线 LumenX 项目 ID：`694cf063-e843-482e-aeaf-5f74d9bf6153`。
- 最终在线数量：2 个角色、1 个场景、6 帧。
- 受保护的生成计划默认仍拒绝消耗额度。
- 端到端就绪状态现在报告：
  - `passed=17`；
  - `warning=0`；
  - `failed=0`；
  - `blocked=2`；
  - `demo_ready=true`；
  - `goal_ready=false`。

仍待解决：

- 仍缺少 `DASHSCOPE_API_KEY`。
- 真实 LumenX 阶段仍未执行：资产、分镜、音频、视频。

下一步：

- 将 `DASHSCOPE_API_KEY` 加入 `external/lumenx/.env` 或 shell 环境。
- 按阶段运行受保护的真实生成：先 `audio`，再 `assets/storyboard`，最后 `video`。

## 2026-07-10 — v1.7 LumenX 在线运行聚合器

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-live-run --project sample_episode --base-url http://127.0.0.1:9 --timeout 0.1
scripts/start_lumenx_backend.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-live-run --project sample_episode --base-url http://127.0.0.1:17177 --timeout 20 --stages audio
```

结果：

- `61 passed`。
- 新增 `factory_cli.py lumenx-live-run`。
- 新增汇总在线冒烟报告 `runs/sample_episode/lumenx_live_run.json`。
- 后端不可用时会写入失败报告并返回非零状态，不会尝试交接或生成。
- 在线后端路径以一条命令通过健康检查、交接、生成门禁规划和就绪检查。
- 在线 LumenX 项目 ID：`6a82fd84-1c24-46e7-9b93-5d01d31d9c3f`。
- 最终在线数量：2 个角色、1 个场景、6 帧。
- `audio` 阶段仍仅规划，`generation_executed=false`。

新增：

- `factory/lumenx_live_pipeline.py`。
- 成功在线运行时序、后端不可用时的停止行为和 CLI 报告写入测试。
- 新聚合命令的 README 与部署文档。

仍待解决：

- 仍缺少 `DASHSCOPE_API_KEY`。
- 尚未运行 `--enable-real-generation`，完整 LumenX 生成目标仍未完成。

下一步：

- 配置 `DASHSCOPE_API_KEY` 后运行：
  `/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py lumenx-live-run --project sample_episode --base-url http://127.0.0.1:17177 --stages audio --enable-real-generation`

## 2026-07-10 — v1.8 自动启动后端的在线流程封装器

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
LUMENX_GENERATION_STAGES=audio LUMENX_TIMEOUT=20 LUMENX_HEALTH_ATTEMPTS=20 scripts/run_lumenx_live_pipeline.sh
lsof -nP -iTCP:17177 -sTCP:LISTEN || true
```

结果：

- `63 passed`。
- 新增 `scripts/run_lumenx_live_pipeline.sh`。
- 该封装器启动 LumenX 后端，等待健康 JSON 中出现 `"available": true`，运行 `factory_cli.py lumenx-live-run`，然后清理后端进程。
- 已使用真实本地后端验证该封装器。
- 在线 LumenX 项目 ID：`90c4dc30-0eee-44a6-83bd-76f05480a6d4`。
- 最终在线数量：2 个角色、1 个场景、6 帧。
- `lsof` 确认封装器退出后端口 `17177` 没有残留监听进程。

发现的问题：

- 第一版封装器将 `factory_cli.py lumenx-health` 的退出码 `0` 视为服务就绪，即使 JSON 中写的是 `"available": false`。
- 修复方式：封装器现在等待健康检查命令输出包含 `"available": true`。

仍待解决：

- 真实生成仍需要 `DASHSCOPE_API_KEY`。

下一步：

- 配置密钥后运行 `ENABLE_REAL_GENERATION=1 LUMENX_GENERATION_STAGES=audio scripts/run_lumenx_live_pipeline.sh`。

## 2026-07-10 — v1.9 角色参考资产接入

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
scripts/start_factory.sh
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode_assets --title 旧城来信 --shots 8 --character-assets /tmp/.../character_assets.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode_assets/final_preview.mp4
```

结果：

- `69 passed`。
- 默认 `scripts/start_factory.sh` 仍以 `demo_ready=true` 完成；`character_assets_ready=false` 被报告为警告，而不是演示阻塞项。
- 新增 `runs/<project>/character_assets.json`。
- 新增可选参数 `factory_cli.py plan --character-assets <manifest.json>`。
- 已验证：含两个本地图片路径的清单会产生 `character_assets_ready=true`。
- 已验证 LumenX 交接包包含：
  - 含参考图路径的角色描述；
  - `api_plan.update_character_images`；
  - 两个角色对应的 `/projects/{script_id}/assets/update_image` 载荷。
- 已验证 OpenMontage 包携带同样的角色参考图路径。
- `output/sample_episode_assets/final_preview.mp4` 是有效 H.264/AAC，时长 45.056 秒。

新增：

- `factory/character_assets.py`。
- 通过 LumenX 交接包、OpenMontage 包、CLI 状态与就绪检查传递角色参考资产。
- 在线 LumenX 执行器支持在在线角色 ID 映射后执行 `update_character_images`。

仍待解决：

- 测试清单以现有分镜 PNG 充当占位图；应以用户提供的 AI 角色图替换。
- 真实 LumenX 生成仍需要 `DASHSCOPE_API_KEY`。

下一步：

- 得到真实角色图与 `DASHSCOPE_API_KEY` 后，使用 `--character-assets` 规划，再运行 `ENABLE_REAL_GENERATION=1 LUMENX_GENERATION_STAGES=audio scripts/run_lumenx_live_pipeline.sh`。

## 2026-07-10 — v1.10 角色资产模板命令

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-assets-template --input samples/sample_novel.txt --project sample_episode_template --title 旧城来信 --output /tmp/.../character_assets.template.json
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode_template --title 旧城来信 --shots 2 --character-assets /tmp/.../character_assets.filled.json
ffprobe -v error -show_entries stream=index,codec_type,codec_name,duration:format=duration -of compact=p=0:nk=1 output/sample_episode_template/final_preview.mp4
```

结果：

- `71 passed`。
- 新增 `factory_cli.py character-assets-template`。
- 该命令从小说中提取规划出的两名角色，并写入一个可填写的清单，包含 `character_id`、`name` 与空白 `reference_image` 字段。
- 已验证：将两个图片路径填入模板后，可传给 `plan --character-assets`。
- 已验证生成的 `runs/sample_episode_template/character_assets.json` 报告 `asset_ready=true`。
- 已验证 LumenX 交接包仍包含 `api_plan.update_character_images`。
- 已验证 OpenMontage 包携带相同角色参考图路径。
- `output/sample_episode_template/final_preview.mp4` 是有效 H.264/AAC，两镜冒烟验证时长 15.061 秒。

仍待解决：

- 真实用户生成的 AI 角色图应替换冒烟验证所使用的临时占位图。
- 真实 LumenX 生成仍需要 `DASHSCOPE_API_KEY`。

下一步：

- 新小说规划前先运行 `character-assets-template`，以便将生成的角色图接入 LumenX 与 OpenMontage，而无需手写 ID。

## 2026-07-10 — v1.11 角色生成简报

命令：

```bash
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests/test_character_brief.py -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python -m pytest tests -q
/Users/tml/Desktop/洋葱样片/OpenMontage/.venv/bin/python factory_cli.py character-brief --input samples/sample_novel.txt --project sample_episode --title 旧城来信 --output runs/sample_episode/character_generation_brief.json
```

结果：

- `73 passed`。
- 新增 `factory_cli.py character-brief`。
- 新增 `factory/character_brief.py`。
- 命令提取规划剧集的角色，并为参考图、全身设计和三视图写出 AI 角色图提示词。
- 简报包含可直接填写的 `character_assets_template` 区块，建议路径为 `assets/characters/*_reference.png`。
- 已验证样例输出 `runs/sample_episode/character_generation_brief.json`，其中包含林澈与苏眠。

仍待解决：

- 真实角色图仍需由用户选定的图片模型生成并保存到建议路径，或修改生成清单中的路径。
- 真实 LumenX 生成仍需要 `DASHSCOPE_API_KEY`。

下一步：

- 图片文件就绪后，增加一个辅助工具，将简报的 `character_assets_template` 区块转换为独立清单。

## 2026-07-10 — v1.12 至 v1.24：角色参考图、生成门禁与交接包

这一阶段把“有角色图”进一步收紧为“可以安全用于生产的角色图”，并把是否能真正发起付费生成变成可检查、可交接的状态。

### v1.12 角色简报清单导出

- 新增 `factory_cli.py character-assets-from-brief`，把角色简报中的 `character_assets_template` 导出为 `plan --character-assets` 可直接使用的独立清单。
- `--require-files` 在图片缺失时返回结构化 JSON（`success=false`），而不再抛出 Python 堆栈。
- 冒烟验证通过：`77 passed`；两镜预览输出为有效 H.264/AAC，时长 15.061 秒。
- 后续需要用真实生成的角色图替换建议路径中的占位文件，并验证图片内容而不只验证路径存在。

### v1.13 角色参考图签名校验

- 接入 PNG／JPEG／WEBP 文件签名检查；空文件或伪装成图片的内容会报 `invalid character reference image`。
- 冒烟验证通过：`78 passed`；真实 PNG 取代本地占位后，角色资产、LumenX 交接和预览仍能通过。
- 这只是轻量文件校验，不等同于角色一致性的感知质量审核。

### v1.14–v1.15 操作员工作流状态

- 新增 `factory/workflow_status.py` 和 `factory_cli.py workflow-status`；向 `runs/<project>/workflow_status.json` 写入统一的生产状态。
- 报告覆盖总体就绪状态、缺失凭据、LumenX 启动／健康／在线项目／真实生成状态、角色参考图、预览路径、阻塞项和下一步操作。
- `scripts/start_factory.sh` 现会在就绪检查后自动写入这份状态。阶段测试从 `81 passed` 增至 `82 passed`。
- 当时的主要阻塞项是 `environment_credentials` 与 `real_generation`。

### v1.16 音频单阶段真实生成冒烟

- 新增 `scripts/run_lumenx_audio_smoke.sh`；它先检查 `DASHSCOPE_API_KEY`，只有凭据就绪才委派给 `scripts/run_lumenx_live_pipeline.sh`。
- 缺少密钥时会产出结构化报告：`success=false`、`executed=false`、`stages=["audio"]`，并列出阻塞原因，而不会误启动生成。
- 该阶段只覆盖音频；完整视频仍必须在音频通过后单独批准。

### v1.17–v1.19 角色资产状态与来源证明

- 新增 `character-assets-status`，逐角色报告建议路径、绝对路径、文件是否存在、图片签名、`ready`／`missing`／`invalid` 状态。
- 启动脚本会自动接入已就绪的角色参考清单，并把参考图路径传给 LumenX 与 OpenMontage。
- 增加 `asset_source`、`provenance_status` 和 `production_ready`：本地冒烟图即使有效，也不能被误当作生产角色图。
- 生产来源规则是 `asset_source=user_generated_ai`；来源不明、占位或未确认时，工作流会提示 `confirm_character_reference_provenance`。

### v1.20–v1.23 视觉生成门禁与来源确认

- 将 `assets`、`storyboard`、`video` 设为视觉生成阶段；真实视觉生成必须满足角色资产 `production_ready=true`。
- 音频阶段可先通过来源门禁，作为配置 `DASHSCOPE_API_KEY` 后成本最低的真实验证。
- 新增 `real-generation-preflight`，将 `audio_ready` 与 `visual_ready` 分开报告，并逐阶段说明阻塞原因。
- 新增 `character-assets-confirm-source`：仅在文件和图片签名通过后，才将审核过的真实角色图标记为 `asset_source=user_generated_ai`、`provenance_status=confirmed` 与 `production_ready=true`；`smoke_placeholder` 等不受信来源会被拒绝。

### v1.24 操作员交接包

- 新增 `factory/operator_handoff.py` 与 `factory_cli.py operator-handoff`。
- 生成 `runs/sample_episode/operator_handoff.json` 和 `runs/sample_episode/operator_handoff.md`，汇总演示／目标状态、预览路径、在线 LumenX 项目 ID、音频／视觉真实生成就绪状态，以及配置 `.env`、确认角色来源、音频冒烟与受保护完整生成的精确下一步。
- `.env` 建议采用非破坏式 `test -f ... || cp ...`，避免覆盖既有配置。

## 2026-07-10 — v1.25 至 v1.46：生产入口、仪表盘与本地模拟验证

这一阶段把上一阶段的检查串成稳定入口，并补齐失败诊断、人工审核资产接入和不消耗额度的本地模拟验证。

- v1.25–v1.32：加入自启动真实生成交接、审核角色图模板／目录导入／投放目录自动安装、`start_factory` 委派和统一操作员入口，减少人工填写 ID 与路径。
- v1.33–v1.35：新增真实生成启动门禁、交接包内的启动门禁和操作员解阻清单；未满足凭据、来源或审批条件时不会越过付费门槛。
- v1.36：新增本地工厂仪表盘，汇集制作状态、预览、阻塞原因和下一步。
- v1.37–v1.39：补齐真实生成失败诊断、音频冒烟报告刷新和缺少密钥时的可读报告，确保失败不再表现为静默或误成功。
- v1.40–v1.41：把角色资产操作写入交接包和仪表盘，使审核／安装动作在同一处可见。
- v1.42–v1.43：以隔离的模拟 LumenX 后端进行在线冒烟验证并保留独立产物，验证请求时序而不消耗真实服务额度。
- v1.44–v1.46：完善人工审核角色图的接收报告、生产角色图安装状态和带角色感知的本地预览；只有审核通过的真实角色图才会进入视觉生产路径。

## 2026-07-13 — v1.47 至 v1.52：网关路由与 Seedance 视频生产

- v1.47：接入能力感知的网关路由，按模型能力和任务类型选路。
- v1.48：接入网关 Seedance 视频生产管线。
- v1.49：增加视频安全与可恢复机制，避免重复提交／重复扣费。
- v1.50：获得首条在线 Seedance 片段，并以混合 OpenMontage 预览验证剪辑路径。
- v1.51：补强生产可靠性和持久化工作进程。
- v1.52：完成六镜生产剪辑，并收紧最终硬性校验。

## 2026-07-14 至 2026-07-20 — v1.53 至 v1.63：声音、微镜头质量与可恢复制作

- v1.53：修复感知音量与运动节奏问题。
- v1.54：验证豆包 TTS，并加入音频母带处理。
- v1.55：建立微镜头质量生产路径。
- v1.56：加入多角色豆包音色与镜头边界裁切。
- v1.57：将质量就绪状态和基线审计改为如实报告。
- v1.58：加入在线对照测试诊断与针对性候选片段迭代。
- v1.59：加入可恢复生产控制和双角色修订。
- v1.60：强化 OCR 精度与可观察的双角色表演。
- v1.61：引入有审计证据的生产批次和精细 QC。
- v1.62：完成微剪辑、人工 OCR 恢复与音画对齐。
- v1.63：进行双角色说话 A/B 测试与场景连续性验证。

## 2026-07-27 至 2026-08-18 — v1.64 至 v1.71：成片闭环、坏例修复与 H3 提示词协议

- v1.64：重建“宠物情景剧”音频优先连续性流程。
- v1.65：完成宠物任务 2 定版、成片自检和证据链修复。
- 2026-07-29：纠正“冻干案”发布版中的第二轮坏样片。
- v1.66：完成 160 秒《斑斑来访》长片生产，并闭环处理全片坏样片。
- v1.67：对《猫猫鬼点子》逐镜重拍，并诊断网关空响应。
- v1.68：完成合成发布、OCR 证据和竞态收束。
- v1.69：完成 R001／R002 实片、口型闭环与坏例修正。
- v1.70：完成试片封版、局部口型与跨镜连续性复查。
- v1.71：完成全片封版、坏例解剖和独立交付门禁。
- 2026-08-10：对“面试猫”V2／V3 的多肢体物理因果、动作卡顿和剧情过渡进行局部修复。
- 2026-08-13：统一九阶段生产核心并清理代码。
- 2026-08-14：接入 MiniMax H3 官方视频提供方。
- 2026-08-15：接入 MiniMax H3 官方提示词协议，并继续加强生产管线。
- 2026-08-16：发布本机制作工作台，并完成 Task 10 独立审查修复。
- 2026-08-18：为 H3 增加动作节奏约束。

## 2026-08-31 — 音频优先微分镜生产线重构

本轮针对已生成成片中的关键问题重构了制作流程：剧情与镜头脱节、配音后贴导致人物未开口却在说话、多人／道具物理关系不稳、对白像说明书，以及动作过度丝滑。

### 规则变化

- 先锁定最终角色音频；每句台词只对应一个明确的说话镜头。
- 每个镜头只承担一个表演目的：说一句、看见、反应、起跑或停下；不把多项动作塞进同一长镜头。
- 可见说话镜头优先使用音频驱动能力；不支持时改用背影、手部或反应镜头，不能伪造正脸口型。
- 一镜最多两名角色；接触动作拆成多个短镜头。
- 运动镜头控制在 2–4 秒，并明确脚掌落地、重心、加速／减速和停顿，禁止漂浮与慢动作式补间。
- 每句对白必须推动眼前人物行为，而不是解释世界观。

### 已实现的工程护栏

- 角色表演卡、最终音频资产与哈希校验。
- 三说话人音频驱动对照验证。
- 物理动作提示词编译与微分镜规划。
- 与候选片段、自动视觉质检、人工复核绑定的视频任务证据。
- 只允许审核通过的候选片段进入剪辑选择。
- 本地预检拒绝缺失／篡改／不完整证据，避免把未验证素材混入成片。

### 当前状态

- 该重构已进入主分支，代码、测试与迭代记录均已同步。
- 视频网关余额不足时，流程只会停在受保护的生成门禁，不会绕过门禁或重复计费。
- 已有成片不会被自动覆盖；后续只有在明确的付费授权和素材审核条件满足时才进入新一轮生成。
