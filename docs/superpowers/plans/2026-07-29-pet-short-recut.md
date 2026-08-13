# 双猫短剧精修 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **执行状态（2026-07-29）：** 下方 Task 中的时间值和素材选择保留为历史实施草案；
> 已执行真值以文末“实际执行结果”、`recut_plan.json` 和最终 QC 报告为准，禁止按草案旧值回退。

**Goal:** 使用现有已审核双猫素材重剪一版 44.5 秒成片，移除合成噪声和无外力袋子运动，并用自动门禁限制切镜密度。

**Architecture:** 新增独立 `pet_sitcom_recut` 模块，读取现有样片目录中的审核素材、豆包 TTS、配乐和透明字幕图，不修改原始候选文件。模块先验证一份显式时间线合同，再分别构建清洁版和发布版 FFmpeg 命令，最后生成切镜、音频来源、黑帧、冻结和物理连续性报告。

**Tech Stack:** Python 3.11、FFmpeg/FFprobe、pytest、Pillow、JSON。

## Global Constraints

- 成片由 8 个叙事镜头组成，目标时长固定为 44.5 秒。
- 明显硬切总数不超过 10 次，任意 5 秒窗口内不超过 2 次。
- 正式混音只允许豆包 Seed-TTS WAV 和已审核配乐源，不允许粉红噪声、正弦波、合成房间底噪或合成拟音。
- 监控镜头只使用 `shot_07/candidate_003.mp4`；袋子必须静止，唯一运动道具为有可见来源的橘色尾巴。
- 复用现有角色音色和 TTS 文件，不重新生成语音。
- 不覆盖原发布版，精修文件使用新文件名。
- 不使用光流插帧或冻结尾帧填充对白。

---

### Task 1: 建立精修时间线与物理因果合同

**Files:**
- Create: `factory/pet_sitcom_recut.py`
- Create: `tests/test_pet_sitcom_recut.py`

**Interfaces:**
- Consumes: 样片根目录 `Path` 和现有视频、音频、字幕 PNG。
- Produces: `build_freeze_dried_recut_plan(root: Path) -> RecutPlan`、`validate_recut_plan(plan: RecutPlan) -> None`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_pet_sitcom_recut.py` 定义以下测试：

```python
def test_recut_plan_has_eight_shots_and_exact_duration(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    assert len(plan.shots) == 8
    assert plan.duration_seconds == pytest.approx(44.5)
    assert [shot.story_id for shot in plan.shots] == [
        "shot_01",
        "shot_03",
        "shot_04",
        "shot_05",
        "shot_07",
        "shot_08",
        "shot_09",
        "shot_10",
    ]


def test_recut_plan_rejects_unforced_prop_motion(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    broken = replace(
        plan,
        prop_actions=(
            *plan.prop_actions,
            PropAction("treat_bag", "slides", ""),
        ),
    )
    with pytest.raises(RecutError, match="visible physical cause"):
        validate_recut_plan(broken)


def test_monitor_shot_keeps_bag_static_and_tail_causal(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    monitor = next(item for item in plan.shots if item.story_id == "shot_07")
    assert monitor.source.name == "candidate_003.mp4"
    assert monitor.duration_seconds == pytest.approx(4.8)
    assert PropAction("treat_bag", "static", "floor friction") in plan.prop_actions
    assert PropAction("orange_tail", "sweeps", "offscreen cat") in plan.prop_actions
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k 'plan or monitor'
```

Expected: collection fails because `factory.pet_sitcom_recut` does not exist.

- [ ] **Step 3: 实现最小时间线模型**

在 `factory/pet_sitcom_recut.py` 新增：

```python
@dataclass(frozen=True)
class RecutClip:
    source: Path
    start_seconds: float
    end_seconds: float
    output_seconds: float
    video_filter: str = ""


@dataclass(frozen=True)
class RecutShot:
    story_id: str
    duration_seconds: float
    clips: tuple[RecutClip, ...]


@dataclass(frozen=True)
class PropAction:
    prop: str
    motion: str
    visible_cause: str


@dataclass(frozen=True)
class DialoguePlacement:
    story_id: str
    start_seconds: float
    source: Path
    overlay: Path
    evidence_overlay: Path | None = None


@dataclass(frozen=True)
class RecutPlan:
    root: Path
    shots: tuple[RecutShot, ...]
    dialogue: tuple[DialoguePlacement, ...]
    prop_actions: tuple[PropAction, ...]
    music: Path
    opening_overlay: Path
    ending_overlay: Path

    @property
    def duration_seconds(self) -> float:
        return round(sum(item.duration_seconds for item in self.shots), 6)
```

`build_freeze_dried_recut_plan()` 使用以下时长：

```text
shot_01 5.2
shot_03 6.4
shot_04 4.2
shot_05 7.3
shot_07 4.8
shot_08 7.0
shot_09 5.5
shot_10 4.1
```

视频来源固定为：

```text
shot_01/candidate_004.mp4
shot_03/candidate_002.mp4 + shot_02/candidate_002.mp4
shot_04/candidate_003.mp4
shot_05/candidate_002.mp4
shot_07/candidate_003.mp4
shot_08/candidate_005.mp4
shot_09/candidate_006.mp4
shot_10/candidate_006.mp4
```

其中 `shot_03` 是由两个原始审核候选组成的三段虚拟镜头，在 Task 3 的滤镜图
中直接拼接；其余镜头各使用一个连续来源。`validate_recut_plan()` 检查文件均
位于样片根目录内、时长为 44.5 秒、镜头 ID 唯一、所有
`motion != "static"` 的道具均有非空 `visible_cause`，并明确拒绝
`treat_bag` 的非静止动作。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k 'plan or monitor'
```

Expected: 3 tests pass.

- [ ] **Step 5: 提交**

```bash
git add factory/pet_sitcom_recut.py tests/test_pet_sitcom_recut.py
git commit -m "feat: define causal pet recut timeline"
```

---

### Task 2: 构建无合成噪声的音频混音

**Files:**
- Modify: `factory/pet_sitcom_recut.py`
- Modify: `tests/test_pet_sitcom_recut.py`

**Interfaces:**
- Consumes: `RecutPlan.dialogue`、`RecutPlan.music`。
- Produces: `build_audio_filter(plan: RecutPlan, first_audio_input: int) -> tuple[str, str]`，返回滤镜图和输出标签。

- [ ] **Step 1: 写失败测试**

```python
def test_audio_graph_uses_only_tts_and_approved_music(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    graph, output = build_audio_filter(plan, first_audio_input=8)
    assert output == "aout"
    assert "anoisesrc" not in graph
    assert "sine=" not in graph
    assert "room_tone" not in graph
    assert "foley" not in graph
    assert graph.count("adelay=") == 8
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph


def test_audio_input_allowlist_rejects_generated_noise_stems(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    forbidden = sample_root / "audio" / "sound_design" / "room_tone.wav"
    with pytest.raises(RecutError, match="approved music or Seed-TTS"):
        validate_audio_sources(plan, (*allowed_audio_sources(plan), forbidden))
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k audio
```

Expected: FAIL because audio helpers are missing.

- [ ] **Step 3: 实现允许列表和混音图**

`allowed_audio_sources()` 只返回 8 条 `audio/owner|cats/*.wav` 与
`audio/music_sources/secret_garden_curated_70s.wav`。`validate_audio_sources()`
要求所有音频路径与该集合完全一致。

对白绝对开始时间固定为：

```text
shot_01 0.55
shot_03 5.75
shot_04 12.25
shot_05 16.35
shot_07 23.30  (复用 audio/owner/shot_06.wav)
shot_08 28.45
shot_09 37.45
shot_10 41.15
```

每条对白使用 `atrim`、`asetpts`、`aresample=48000`、`adelay` 和 `apad`，
再合成 `[dialogue]`。配乐截取前 44.5 秒，整体标准化到约 -34 LUFS；每个对白
区间前 0.1 秒至后 0.2 秒降低 10 dB。最终只混合 `[dialogue]` 与 `[music]`，
执行 `loudnorm=I=-16:TP=-1.5:LRA=11` 和 `alimiter`。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k audio
```

Expected: 2 tests pass.

- [ ] **Step 5: 提交**

```bash
git add factory/pet_sitcom_recut.py tests/test_pet_sitcom_recut.py
git commit -m "fix: remove synthetic noise from pet recut mix"
```

---

### Task 3: 构建八镜视频与发布字幕

**Files:**
- Modify: `factory/pet_sitcom_recut.py`
- Modify: `tests/test_pet_sitcom_recut.py`

**Interfaces:**
- Consumes: `RecutPlan`。
- Produces: `build_recut_commands(plan: RecutPlan) -> tuple[list[str], list[str]]`、`render_recut(plan: RecutPlan) -> tuple[Path, Path]`。

- [ ] **Step 1: 写失败测试**

```python
def test_video_graph_has_eight_story_shots_and_no_freeze_or_interpolation(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    clean, release = build_recut_commands(plan)
    graph = clean[clean.index("-filter_complex") + 1]
    assert "concat=n=8:v=1:a=0" in graph
    assert "minterpolate" not in graph
    assert "tpad=stop_mode=clone" not in graph
    assert clean[-1].endswith("冻干到底是谁偷吃的_精修清洁版.mp4")
    assert release[-1].endswith("冻干到底是谁偷吃的_精修发布版.mp4")


def test_release_graph_retimes_all_dialogue_overlays(sample_root):
    plan = build_freeze_dried_recut_plan(sample_root)
    _clean, release = build_recut_commands(plan)
    graph = release[release.index("-filter_complex") + 1]
    assert graph.count("overlay=") == 14
    assert "between(t,23.300,25.144)" in graph
    assert "between(t,41.150,43.749)" in graph
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k 'video_graph or release_graph'
```

Expected: FAIL because render helpers are missing.

- [ ] **Step 3: 实现视频滤镜与输出命令**

每个源片段先执行：

```text
trim=start=<start>:end=<end>,setpts=PTS-STARTPTS,
scale=1080:1920:force_original_aspect_ratio=increase,
crop=1080:1920,fps=30,setsar=1
```

`shot_03` 从原 5 段正反打减少为以下 3 段，并在滤镜图中先生成 `[shot03]`：

```text
shot_03/candidate_002.mp4 0.75-2.05 -> 奶糖说话 1.30 秒
shot_02/candidate_002.mp4 0.00-4.00 -> 豆包连续反应 4.20 秒
shot_03/candidate_002.mp4 4.45-5.35 -> 奶糖句尾 0.90 秒
```

`shot_05/candidate_002.mp4` 使用连续 0.0-7.3 秒；`shot_08/candidate_005.mp4`
使用 1.1-8.0 秒并均匀调整到 7.0 秒。8 个故事镜头使用
`concat=n=8:v=1:a=0`，不得加入插帧或克隆尾帧。

发布版复用现有透明 PNG：

```text
opening_title.png: 0.0-3.0
各 dialogue.png: 对应对白起止时间
各 evidence.png: 与对应对白同时
ending_card.png: 43.749-44.5
```

编码固定为 H.264 High、CRF 16、30 fps、AAC 320 kbps、`+faststart`。

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k 'video_graph or release_graph'
```

Expected: 2 tests pass.

- [ ] **Step 5: 提交**

```bash
git add factory/pet_sitcom_recut.py tests/test_pet_sitcom_recut.py
git commit -m "feat: render eight-shot pet recut"
```

---

### Task 4: 增加切镜、音频和物理连续性门禁

**Files:**
- Modify: `factory/pet_sitcom_recut.py`
- Modify: `tests/test_pet_sitcom_recut.py`

**Interfaces:**
- Consumes: 精修成片和 `RecutPlan`。
- Produces: `CutDensityReport`、`review_recut_outputs(plan, clean, release) -> Path`。

- [ ] **Step 1: 写失败测试**

```python
def test_cut_density_rejects_more_than_ten_total():
    with pytest.raises(RecutError, match="more than 10"):
        validate_cut_timestamps(tuple(float(i * 3) for i in range(11)))


def test_cut_density_rejects_three_cuts_inside_five_seconds():
    with pytest.raises(RecutError, match="5-second window"):
        validate_cut_timestamps((10.0, 11.5, 14.8))


def test_cut_density_accepts_bounded_timeline():
    report = validate_cut_timestamps((5.2, 11.6, 15.8, 23.1, 27.9))
    assert report.total == 5
    assert report.max_in_five_seconds == 1
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py -k cut_density
```

Expected: FAIL because cut-density helpers are missing.

- [ ] **Step 3: 实现门禁与报告**

`detect_hard_cuts()` 调用：

```bash
ffmpeg -i <video> -filter_complex \
  "select='gt(scene,0.12)',metadata=print:file=-" -an -f null -
```

解析所有 `pts_time`。`validate_cut_timestamps()` 拒绝总数超过 10，或任意闭区间
`[t, t+5]` 中超过 2 个切点。

`review_recut_outputs()` 同时执行：

```text
ffprobe 音视频流、尺寸、帧率和 44.5 秒时长
blackdetect
freezedetect
ebur128 真峰值
切镜密度
音频来源允许列表
物理因果合同
```

报告写入：

```text
evidence/recut_20260729/recut_qc.json
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py
```

Expected: all recut tests pass.

- [ ] **Step 5: 提交**

```bash
git add factory/pet_sitcom_recut.py tests/test_pet_sitcom_recut.py
git commit -m "feat: gate pet recuts on cuts and causal motion"
```

---

### Task 5: 渲染、逐镜复检并记录迭代

**Files:**
- Modify: `docs/iteration-log.md`
- Create: `/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/recut_plan.json`
- Create: `/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/final/冻干到底是谁偷吃的_精修清洁版.mp4`
- Create: `/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/final/冻干到底是谁偷吃的_精修发布版.mp4`
- Create: `/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/evidence/recut_20260729/recut_qc.json`

**Interfaces:**
- Consumes: Tasks 1-4 的精修器和现有样片资产。
- Produces: 可播放精修版、联系表、QC 报告和迭代记录。

- [ ] **Step 1: 运行精修测试**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py
```

Expected: all tests pass.

- [ ] **Step 2: 渲染两版成片**

Run:

```bash
python -m factory.pet_sitcom_recut \
  --root '/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2'
```

Expected: 生成精修清洁版、精修发布版、`recut_plan.json` 和 QC 报告。

- [ ] **Step 3: 执行视频自检**

Run:

```bash
python /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  '/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/final/冻干到底是谁偷吃的_精修发布版.mp4'
```

Expected: 1080x1920、44.5 秒、存在音频、无技术警告，并生成覆盖全片的联系表。

- [ ] **Step 4: 人工逐镜复检**

正常速度完整观看两遍，并检查：

```text
第一遍：无眩晕感、无生硬转场、配乐不抢对白、无可感知杂音。
第二遍：逐句口型起止正确、静默猫闭嘴、袋子全程静止、尾巴有可见来源。
```

若任一项失败，只重剪对应镜头或音频层，然后重复 Steps 2-4。

- [ ] **Step 5: 运行回归测试**

Run:

```bash
pytest -q tests/test_pet_sitcom_recut.py tests/test_pet_sitcom_sound.py \
  tests/test_pet_sitcom_compose.py tests/test_pet_sitcom_review.py
```

Expected: all selected pet-workflow tests pass.

- [ ] **Step 6: 更新迭代日志**

在 `docs/iteration-log.md` 记录：

```text
用户现象：持续杂音、切镜过密、袋子自移动。
证据：连续宽频噪声、54 秒 16 个硬切、第 6 镜无可见动力。
根因：程序合成底噪/拟音、口型补救导致过度正反打、审核只看轨迹不看动力。
处理：8 镜 44.5 秒重剪、仅 TTS+审核配乐、删除第 6 镜、增加切镜和因果 Gate。
效果：引用最终 QC 中的硬切、黑帧、冻结、重叠、时长和响度实测值。
```

- [ ] **Step 7: 最终验证**

Run:

```bash
git diff --check
python -m compileall -q factory/pet_sitcom_recut.py
pytest -q tests/test_pet_sitcom_recut.py
```

Expected: commands exit 0.

---

## 2026-07-29 实际执行结果

- 开场改用 `shot_01/candidate_006.mp4` 的完整 6 秒运动段，避免尾帧补齐。
- 长对白使用橘猫说话镜头、黑猫反应近景、橘猫说话镜头三段组合，并用
  0.2 秒短溶解遮住源素材冻结区；说话角色离开画面时只保留反应镜头。
- “鸡肉香味”使用 `shot_05/candidate_003.mp4` 的黑猫开口近景，句尾再切橘猫反应，
  不再让闭嘴角色承担整句对白。
- 监控镜头固定冻干袋，只允许有画外来源的橘猫尾巴运动；镜子必须在黑猫前爪接触后
  才改变角度。
- 成片最终为 8 个叙事镜头、44.5 秒、9 个硬切，任意 5 秒最多 2 个硬切。
- 音频图只允许 8 条豆包 Seed-TTS 2.0 对白和人工批准配乐；删除
  `anoisesrc`、正弦拟音、房间底噪及所有程序合成袋子/尾巴/镜子声音。
- 最终发布版为 1080x1920、30 fps、H.264/AAC 48 kHz stereo，
  `-15.6 LUFS`、`-1.5 dBTP`，blackdetect=0、freezedetect=0。
- 逐帧人工复核确认对白起止、说话主体、静止袋子和镜子动力关系；相关回归测试
  `323 passed`。独立代码审查后又增加对白镜头边界、完整道具合同、发布版真实切镜
  扫描和溶解偏移边界测试；精修模块最终 `23 passed`。
