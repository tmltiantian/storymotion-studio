# 统一视频生产流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一个版本化生产协议、三种输入模式和一个 CLI 入口统一小说漫剧、原创宠物短剧和参考视频复刻流程。

**Architecture:** 新建小型统一内核，负责项目规范、阶段图、状态持久化、断点续跑和模式注册；三种模式适配器先调用现有成熟执行器。只有契约测试证明统一入口覆盖旧行为后，才删除重复包装层。

**Tech Stack:** Python 3.12、dataclasses、JSON、argparse、pytest、现有 Seedream/Seedance/Seed-TTS/FFmpeg 适配器

## Global Constraints

- 标准阶段固定为 `concept/script/storyboard/assets/audio/video/edit/eval/deliver`。
- 模式固定为 `original/novel/replica`。
- 默认不联网，只有 `--enable-live` 可产生云端调用。
- 所有状态和规范使用原子写入，拒绝路径穿越和符号链接逃逸。
- 报告不写入 API key、token、签名 URL 或完整供应商错误。
- 旧项目产物和用户未提交改动不得删除或覆盖。
- 音频阶段在视频阶段之前。

---

### Task 1: 统一协议与状态存储

**Files:**
- Create: `factory/pipeline_contracts.py`
- Create: `factory/pipeline_store.py`
- Test: `tests/test_pipeline_contracts.py`
- Test: `tests/test_pipeline_store.py`

**Interfaces:**
- Produces: `ProjectMode`, `StageName`, `StageState`, `ProjectSpec`, `StageRecord`, `ProductionPackage`。
- Produces: `create_project()`, `load_project_spec()`, `load_production_package()`, `update_stage()`。

- [ ] 写测试，覆盖三种模式、九阶段顺序、序列化、路径约束、原子写入和下游失效。
- [ ] 运行测试并确认因模块不存在失败。
- [ ] 实现不可变契约与存储层。
- [ ] 运行两组测试并确认通过。

### Task 2: 模式适配器与阶段映射

**Files:**
- Create: `factory/pipeline_modes.py`
- Test: `tests/test_pipeline_modes.py`

**Interfaces:**
- Consumes: `ProjectSpec`, `ProductionPackage`。
- Produces: `ModeAdapter`, `OriginalModeAdapter`, `NovelModeAdapter`, `ReplicaModeAdapter`, `get_mode_adapter()`。

- [ ] 写失败测试，断言三种模式都映射到完整标准阶段，且 audio 早于 video。
- [ ] 实现注册表和旧执行器映射，不复制供应商逻辑。
- [ ] 为每种模式验证无网络 status/plan 路径。
- [ ] 运行模式测试。

### Task 3: 统一运行器

**Files:**
- Create: `factory/pipeline_runner.py`
- Test: `tests/test_pipeline_runner.py`

**Interfaces:**
- Consumes: `ModeAdapter.execute_stage(spec, package, stage, enable_live)`。
- Produces: `run_pipeline()`, `resume_pipeline()`, `pipeline_status()`。

- [ ] 写失败测试，覆盖 through、失败停止、blocked、复用和 stale 下游重跑。
- [ ] 实现锁、阶段状态流转、错误脱敏和原子保存。
- [ ] 运行运行器测试。

### Task 4: 单一 CLI

**Files:**
- Create: `factory/pipeline_cli.py`
- Modify: `factory_cli.py`
- Test: `tests/test_pipeline_cli.py`

**Interfaces:**
- Produces: `factory create/run/resume/status/review/publish` 子命令。
- Keeps: 旧命令作为兼容入口，不在帮助首页推荐。

- [ ] 写失败测试，覆盖 create、run、resume、status 和默认禁用 live。
- [ ] 实现统一 CLI 并注册到现有顶层入口。
- [ ] 运行 CLI 测试及旧 CLI 重点回归。

### Task 5: 真实项目迁移与清理报告

**Files:**
- Create: `factory/pipeline_migration.py`
- Create: `docs/pipeline-code-map.md`
- Test: `tests/test_pipeline_migration.py`

**Interfaces:**
- Produces: `migrate_existing_project()` 和模块分类报告。

- [ ] 写失败测试，用小说输出、宠物短剧输出和参考复刻配置生成统一状态。
- [ ] 实现只读迁移，不移动或覆盖旧产物。
- [ ] 生成保留/兼容/删除候选清单，附直接导入和 CLI 证据。
- [ ] 删除仅有缓存性质的仓库文件；不删除仍被测试或生产入口引用的模块。
- [ ] 运行迁移测试。

### Task 6: 文档与前端流程

**Files:**
- Modify: `README.md`
- Modify: `sites/pet-video-showcase/app/page.tsx`
- Modify: `sites/pet-video-showcase/tests/rendered-html.test.mjs`

**Interfaces:**
- Consumes: 统一九阶段和三种模式。
- Produces: 面向使用者的统一流程说明。

- [ ] 把 README 首页改为统一入口和最短使用路径，旧命令移入兼容说明。
- [ ] 前端成片页展示统一流程与三种模式，不暴露内部重复实现。
- [ ] 构建站点并运行页面测试。

### Task 7: 全量验证与安全清理

**Files:**
- Modify: only files required by failures found in this task

**Interfaces:**
- Produces: 通过的完整测试套件和最终清理清单。

- [ ] 运行统一流水线测试、三条旧路线重点测试和音频固定映射测试。
- [ ] 运行全量 pytest，修复由重构引入的回归。
- [ ] 扫描敏感信息、绝对临时路径、未引用新模块和重复 CLI。
- [ ] 更新 `docs/pipeline-code-map.md` 的最终保留/兼容/删除结论。
