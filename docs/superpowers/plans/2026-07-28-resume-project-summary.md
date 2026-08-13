# 漫剧工厂简历项目总结实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将现有简历稿重构为一份以用户交互、bad case 纠正和系统演进为主线、能够完整阐述项目的简历母版。

**Architecture:** 只维护一份事实母版，先区分《旧城来信》和“双猫冻干案”两条验证路径，再从同一套项目事实提炼一份五条简历压缩版。正文以“现象、证据、判断、方案、验证、固化规则”描述迭代，不将聊天记录直接堆成时间线。

**Tech Stack:** Markdown、Mermaid、项目迭代日志、最终审核报告、Git。

## Global Constraints

- 主文件固定为 `/Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md`。
- 《旧城来信》使用 19 微镜头、61.033 秒、7 条音轨和 1,658 项历史回归数据。
- “双猫冻干案”使用 10 镜、54.000 秒、8 条对白、563 项宠物工作流回归数据。
- 两条案例的数据不得混写成同一条样片结果。
- 口型只表述为自然同步和人工起止误差审核，不宣称逐字音素级认证。
- 开源项目表述为参考、兼容或后期使用，不表述为全部自研。
- 不写未经测量的效率提升、成本下降、用户增长或商业化效果。
- 不记录真实 API key、私有网关凭据或完整秘密 URL。

---

### Task 1: 建立统一项目事实与核心定位

**Files:**
- Modify: `/Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md`
- Read: `/Users/tml/Desktop/漫剧工厂/docs/iteration-log.md`
- Read: `/Users/tml/Desktop/漫剧工厂/docs/quality-iteration-handbook.md`
- Read: `/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/review.md`

**Interfaces:**
- Consumes: v1.62《旧城来信》结果、v1.64-v1.65 双猫 Task 2 结果。
- Produces: 后续时间线、bad case、简历压缩版和面试讲述共用的项目定义与事实表。

- [x] **Step 1: 重写项目摘要**

把项目定位为“面向小说/剧本的可恢复、可质检 AI 漫剧生产系统”，明确个人承担：
需求定义、产品流程、模型接入、工作流开发、质量验收和迭代复盘。

- [x] **Step 2: 建立双案例事实表**

分别记录：

```text
旧城来信：19 微镜头 / 12 视频 / 7 静帧 / 61.033 秒 / 7 条 TTS / 0 重叠
双猫冻干案：10 镜 / 54.000 秒 / 8 条对白 / 3 个固定角色音色 / 0 重叠
```

- [x] **Step 3: 写入系统流程**

使用 Mermaid 表达：

```text
小说/脚本 -> 结构化分镜 -> 角色与场景资产 -> 音频先行 ->
候选生成 -> 自动 QC -> 人工 Gate -> OpenMontage/FFmpeg 合成 ->
成片复检 -> bad case 回写
```

- [x] **Step 4: 核验事实边界**

Run:

```bash
rg -n '19|61\\.033|1,658|10 镜|54\\.000|563|逐字|全自动|全部自研' /Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md
```

Expected: 两个案例数据出现在各自标题或表格中；“逐字”只出现在能力边界说明；不出现“全自动”或“全部自研”。

### Task 2: 写清完整交互过程和 Bad Case 纠正

**Files:**
- Modify: `/Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md`
- Read: `/Users/tml/Desktop/漫剧工厂/docs/iteration-log.md`

**Interfaces:**
- Consumes: Task 1 的统一项目定义和双案例事实。
- Produces: 可供简历和面试复用的“反馈、证据、判断、处理、效果、固化”记录。

- [x] **Step 1: 建立项目交互阶段**

按以下阶段写成表格，而不是聊天逐句摘录：

```text
需求探索与开源选型
网关/模型/TTS 接入
首版样片生成
声音与口型纠错
动作、场景和物理连续性纠错
配乐与最终编码纠错
证据链与发布门禁定版
```

- [x] **Step 2: 建立 Bad Case 矩阵**

至少覆盖以下 11 类问题：

```text
声音重叠
角色声线漂移
嘴不动/口型偏移
动作过滑与掉帧
场景和角色不一致
转场不符合物理逻辑
伪文字与 OCR 误报/漏报
机械循环 BGM
异步任务重复提交风险
编码压平微动作
审核历史绑定可变候选路径
```

每行必须包含“用户现象、根因、方案比较、最终处理、效果、固化规则”。

- [x] **Step 3: 写三个深挖案例**

详细解释：

1. 视频先生成、TTS 后贴导致嘴不动，如何演进为 audio-first 和 audio-drive probe。
2. 动作过滑/冻结如何区分源模型问题、帧率处理问题和 H.264 编码问题。
3. 场景跳变如何从 Prompt 问题上升为镜头依赖、起止状态和物理因果问题。

- [x] **Step 4: 检查每个案例是否形成闭环**

Run:

```bash
rg -n '用户现象|根因|方案比较|最终处理|效果|固化规则|audio-first|PTS|物理因果' /Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md
```

Expected: 每个矩阵字段均有内容，三个深挖案例均包含判断和验证，不只描述“重新生成”。

### Task 3: 生成简历压缩版与面试讲述

**Files:**
- Modify: `/Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md`

**Interfaces:**
- Consumes: Task 1 的事实、Task 2 的迭代闭环。
- Produces: 可直接用于主简历和面试的不同长度版本。

- [x] **Step 1: 写主简历五条版本**

五条分别覆盖：

```text
产品定义与端到端流程
模型能力路由和生产门禁
bad case 驱动的音画/连续性迭代
异步任务、成本和证据链工程
双案例量化结果
```

每条使用“动作 + 方法 + 结果”结构，避免一条堆叠所有技术名词。

- [x] **Step 2: 写面试讲述**

分别输出：

```text
30 秒：问题、核心方案、结果
2 分钟：首版失败、三次关键判断、最终结果
5 分钟：完整交互与 bad case 纠正
```

- [x] **Step 3: 写常见追问**

至少回答：

```text
你真正自研了什么？
为什么不是简单调用模型？
最有价值的失败是什么？
如何证明修改有效？
如何处理成本和重复计费？
口型到底做到什么程度？
当前不足与下一步是什么？
```

### Task 4: 内容自检与交付

**Files:**
- Verify: `/Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md`

**Interfaces:**
- Consumes: Task 1-3 的完整母版。
- Produces: 事实一致、可投递、可面试讲述的最终 Markdown。

- [x] **Step 1: 扫描占位和模糊表述**

Run:

```bash
rg -n 'TB[D]|TO[D]O|待补[充]|显著提升|大幅降低|完全解决|保证所有|全自动' /Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md
```

Expected: 无匹配。

- [x] **Step 2: 扫描敏感信息**

Run:

```bash
rg -n 'sk-[A-Za-z0-9_-]{20,}|API_KEY=|ACCESS_KEY=' /Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md
```

Expected: 无匹配。

- [x] **Step 3: 核验结构完整**

Run:

```bash
rg -n '^## ' /Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md
```

Expected: 至少包含项目摘要、职责、系统流程、交互时间线、Bad Case、系统演进、量化结果、简历五条版本、面试讲述、追问和表述边界。

- [x] **Step 4: Markdown 格式检查**

Run:

```bash
python -c 'from pathlib import Path; p=Path("/Users/tml/Desktop/j.sorce/漫剧工厂_简历总结.md"); t=p.read_text(encoding="utf-8"); assert t.startswith("# "); assert t.count("```") % 2 == 0; assert len(t) > 8000; print(len(t), "characters")'
```

Expected: 命令退出码为 0，代码围栏成对，正文不少于 8,000 字符。

- [x] **Step 5: 最终交付**

提供主文件路径，并说明：

```text
第“简历五条版本”可直接放主简历；
2 分钟和 5 分钟版本用于面试；
Bad Case 矩阵用于回答深挖问题。
```
