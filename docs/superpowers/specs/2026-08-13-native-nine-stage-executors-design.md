# 原生九阶段执行器设计

## 目标

把现有“统一状态机 + 旧组合命令”改成真正的九阶段生产结构：

`concept -> script -> storyboard -> assets -> audio -> video -> edit -> eval -> deliver`

原创、小说改编和参考复刻共用状态机、阶段协议、产物登记、断点续跑与审批机制；
模式只改变每个阶段的策略，不再维护三条互相独立的流水线。

## 当前问题

- 原创和小说的 `script` 阶段调用 `run-project`，一次生成剧本、分镜、素材、音频、
  视频和剪辑，阶段状态与真实执行边界不一致。
- 统一主流程通过子进程调用自身旧 CLI，错误、产物和门禁要靠 JSON 文本二次解析。
- `covered_stages` 允许一个执行结果替多个阶段通过，削弱了阶段独立返修能力。
- 复刻虽然已有分阶段命令，但统一状态机仍通过 `pet-replica` CLI 间接调用。

## 结构

### 1. 状态机层

`pipeline_runner.py` 只负责锁、顺序、阶段状态、签名、失效和停止原因。每次循环只执行
一个阶段，一个执行器只能更新自己的阶段，不再支持组合覆盖。

### 2. 阶段上下文层

新增 `StageContext`，包含统一项目目录、项目规范、当前阶段、模式策略、是否允许云端
生成，以及读取前序产物的方法。执行器不自行猜测目录，也不读取后序产物。

### 3. 标准产物层

每个阶段拥有固定目录：

```text
runs/<project>/stages/
  concept/
  script/
  storyboard/
  assets/
  audio/
  video/
  edit/
  eval/
  deliver/
```

阶段完成后写 `manifest.json`，记录 schema、阶段、模式、执行器、产物和内容哈希。
输入签名保存在生产状态包。状态文件只登记本阶段 manifest 与本阶段真实文件。删除或
修改前序产物时，当前阶段及下游自动失效。

### 4. 模式策略层

- 原创：从创意扩写角色、对白和剧情节拍。
- 小说：读取小说，提取人物、事件和对白后改编。
- 复刻：读取参考视频分析结果，保留剧情节拍、镜头时长和连续性约束。

模式适配器声明执行器 ID、费用门禁、人工门禁和策略版本，不再构造旧 CLI 命令。

### 5. 能力层

阶段执行器直接调用现有 Python 能力：`novel_planner`、角色资产、豆包 TTS、网关视频、
OpenMontage/FFmpeg、复刻分析与 EVAL。旧 CLI 继续调用这些能力作为兼容入口，但统一
主流程不再依赖旧 CLI。

## 阶段职责

| 阶段 | 输入 | 输出 |
|---|---|---|
| concept | ProjectSpec | concept.json |
| script | concept + 源文本/参考源 | script.json |
| storyboard | script | episode.json / shot_timeline.json |
| assets | storyboard | asset_manifest.json / 锚点与参考图 |
| audio | storyboard + assets | audio_manifest.json / 音频 / 字幕时序 |
| video | storyboard + assets + audio | video_manifest.json / 候选镜头 |
| edit | video + audio | edit_manifest.json / 可审核成片 |
| eval | 可审核成片 + 各阶段证据 | eval_result.json / 返修建议 |
| deliver | EVAL PASS + 成片 | delivery_manifest.json / 封存母版 |

## 门禁

- 默认本地预览不产生云端费用。
- 需要云端的视频阶段在未启用 live 时返回 `blocked`，不能人工绕过。
- 角色、复刻参考标注、EVAL 和发布为人工门禁；先生成审核材料，再绑定证据批准。
- 自动返修只使目标阶段及下游失效，不重跑已通过且产物完整的上游阶段。

## 兼容与迁移

- 保留 `run-project`、`plan`、`pet-replica` 等旧命令。
- 已迁移项目保留 `legacy:` 签名和只读产物，不被新执行器强制重跑。
- 新建统一项目只使用原生执行器。
- 不移动或删除历史输出目录。

## 验证标准

- 任一阶段执行时不会生成后序阶段产物。
- `--through storyboard` 只执行 concept、script、storyboard。
- 删除音频只从 audio 恢复，删除成片只从 edit 恢复。
- 原创、小说、复刻都映射完整九阶段，且主流程不启动 `factory_cli.py` 子进程。
- 云端和人工门禁不可互相绕过。
- 旧 CLI 测试和统一流水线测试全部通过。
