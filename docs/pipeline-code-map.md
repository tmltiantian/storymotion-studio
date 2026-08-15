# 流水线代码地图

## 主流程

| 职责 | 文件 |
| --- | --- |
| 项目模式、阶段和状态契约 | `pipeline_contracts.py`, `pipeline_modes.py` |
| CLI 创建、运行、恢复、审批、发布 | `pipeline_cli.py` |
| 阶段调度与断点恢复 | `pipeline_runner.py`, `pipeline_store.py` |
| 执行器注册与上下文 | `pipeline_executors.py`, `pipeline_context.py` |
| 通用阶段实现 | `pipeline_generic_stages.py` |
| 参考复刻阶段实现 | `pipeline_replica_stages.py` |
| 产物登记与原子写入 | `pipeline_artifacts.py`, `file_io.py` |
| 旧项目登记迁移 | `pipeline_migration.py` |

九阶段固定为：`concept`, `script`, `storyboard`, `assets`, `audio`, `video`, `edit`, `eval`, `deliver`。

## 内容与素材

| 职责 | 文件 |
| --- | --- |
| 小说/构思转剧本 | `novel_planner.py`, `schema.py` |
| 角色资产与确认 | `character_assets.py`, `character_brief.py` |
| 动作表演与提示词约束 | `performance_planner.py`, `prompt_compiler.py`, `prompt_safety.py` |
| 中立视频任务 | `video_handoff.py` |
| 剪辑包与后期 | `openmontage_adapter.py`, `openmontage_post.py` |
| 本地预览和占位帧 | `preview_*`, `hybrid_preview.py`, `shot_card_renderer.py` |

## 生成服务

| 职责 | 文件 |
| --- | --- |
| Provider 选择和脱敏报告 | `provider_profile.py`, `dotenv.py` |
| 可替换视频提供方 | `video_provider.py`, `gateway_video.py`, `minimax_h3_video.py` |
| 网关文本/图像/视频 | `gateway_text.py`, `gateway_image.py`, `gateway_video.py` |
| 视频 handoff 批处理 | `gateway_video_batch.py` |
| 豆包与本地语音 | `doubao_tts.py`, `local_voiceover.py` |

LumenX 专属启动、Mock 后端和控制面已删除。`gateway_video_batch.py` 只为旧项目保留旧 handoff 的只读兼容，新产物统一使用 `video_handoff.json`。

## 宠物专业能力

| 职责 | 文件 |
| --- | --- |
| 原创宠物短剧 | `pet_sitcom*.py` |
| 参考视频复刻 | `pet_replica*.py` |
| 音频驱动与口型 | `pet_sitcom_audio_first.py`, `pet_replica_lipsync.py` |
| 参考源锁定复刻 | `source_locked_cat_replica.py` |

这些模块是可复用能力，不再拥有另一套项目调度逻辑；九阶段执行器负责调用它们。

## EVAL 与交付

| 职责 | 文件 |
| --- | --- |
| 时间线和镜头连续性 | `visual_timeline.py` |
| 自动画面检查 | `visual_qc.py` |
| 模型候选与选择 | `model_bakeoff.py`, `quality_*` |
| 媒体结构检查 | `media_validation.py` |
| 返修决策 | `edit_decisions.py` |

阶段状态、EVAL 结果和交付门禁都由主流程持有，不再维护重复的 dashboard/readiness/operator 状态文件。
