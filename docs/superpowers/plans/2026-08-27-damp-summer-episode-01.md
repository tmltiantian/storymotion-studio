# 《潮湿的夏天》第一集制作计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 制作《潮湿的夏天》第一集《雨夜归人》的可审核动态漫项目，从剧本与分镜推进至经过 EVAL 的竖屏成片。

**Architecture:** 使用 StoryMotion Studio 的 `original` 项目模式创建独立项目，先在不触发付费生成的情况下产出概念、剧本、分镜和角色资产清单。完成角色参考图与剧本审核后，才以显式 `--enable-live` 调用豆包 TTS 与网关 Seedance 2.0；视频提示词由现有 H3 风格转化器编译，所有阶段在工作台审批后再继续。

**Tech Stack:** Python 3.12、StoryMotion Studio 九阶段流程、网关 Seedance 2.0、豆包 Seed-TTS 2.0、FFmpeg-full、Fontconfig。

**Spec:** `docs/superpowers/specs/2026-08-27-damp-summer-bl-motion-comic-design.md`

## Global Constraints

- 项目 ID 固定为 `damp_summer_ep01`，标题固定为《潮湿的夏天·第一集：雨夜归人》。
- 采用 9:16、1080x1920、30 fps、75 秒、20 镜头的竖屏动态漫规格。
- 两位主角均为成年男性：陈川 31 岁，陆野 32 岁；不生成未成年亲密内容。
- 人物、场景和情绪按已批准规格执行：青湾、梅雨、青砖、渡口、柑橘园、旧摩托、货车和修理铺。
- 第一集只表现雨夜重逢、未说出口的旧伤与果园合作的引子；不提前揭露旧信和离开真相。
- 付费图像、TTS 和视频调用必须在用户明确确认费用后使用 `--enable-live`；不得自动执行。
- 视频提供方为 gateway / `doubao-seedance-2-0`，音频提供方为豆包 `seed-tts-2.0`。
- 每个阻塞审批阶段必须保存审核理由；角色图和最终成片均需人工审核。

---

## File Structure

- Create: `projects/damp-summer/episode-01-source.md` — 第一集的人工可读故事、对白和镜头节拍，是管线输入的唯一叙事来源。
- Create: `projects/damp-summer/README.md` — 项目定位、固定角色锚点、命名规则和审核清单。
- Create: `projects/damp-summer/ep01/` — `factory create` 生成的统一项目目录、阶段产物、审核记录和交付物。
- Create: `projects/damp-summer/ep01/assets/characters/` — 经审核后安装的陈川与陆野角色参考图。
- Create: `projects/damp-summer/ep01/review/` — 人工审批记录与每轮角色、镜头、音频、成片的反馈。
- Test: `tests/test_damp_summer_story_contract.py` — 验证源故事的角色、时空、首集边界和镜头时长预算，防止后续内容偏离已批准规格。

### Task 1: 固化第一集叙事输入与内容合同

**Files:**
- Create: `projects/damp-summer/episode-01-source.md`
- Create: `tests/test_damp_summer_story_contract.py`

**Interfaces:**
- Consumes: 第一季规格中的人物、地点、第一集钩子与 75 秒限制。
- Produces: 可供 `factory create --mode original --input` 读取的 UTF-8 叙事文本，以及对该文本的合同测试。

- [ ] **Step 1: 写出失败的内容合同测试**

```python
from pathlib import Path


SOURCE = Path("projects/damp-summer/episode-01-source.md")


def test_episode_one_source_keeps_the_approved_reunion_boundary():
    text = SOURCE.read_text(encoding="utf-8")
    assert "1998" in text
    assert "2011" in text
    assert "陈川（31 岁）" in text
    assert "陆野（32 岁）" in text
    assert "雨夜重逢" in text
    assert "柑橘园" in text
    assert "旧信" not in text
    assert "真相揭开" not in text
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_damp_summer_story_contract.py`

Expected: FAIL，因为 `episode-01-source.md` 尚不存在。

- [ ] **Step 3: 编写第一集源故事**

写入以下固定内容：

```markdown
# 《潮湿的夏天·第一集：雨夜归人》

2011 年梅雨夜，青湾。陈川（31 岁）开着旧货车给果农送完零件，途经镇口时看见陆野（32 岁）被债主拦在雨里。陆野十多年未归，陈川认出他后先踩下刹车，又冷着脸开过去；下一秒，他从后视镜看到陆野被雨水冲得站不稳，终于倒车。

陈川只说：“上车，别弄脏座位。”陆野坐进副驾，低声说：“阿川，好久不见。”陈川没有回答。

车经过荒废的柑橘园。村主任打来电话，说果园明天就要低价卖给外人，问陈川肯不肯接下运输的活。陆野望向车窗外，提出他知道省城有销路。陈川说：“我不和欠一屁股债的人做生意。”陆野回答：“那就当我把命押给你。”

结尾：货车停在渡口。雨声盖住沉默，陈川下车去拿伞，只留一句：“明早六点，果园见。迟到就滚。”陆野站在雨里笑了一下，握紧旧行李箱。
```

- [ ] **Step 4: 运行内容合同测试并确认通过**

Run: `.venv/bin/python -m pytest -q tests/test_damp_summer_story_contract.py`

Expected: PASS。

- [ ] **Step 5: 提交内容输入与测试**

```bash
git add projects/damp-summer/episode-01-source.md tests/test_damp_summer_story_contract.py
git commit -m "feat: add damp summer episode one source"
```

### Task 2: 创建统一项目并完成无付费预制作

**Files:**
- Create: `projects/damp-summer/README.md`
- Create: `projects/damp-summer/ep01/`（由 CLI 创建）
- Modify: `tests/test_damp_summer_story_contract.py`

**Interfaces:**
- Consumes: `episode-01-source.md` 与第一集 75 秒、20 镜头的合同。
- Produces: 原创模式项目规格、概念、剧本、分镜、字幕草稿和角色资产模板。

- [ ] **Step 1: 扩展失败测试，要求项目配置符合首集规格**

```python
import json
from pathlib import Path


PROJECT_SPEC = Path("projects/damp-summer/ep01/project.json")


def test_episode_one_project_is_a_strict_vertical_original_project():
    payload = json.loads(PROJECT_SPEC.read_text(encoding="utf-8"))
    assert payload["project_id"] == "damp_summer_ep01"
    assert payload["mode"] == "original"
    assert payload["target"]["aspect_ratio"] == "9:16"
    assert payload["target"]["resolution"] == "1080x1920"
    assert payload["target"]["fps"] == 30
    assert payload["target"]["duration_seconds"] == 75
    assert payload["target"]["shots"] == 20
    assert payload["approval_preset"] == "strict"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_damp_summer_story_contract.py::test_episode_one_project_is_a_strict_vertical_original_project`

Expected: FAIL，因为项目尚未创建。

- [ ] **Step 3: 创建项目和项目说明**

```bash
.venv/bin/python factory_cli.py factory create \
  --mode original \
  --project damp_summer_ep01 \
  --title "潮湿的夏天·第一集：雨夜归人" \
  --input projects/damp-summer/episode-01-source.md \
  --output-dir projects/damp-summer/ep01 \
  --duration 75 --shots 20 --ratio 9:16 --resolution 1080x1920 --fps 30 \
  --approval-preset strict
```

`projects/damp-summer/README.md` 必须写明陈川与陆野的姓名、年龄、固定服装、发型、声音、青湾场景锚点，以及每轮审核必须记录在 `review/` 下。

- [ ] **Step 4: 运行至分镜阶段，不启用真实生成**

```bash
.venv/bin/python factory_cli.py factory run projects/damp-summer/ep01 --through storyboard
.venv/bin/python factory_cli.py factory status projects/damp-summer/ep01
```

Expected: `concept`、`script` 和 `storyboard` 有产物；命令未使用 `--enable-live`。

- [ ] **Step 5: 验证项目合同并人工审核剧本分镜**

Run: `.venv/bin/python -m pytest -q tests/test_damp_summer_story_contract.py`

人工检查 `storyboard/storyboard.md`：雨夜重逢、货车、渡口和柑橘园必须出现；不得出现旧信、离开真相或亲密露骨画面。将“通过”或逐镜修改意见写入 `projects/damp-summer/ep01/review/storyboard.md`。

- [ ] **Step 6: 提交预制作成果**

```bash
git add projects/damp-summer/README.md projects/damp-summer/ep01 tests/test_damp_summer_story_contract.py
git commit -m "feat: scaffold damp summer episode one"
```

### Task 3: 生成并锁定两位成年主角的角色资产

**Files:**
- Create: `projects/damp-summer/ep01/assets/characters/chen_chuan_reference.png`
- Create: `projects/damp-summer/ep01/assets/characters/lu_ye_reference.png`
- Create: `projects/damp-summer/ep01/review/character-assets.md`
- Modify: `projects/damp-summer/ep01/character_assets.confirmed.json`

**Interfaces:**
- Consumes: 分镜中的角色 ID、`character_generation_brief.json` 与项目 README 的视觉锚点。
- Produces: 被 `character_assets.confirmed.json` 绑定的、人工审核过的生产角色参考图。

- [ ] **Step 1: 创建角色图生成简报且不触发付费调用**

```bash
.venv/bin/python factory_cli.py character-brief projects/damp-summer/ep01
.venv/bin/python factory_cli.py character-assets-reviewed-template \
  --brief projects/damp-summer/ep01/character_generation_brief.json \
  --output projects/damp-summer/ep01/review/reviewed_role_images.template.json
```

Expected: 为陈川和陆野分别生成正面、三视图、表情与服装要求；此步骤只生成文本和模板。

- [ ] **Step 2: 人工确认角色绘制提示词和费用上限**

审核以下人物事实：陈川 31 岁、短黑发、洗旧的深蓝工装、掌心有机油；陆野 32 岁、略长黑发、褪色卡其雨衣、旧行李箱。确认不含未成年外观、名人肖像或受版权角色后，记录预算与批准人到 `review/character-assets.md`。

- [ ] **Step 3: 在明确获得用户付费确认后生成候选图**

使用已配置网关图像模型，一次只生成一个角色候选，保存到项目审核目录。调用命令必须显式附带当前项目的预算限制；不得在未获得该轮确认时执行。

- [ ] **Step 4: 安装人工选定的参考图并验证资产状态**

```bash
.venv/bin/python factory_cli.py character-assets-reviewed-from-dir \
  --brief projects/damp-summer/ep01/character_generation_brief.json \
  --source-dir projects/damp-summer/ep01/review/selected-characters \
  --output projects/damp-summer/ep01/review/reviewed_role_images.json
.venv/bin/python factory_cli.py character-assets-confirm-source \
  --manifest projects/damp-summer/ep01/review/reviewed_role_images.json \
  --output projects/damp-summer/ep01/character_assets.confirmed.json
.venv/bin/python factory_cli.py character-assets-status projects/damp-summer/ep01
```

Expected: 陈川与陆野均为 `production_ready`，并且参考图路径位于项目目录内。

- [ ] **Step 5: 提交审核记录和角色资产清单**

```bash
git add projects/damp-summer/ep01/review projects/damp-summer/ep01/character_assets.confirmed.json
git commit -m "feat: approve damp summer character assets"
```

### Task 4: 制作音频、Seedance 镜头与首集成片

**Files:**
- Create: `projects/damp-summer/ep01/review/audio.md`
- Create: `projects/damp-summer/ep01/review/video.md`
- Create: `projects/damp-summer/ep01/review/final.md`
- Create: `projects/damp-summer/ep01/delivery/`（由 delivery 阶段生成）

**Interfaces:**
- Consumes: 已审核剧本、角色资产和用户本轮付费批准。
- Produces: 豆包 TTS 音频、Seedance 2.0 单镜视频、剪辑成片、EVAL 结果和交付清单。

- [ ] **Step 1: 先生成音频审核预览并审核台词时长**

在获得用户对 TTS 费用的明确确认后执行：

```bash
.venv/bin/python factory_cli.py factory run projects/damp-summer/ep01 --through audio --enable-live
```

人工检查两人的成年男声区分、普通话清晰度、雨声下对白可懂度和总时长不超过 75 秒；将结论写入 `review/audio.md`，再按严格审批流程批准音频阶段。

- [ ] **Step 2: 生成并审核 Seedance 2.0 镜头**

在用户确认视频预算后执行：

```bash
.venv/bin/python factory_cli.py factory run projects/damp-summer/ep01 --through video --enable-live
```

验证 `video_handoff.json` 的模型为 `doubao-seedance-2-0`，陈川与陆野镜头带有角色参考，且 H3 风格提示词已转换为 Seedance 可读文本。逐镜审核人脸一致性、雨夜空间关系、货车方向、渡口地理关系与禁止提前揭露真相；将镜头编号、通过/返工原因写入 `review/video.md`。

- [ ] **Step 3: 剪辑、EVAL 与交付**

```bash
.venv/bin/python factory_cli.py factory run projects/damp-summer/ep01 --through deliver --enable-live
.venv/bin/python factory_cli.py factory status projects/damp-summer/ep01
```

人工检查最终竖屏文件：开头三秒必须出现陆野雨夜归来，结尾必须停在“明早六点，果园见。迟到就滚。”；字幕、声音、人物年龄呈现和台词同步均无错误。将结论写入 `review/final.md`。

- [ ] **Step 4: 验证最终交付物与内容合同**

Run: `.venv/bin/python -m pytest -q tests/test_damp_summer_story_contract.py`

Run: `.venv/bin/python factory_cli.py factory status projects/damp-summer/ep01`

Expected: 角色资产、剧本与审核证据齐全；若任何付费阶段、EVAL 或人工审核未通过，则不发布成片。

- [ ] **Step 5: 提交第一集审核文本与非媒体项目记录**

```bash
git add projects/damp-summer/ep01/review projects/damp-summer/ep01/delivery
git commit -m "feat: deliver damp summer episode one"
```

## Self-Review

- Spec coverage: 四项任务覆盖了已批准的 90 年代南方乡村气质、两位成年竹马、雨夜重逢首集、克制情感表达、竖屏动态漫、角色一致性、豆包 TTS、Seedance 2.0、审核和交付。
- Scope: 本计划只制作第一集。其余 11 集在第一集的角色、声音、镜头质量审核通过后，按同一项目合同分别立项，避免一次启动整季付费生成。
- Placeholder scan: 计划中的人物、首集剧情、路径、命令、时长、镜头数、审核条件和验证命令均已明确。
- Type consistency: `damp_summer_ep01`、`projects/damp-summer/ep01`、角色资产清单、`factory create/run/status` 命令和首集审核目录在全部任务中一致。
