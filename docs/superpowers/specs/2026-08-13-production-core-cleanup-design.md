# 漫剧工厂生产主线收敛设计

## 目标

将项目收敛为一条可维护的九阶段生产主线，删除已被替代且没有生产入口的平行流水线，
保留猫咪音色、口型、物理连续性、参考复刻和质量审核等独特能力。整理后的版本发布到
新的私有 GitHub 仓库，原仓库不删除、不改写、不强推。

## 保留边界

以下内容属于生产核心：

- `pipeline_*` 的九阶段状态、执行器、审批、续跑和产物协议。
- 剧本、角色资产、网关模型、豆包 TTS、媒体校验、OpenMontage/FFmpeg 和 EVAL 能力。
- `pet_replica_*` 的参考拆解、资产绑定、候选生成、口型、审核和合成能力。
- `pet_sitcom_*` 中尚未被统一主线等价替代的角色声音、音频驱动、物理连续性和审核能力。
- `performance_planner`、`prompt_compiler`、`visual_timeline`、`visual_qc`、`quality_*` 等可复用质量能力。
- 当前面试猫专项 `source_locked_cat_replica`，直到其配置驱动能力完全并入统一复刻模式。

保留能力不等于保留旧入口。主入口统一为 `factory create/run/resume/status/approve/review/publish`。

## 删除边界

立即删除以下可证明孤立或已替代的实现：

- `pet_longform_*` 全套模块和测试。它没有生产 CLI 注册、脚本调用或统一流程引用。
- 已被新版宠物口型与复刻链替代的 `speaking_ab` 独立实验入口、模块和测试。
- 旧 `project_runner`、`job_queue` 与 `plan/run-project/enqueue/worker` 组合流水线。
- LumenX 的 bootstrap、health、live executor、mock backend、generation guard 和控制面调试链。
- 旧 dashboard、readiness、operator handoff、preflight、workflow status 等只服务旧流水线的报告层。
- 被删除入口专用的 shell 脚本、测试和文档段落。

`pet_sitcom_*` 与 `source_locked_cat_replica` 暂不删除，因为其能力尚非冗余；它们作为能力层保留，
不再作为推荐主流程。

## LumenX 收敛

不再依赖 LumenX 应用、后端或运行时。保留原适配器的结构化价值，将
`lumenx_adapter.py` 改为中性的 `video_handoff.py`，schema 改为工厂自有的视频任务格式。
网关批处理直接读取：

```text
storyboard + assets + audio -> video_handoff -> Seedance gateway -> edit -> EVAL
```

旧 LumenX schema 只在历史项目迁移读取时兼容，不再由新项目写出。

## 共享基础设施

新增一个很小的文件工具模块，统一活跃主线中的原子 JSON 写入、文本写入和 SHA-256。
只替换主线与本轮触及模块中的重复实现，不进行全仓机械重写。

## 兼容策略

- 新项目只使用统一 `factory` 命令。
- 历史成片和 `runs/output` 不移动、不删除。
- 原宠物短剧和复刻的底层 Python 能力保留，可供统一执行器调用。
- 删除的 CLI 返回不再兼容；README 给出统一命令迁移方式。
- 原 GitHub 仓库只读保留，新版本推到新仓库。

## 验证与发布

- 对每组删除先做生产引用扫描，再删除源码和对应测试。
- 运行 Ruff、compileall、统一流水线测试、宠物/复刻关键回归和全量测试。
- 对待提交文件执行密钥、token、私钥和超大文件扫描。
- 新建私有仓库 `tmltiantian/manju-factory-next`；若名称已存在，使用带日期后缀的新名称。
- 新增 `next` remote，推送当前整理版本到新仓库 `main`，不修改 `origin`。
