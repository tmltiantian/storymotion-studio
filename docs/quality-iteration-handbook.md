# 漫剧工厂质量迭代档案

## 1. 文档用途

本文档是漫剧工厂的长期质量账本，记录过去做过什么、为什么做、得到什么效果，以及下一轮如何继续。逐版本命令和原始结果保留在 `docs/iteration-log.md`；本文档负责把零散版本整理成可复用的方法。

以后每轮迭代必须回答七个问题：

1. 遇到了什么问题，用户感受到的现象是什么。
2. 使用了什么证据复现，问题属于内容、模型、编排、后期还是状态报告。
3. 为什么会出现，根因是否已经定位。
4. 选择了什么处理方案，为什么没有选其他方案。
5. 改了哪些代码、配置、素材或提示词。
6. 用什么测试和人工检查证明有效。
7. 还剩什么风险，下一轮只重做哪些部分。

## 2. 当前持续目标

把《旧城来信》从“可播放的 AI 六镜样片”升级成“可验收的动态漫画成片”，并把同一套能力沉淀为可续跑、可质检、可局部重生的小说转漫剧工作流。

当前验收线：

- 角色身份、发型、服装和参演人数在相邻微镜头中一致。
- 每个人物镜头有可读的表情、眼神、姿态和单一动作。
- 源素材不得出现字幕、水印、双脸、重复肢体或模型内切景。
- 模型不负责跨场景转场；后期只使用设计过的硬切、动作匹配切或 2-4 帧时空黑场。
- 人物视频不叠加持续数字推镜；动态漫画动作暴露主要为 6-10 fps。
- 不用长冻结掩盖坏素材；语义完整的稳定取段才允许进入成片。
- 配音重叠数为 0，字幕边界来自实测音频。
- 正式输出为 1080x1920、30 fps、H.264/AAC。
- 工作流状态必须如实反映质量门禁，不能把旧预览误报为目标完成。

## 3. 历史演进整理

| 阶段 | 版本 | 遇到的问题 | 思考与处理 | 得到的效果 | 留给下一阶段 |
| --- | --- | --- | --- | --- | --- |
| 本地闭环 | v0.1-v1.0 | 小说只能形成结构化数据，没有可播放、可交接的结果 | 先建立 Episode、LumenX/OpenMontage handoff、字幕、卡片预览、运行时探测和后端 smoke | 形成不依赖付费生成也能验证的最小闭环 | 真实生成需要凭证、角色资产和失败关闭 |
| 安全门禁 | v1.1-v1.18 | 实生成容易误调用、状态分散、缺少有声预览 | 增加显式实生成开关、端到端 readiness、本地 TTS、OpenMontage 字幕合成和工作流状态 | 可以安全地区分 dry-run、demo-ready 和 real-generation | 角色图仍缺少来源和一致性管理 |
| 角色与操作面 | v1.19-v1.46 | 角色参考图来源不明，人工操作步骤多，面板容易给出错误下一步 | 增加角色图签名、来源确认、生产门禁、预检、操作交接、Dashboard 和角色感知卡片预览 | 两位角色资产可以校验、安装、追踪和复用 | 场景画面仍是卡片或静态参考 |
| 网关生产接入 | v1.47-v1.51 | DashScope 不是唯一通路，网关视频契约、异步任务和计费续跑未验证 | 按能力路由文本/图像/视频/TTS；实现 Seedance 任务提交、轮询、下载、签名、锁、原子发布和 Worker | 网关真实视频可生成，失败可恢复，已完成镜头不会重复计费 | 需要完成整集并建立素材级视觉验收 |
| 六镜成片 | v1.52 | 六条视频存在坏尾帧、生成字幕、双脸和字幕时序问题 | 使用镜头级取段、删段、冻结和 OpenMontage 字幕修复完成第一版正式预览 | 六条动态镜头、零卡片回退、可播放成片 | “救片”隐藏了源素材结构问题，不能作为长期生产方案 |
| 音画节奏修复 | v1.53 | 声音重叠、虚构台词、匀速 30 fps 过于丝滑 | 按实测语音排程，删除重复/填充对白，引入 12 fps 动作采样 | 7 条来源对白、0 重叠，字幕跟随语音 | 统一降帧不能修复模型内运镜和长镜头结构 |
| 豆包配音 | v1.54 | 本地声音生硬；旧 dotenv 把行内注释误判成密钥 | 修复 dotenv，接入 Seed-TTS 2.0，加入原子缓存、音频验证和 -16 LUFS 母带 | 7 条豆包音轨，音频响度稳定，无重叠 | 单音色仍不足以表现角色 |
| 微镜头质量架构 | v1.55 | 一个提示词承担多个场景和动作，模型自行切景、漂移和变脸 | 新增 19 个微镜头、结构化表演提示词、生产模型小样、候选续跑、视觉 QC、微镜头渲染和质量路径保护 | 质量工作流代码和 dry-run 产物已就绪，旧预览不会覆盖未完成的微镜头路径 | 尚未执行付费模型小样和全量候选 |
| 多角色豆包音色 | v1.56 | 旁白和角色共用音色；约 0.4 秒首尾静音干扰排程 | 实测 6 个官方音色；旁白用流畅女声、苏眠用 Vivi 2.0、林澈预设儒雅逸辰；增加首尾静音裁切 | 完整成片 7 条豆包音轨、0 本地、0 重叠、角色音色区分为真 | 当前剧本没有林澈对白；画面仍是旧六镜 |
| 质量真值审计 | v1.57 | 旧六镜仍被状态面板标成 `goal_ready`，`status.json` 还报告本地 TTS | 把已启动的微镜头路径设为强制质量门禁；工作流写入时原子同步当前配音报告 | 状态改为 `demo_ready_blocked_for_quality_upgrade`，只剩质量路径一个阻塞项 | 完成模型小样、逐镜 QC、选择和新版渲染 |
| 模型实测淘汰 | v1.58-v1.60 | 模型列表可见但任务不兼容；票据、信纸、招牌、雨后表面反复诱发伪字和持续降雨；双角色像轻推立绘 | 读取脱敏供应商详情；按实测能力缩小生产模型；每镜最多 3 候选；把动作目标移到画面内角色；自动 OCR 与人工表演分开验收 | Seedance 2.0、Seedream 4.5 分别以 88/92 分样本通过；1.5 Pro 和 GPT Image 留在实验区 | 批量前仍需移除已知高风险物件并验证全覆盖 |
| 审计式批量生产 | v1.61 | 两个静帧耗尽候选仍有雨线；3 秒视频被供应商拒绝；CDN 短读；衣纹持续触发 OCR | 新增带双哈希和操作参数的 editorial still；按供应商最短 4 秒生成后再剪到时间线；完成任务下载最多重试 3 次；OCR 要求 4 字符且跨帧确认 | 七个静帧覆盖完成；首批五条人物视频通过，下载恢复未重复提交；误报消失而持续文字仍会失败 | 完成余下五条视频、全量选择、新版渲染和成片复检 |
| 质量路径闭环 | v1.62 | 影院灯箱伪字逃过自动 OCR；首版微剪有两处旁白与画面不一致；静帧被通用冻结检测误报为异常 | 人工放大继续作为硬门禁；重构高风险构图后定点重生；用画面优先的剧本适配修订两条旁白；按微镜头来源解释冻结 | 19/19 镜头完成选择，12 动态、7 静帧、0 回退；两条旁白定点重生，最终 0 黑帧、0 音频重叠，状态进入 `goal_ready` | 只剩三个低分镜头的非阻塞精修机会 |

## 4. 2026-07-17 当前基线

### 4.1 已通过

- 成片：`output/sample_episode/final_preview.mp4`
- 规格：H.264/AAC、1080x1920、30 fps、48 kHz 双声道。
- 时长 61.033 秒，文件大小 29,017,862 字节。
- 视频预检没有解码、尺寸、流数量或容器警告。
- 配音供应商报告：豆包 7 条、本地 0 条、重叠 0 条、错误 0 条。
- 当前有效角色音色：旁白“流畅女声”，苏眠“Vivi 2.0”；林澈已配置“儒雅逸辰”但本集无台词。
- 综合响度 -16.31 LUFS，真峰值 -5.06 dBTP。
- 7 条字幕起止与 7 条实测语音时序一致。
- 视觉选择覆盖 19/19 个微镜头：12 个 Seedance 2.0 视频、5 个 Seedream 4.5 静帧、2 个带来源哈希的编辑静帧。
- 质量渲染使用 6/8/10 fps 分镜节奏，旧卡片回退为 0；工作流状态为 `goal_ready`。
- 最终 SHA-256：`5c37963912dde7e9bb10ddec7a6cee19e9ca9937cc8f4646e1513958b817feb2`。
- 全量工程回归 1,658 项通过；Ruff、compileall、JSON 与差异空白检查通过。

### 4.2 已解决问题与剩余精修项

| 优先级 | 证据 | 根因判断 | 处理策略 |
| --- | --- | --- | --- |
| 已解决 P0 | `visual_selection.json`、`micro_preview_report.json` 和质量路径最终成片均存在 | 模型小样之后缺少全量选择与渲染闭环 | 19/19 完整选择通过哈希与 QC 门禁，渲染路径为 `quality_micro`，`goal_ready=true` |
| 已解决 P1 | `micro_009` 候选 1 的失焦灯箱有伪字但自动 OCR 未检出 | 低对比、失焦伪字符低于 OCR 证据阈值 | 人工放大按 `embedded_text` 淘汰；人物占满画面、背景暗墙的候选 2 以 98 分通过 |
| 已解决 P1 | 首版微剪第 3、5 镜旁白与画面不一致 | 为规避文字、儿童和陌生人风险，画面已抽象化但剧本没有同步适配 | 修订两条旁白并只重生两条豆包音轨，字幕按新实测时长重建 |
| 已解决 P1 | 旧六镜长冻结、模型内切景、源字幕和重复脸 | 单条模型请求承担过多事件，后期依赖救片 | 19 个单动作微镜头替代旧路径；18 个切点前后逐帧通过，无黑场和源字幕 |
| 精修 P2 | `micro_006` 86 分 | 结尾更接近惊讶，压抑蹙眉较弱 | 当前不阻塞；高级版只定点替换该镜，通过后再更新选择 |
| 精修 P2 | `micro_013` 88 分 | 更像已经停住，没有完整表现走到停住 | 当前与旁白仍一致；未来可重做动作起点，不改其他镜头 |
| 精修 P2 | `micro_016` 84 分 | 银白背景偏抽象星尘，不像传统影院银幕 | 当前信封轮廓和屏幕亮起链路成立；高级版优先换更具影院材质的静帧 |
| 守护 P3 | OCR 仍可能漏掉失焦短伪字，也可能把稳定纹理当字 | 图像文字检测无法完全覆盖生成式伪符号 | 保留“自动 OCR + 九宫格 + 高风险区域放大”三层验收，不允许只看自动结果 |

### 4.3 为什么技术预检之后还要做内容复检

技术预检只回答文件能否播放、编码和音轨是否合格。最终交付还必须检查人物身份、表情、手部、模型内切景、源文字、字幕碰撞和音画语义。本轮技术预检第一次通过后，19 镜中点复检仍发现两处旁白与画面不一致；修订旁白、重生两条豆包音轨并重新烧录后，才把质量状态从“技术可播”推进到 `goal_ready`。

### 4.4 生产小样实测与定点迭代

| 问题 | 证据与判断 | 处理 | 当前效果 |
| --- | --- | --- | --- |
| 1.5 Pro 两条人物请求均为 HTTP 400 | 脱敏后的供应商错误明确说明 `task_type=r2v` 不支持 `doubao-seedance-1-5-pro` | 把接口支持模型与生产模型分开；1.5 Pro 留在实验区，角色正式路由只保留标准 2.0 | 不再重复提交确定不兼容的参考图任务 |
| Seedream 4.5 首次静帧为 HTTP 400 | 供应商要求至少 3,686,400 像素，旧 `1024x1536` 只有约 157 万像素 | 改为正好满足门槛的竖屏 `1440x2560`；GPT Image 继续使用自身的 `1024x1536` | Seedream 请求成功返回 |
| Seedream 候选 1 出现说明框和 `AI生成` 水印 | 说明框内容直接来自提示词里的 `9:16` 和字幕安全区；右下角是供应商水印 | 从静帧提示词移除可被照抄的画幅/安全区标签；请求增加 `watermark=false` | 候选 2 消除了文字和水印，但仍需天气连续性人工复核 |
| Seedream 候选 2 在“雨停后”继续降雨 | 黑色信封主体成立且 OCR 通过，但整幅有大量落雨线，违反 `no_rain` | 按 `composition_mismatch` 淘汰；最后一次候选明确空气中无雨线，只保留柜台残留水滴 | 防止自动检查通过后把语义错误误报为生产可用 |
| Seedream 候选 3 仍出现落雨线 | 三次候选均表明“雨后湿润柜台”会稳定触发降雨先验 | 达到上限后停止 `micro_003` 计费；镜头转后期清理/重新分镜，静帧小样改用 `micro_011` 旧喇叭 | 不用第 4 次抽卡掩盖模型边界 |
| GPT Image 长时间无结果 | 120 秒客户端超时后，放宽到 300 秒仍由网关返回 HTTP 504 | 保留为实验路由，不阻塞已验证的 Seedream 生产路径 | 避免把模型列表可见误报为生产可用 |
| `micro_017` 及生产镜头自动 OCR 误报 | 接触表没有文字，Tesseract 把发丝、衣领、门框和针织衫扣眼识别成 `CR/Ny/Wii/IN/iff` | 先把置信阈值提高到 80；生产实测后进一步要求单裁剪累计至少 4 个规范字符，并在两个不同采样帧确认 | 短纹理噪声不再硬失败；持续 `SALE` 和句子文字仍被拦截，九宫格继续人工检查短标志 |
| `micro_005` 候选 1 有票面伪字 | 人物稳定，但票面印刷、数字和条码清晰可见 | 候选淘汰；第二版要求无字背面完整可见 | 人物表演保留，但模型仍生成栏目式票面 |
| `micro_005` 候选 2 仍有票面伪字 | 仅改变“正面/背面”不足以降低文字物件先验 | 最后一次候选改为只露薄纸侧边，票面完全不入画 | 为候选 3 提供最后一次受控验证 |
| `micro_005` 候选 3 仍生成完整票面 | 接触表确认模型把纸边扩写成完整车票，多行伪字贯穿中段，人物视线也没有持续落到票据上 | 达到三个候选上限后停止计费；票据特写改走无字静帧或后期合成，模型小样改用双角色 `micro_018` | 失败被保留为生产禁用规则，不再用裁切、冻结或第 4 个候选掩盖 |
| `micro_018` 候选 1 展开信纸并生成伪字 | 双角色身份稳定且无切镜，但前景出现印章和多行伪字，两人视线动作也不明显 | 候选淘汰；第二版移除完整纸面，只允许画面下缘出现闭合黑色封套薄边，并明确视线从正前方下移 | 把双角色能力和文字道具风险拆开验证 |
| `micro_018` 候选 2 接近轻推立绘 | 收窄后的封套没有文字，双角色身份和结构稳定；但视线下移、眉间放松均不够可读，人工评分 74 | 不把 OCR 通过当成表演通过；最后一次候选移除全部道具，只让林澈把视线横向移向苏眠 | 把动作目标放在画面内现有角色上，首尾帧可以直接验收视线方向 |
| `micro_018` 候选 3 执行动作但有轻微偏差 | 林澈清楚转向苏眠，身份、解剖、构图和清洁度稳定；模型加入小幅头转，苏眠视线未完全低垂 | 偏差按表达和语义扣分，不扩大成硬失败；以 88 分通过，正式合成使用 8 fps 节奏采样 | 得到无字、无切镜、可读动作的双角色生产样本，不再继续计费抽卡 |
| `micro_011` 候选 1 生成人群和地点招牌 | 旧喇叭几何、冷光和无水印通过，但下方出现清晰地点文字，右侧有一排人物剪影 | 以文字和构图双硬失败淘汰；第二版改为喇叭占九成画面的物件特写，只保留天花板背景 | 从构图上移除招牌平面和人群空间，不靠重复否定词抽卡 |
| `micro_011` 候选 2 物件特写通过 | 无文字、人物、水印和坏几何；喇叭约占三分之二，底部窗框略亮 | 轻微构图偏差按分数扣除，不扩大为硬失败；以 92 分通过并停止候选 3 | Seedream 获得可审计的生产静帧样本，模型小样门禁可进入定版 |
| OCR 把衣领、发丝和信封折线当成英文 | 候选 2 没有真实文字，但 60-77 置信度的 `we`、`AN`、`ALN` 等随机片段触发硬失败 | 默认 OCR 置信门槛由 60 提到 80，新增真实噪声回归；清晰文字仍自动拦截，伪文字继续由九宫格人工复核 | 候选 2 自动门禁从误报修正为通过，人工表演门禁仍使其淘汰 |
| 视频轮询发生 TLS EOF | 远端任务已提交且任务 ID 完整，失败只发生在 GET 轮询，本地状态保持 `submitted` | 使用同一候选续跑并恢复下载；轮询层对传输瞬断最多连续重试 3 次，确定性 HTTP/JSON/任务失败不重试 | 同一任务成功恢复，未重复提交；网关相关 85 项回归通过 |
| HTTP 400 只有状态码，没有原因 | 原报告无法区分模型、尺寸和任务类型错误 | 新增有界 JSON 错误详情提取，只读取白名单字段并脱敏密钥、URL、签名和图像数据 | 已直接定位 r2v 与最小像素两类根因 |
| 明确 400 仍写成 `submitting` | 确定拒绝与中断后的未知远端状态混在一起 | HTTP 拒绝写 `rejected + http_status_code`；只有真正不确定的中断保留 `submitting` | 续跑和费用判断更准确 |

本轮新增三个正式操作命令：

- `quality-bakeoff-candidates`：默认干跑，只有 `--enable-live` 才执行选定候选。
- `quality-visual-qc`：生成或刷新自动证据，并写入严格格式的人工评分。
- `quality-finalize-bakeoff`：重新校验候选路径、文件哈希、分数和硬失败后选定生产模型。
- `quality-production-candidates`：模型小样通过后，默认干跑并按所选模型生成全量人物视频/静帧候选。
- `quality-select`：调用逐镜 QC、候选哈希和完整覆盖门禁后，原子发布 `visual_selection.json`。

截至候选 3 复核阶段，质量相关回归共 816 项通过，Ruff 和 `git diff --check` 通过。运行时逐镜修改记录在 `runs/sample_episode/visual_plan_review.json`，不会把失败候选从审计链中删除。

## 5. 当前决策

1. 不再继续微调旧六镜。现有截短、删段和冻结只保留为回退样片。
2. 先做小样淘汰，不直接生成 19 条收费素材。
3. 人物生产只使用已通过角色参考图契约的 `doubao-seedance-2-0`；`doubao-seedance-1-5-pro` 因拒绝 `r2v` 进入实验区。
4. 静帧生产使用已成功返回且可关闭水印的 `doubao-seedream-4-5`；`gpt-image-2` 因 HTTP 504 暂不进入生产。
5. 代表镜头使用 `micro_017`、`micro_018` 和 `micro_011`；`micro_005` 三次因票据伪字淘汰，`micro_003` 三次因持续降雨淘汰，均转为后期清理/重新分镜设计样本。
6. 总分低于 80，或出现串脸、额外人物、双脸、肢体异常、源文字、镜头内切景，候选直接淘汰。
7. 小样胜出后分批生成；每批生成后立即 QC，只重做失败镜头。

### 5.1 全量付费前风险改写

- `micro_003` 三次雨线失败后不再沿用原 ID。新设计 `micro_003r` 去掉雨后、湿润和水滴提示，只保留干燥无字柜台上的黑色信封；旧候选继续作为模型边界证据。
- `micro_005` 三次票面伪字失败后改为 `micro_005r`。人物不再持票，只保留低垂视线、眨眼和肩线绷紧；车票信息由旁白与字幕承担。
- `micro_004`、`micro_006`、`micro_013`、`micro_014` 同步移除票据平面。检票段改为苏眠停步并把空手伸向检票口，降低文字与手部同时失控的概率。
- `micro_008` 改成无字冷光灯箱，`micro_009` 注视入口冷光，`micro_010` 改成只有磨砂玻璃和两道冷光的空白海报框，避免招牌文字和海报人脸。
- 票据和十年前仍由原始对白保留。海报人物与童年陌生人因为没有安全、可信的视觉证据，最终改成与已通过画面一致的“两道交叠冷光”和“林澈接住信封、苏眠站在身后”；剧本适配与两条新豆包音轨均保留在审计记录中。

## 6. 下一轮执行顺序

1. 保留当前 `visual_selection.json` 和最终成片为可回滚基线，不覆盖已通过资产。
2. 高级版按分数从低到高只重做 `micro_016`、`micro_006`、`micro_013`，每次只替换一个镜头。
3. 新候选继续检查身份、表情、手部、动作、源文字、场景切换和镜头漂移；总分低于 80 或有硬失败就保留当前镜头。
4. 增加成片级“旁白/画面证据对照表”，要求每条旁白至少对应一个可见镜头证据，避免画面安全改写后剧本没有同步。
5. 研究低对比伪文字的第二 OCR/视觉模型交叉检查，但不降低当前人工九宫格门禁。
6. 新小说项目沿用同一顺序：微镜头规划、模型小样、逐镜候选、完整选择、质量渲染、音画语义复检。

## 7. 每轮记录模板

```markdown
## YYYY-MM-DD - vX.Y 标题

目标：

问题与用户现象：

复现证据：

根因判断：

方案比较与决定：

实施：

验证：

效果：

未解决与风险：

下一轮：
```

## 8. 证据索引

- 原始逐版本日志：`docs/iteration-log.md`
- 质量升级设计：`docs/superpowers/specs/2026-07-15-motion-comic-quality-upgrade-design.md`
- 实施计划：`docs/superpowers/plans/2026-07-15-motion-comic-quality-upgrade.md`
- 当前工作流状态：`runs/sample_episode/workflow_status.json`
- 当前视觉时间线：`runs/sample_episode/visual_timeline.json`
- 当前模型小样计划：`runs/sample_episode/model_bakeoff_plan.json`
- 当前完整视觉选择：`runs/sample_episode/visual_selection.json`
- 当前微镜头渲染报告：`runs/sample_episode/micro_preview_report.json`
- 当前配音报告：`runs/sample_episode/voiceover/voiceover_provider_report.json`
- 当前成片接触表：`output/sample_episode/self_check_contact_sheet.jpg`
- 19 镜中点表：`runs/sample_episode/final_qc/micro_midpoints_5x4.jpg`
- 18 个切点前后表：`runs/sample_episode/final_qc/cut_pairs_6x6.jpg`
- 本轮最终复检报告：`runs/sample_episode/final_qc/final-review.md`
- 六条源片接触表：`runs/sample_episode/clips/shot_001_contact.jpg` 至 `shot_006_contact.jpg`
- 多音色交付：`output/sample_episode/多音色试听交付/`

## 9. 宠物短剧音频先行 SOP

### 9.1 固定生产契约

“冻干案”当前是 10 shots / 54s，不再使用 14 个独立固定五秒片段。Task 2
用 Doubao Seed-TTS 2.0 先冻结 8 条对白、角色 voice/rate、真实 WAV 时长和绝对
时间线，并为六个猫说话镜头制作 generation-length drive WAV。对白禁止
`atempo`；唯一跨切点对白是 `shot_06` 提前 0.20 秒开始的有意 J-cut。

Task 4 先执行 audio-drive probe。Task 5 只有在 probe 与人工 review 仍 current
时，才调用 `doubao-seedance-2-0` endpoint-bound audio-driven generation。
Task 6 使用 `motion-comic-factory.pet-sitcom-shot-review.v4` 验证 10 个镜头和
六个 mouth timing。Task 7 把 variable trims 合成 1080x1920@30，禁止短源
padding、`minterpolate` 与视频转场，并使用三幕非循环 sound、J-cut 及
tail/footstep L-bridge。

### 9.2 操作命令

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage plan --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2"
.venv/bin/python factory_cli.py pet-sitcom --stage anchors --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage audio --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage audio-probe --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage shots --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage review --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2"
.venv/bin/python factory_cli.py pet-sitcom --stage compose --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --music-source "/absolute/path/to/approved/music.m4a"
.venv/bin/python factory_cli.py pet-sitcom --stage status --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2"
```

第一次 compose 用 `--music-source` prepare。音乐必须是 absolute canonical
local regular file，路径各级不能是 symlink，且同路径
`<music-source>.approval.json` 必须绑定 current hash 并通过人工批准。之后可
省略参数，但只复用 current hash-bound `sound_design.json`；省略不是重新选曲，
也没有自动音乐 fallback。

### 9.3 人工门禁

1. **Anchor approval**：检查奶糖、豆包、客厅和厨房四个 anchor 的身份、标记、
   光向、空间和无字要求；完成 review 后才能 approve。
2. **Audio probe review**：看 probe MP4 与九帧证据，确认 supplied audio 未被
   替换/重定时，口型在对白开始/结束附近，静默时闭嘴且猫嘴自然。
3. **六镜 mouth timing**：逐镜填写 onset/offset、silent-mouth 与
   speaking-mouth gate，误差必须各自不超过 0.25 秒；这是人工视觉判断，
   不是音素级认证。
4. **10-shot review + owner gate**：每镜检查 identity、anatomy、speaker、
   action preparation/execution/settle、screen position/eyeline、prop 与
   dependency continuity；另行确认 owner native audio。
5. **Music approval**：批准 non-looped source 的音色不过分刺耳、不机械重复、
   与对白兼容；compose 后仍试听三幕转段、duck 和 ending button。

任何自动技术检查都不能替代以上人工步骤，也不得把未填写模板描述为“已自动通过”。

### 9.4 Probe 与 status 状态机

| 状态 | 操作 |
| --- | --- |
| `missing` / `stale` | 排查 current anchors、Task 2 audio 与旧绑定后，才可由操作员显式执行 `audio-probe --enable-live` |
| `pending` | supported probe 等待人工 review 或 review 已陈旧；查看 evidence，不重新提交 |
| `unsupported` | 已持久化 HTTP 400 capability 证据；terminal fail-closed，禁止自动重试 |
| `inconclusive` | 已持久化 durable/unavailable task identity 的不确定结果；terminal fail-closed，禁止自动重试 |
| `approved` | report、MP4、九帧、source hashes 与人工 timing 全部 current，shots 才可进入 |

`status` 是纯读快照，不调用 provider/TTS，不运行 FFmpeg/FFprobe，不 probe
媒体，也不写、修复或刷新文件。它只说明最近一次强验证证据及关联 bytes/hash
是否仍 current，并以第一项未完成 gate 给出 `next_stage`。`composition_ready`
还要求 10/10 selection、10/10 review、owner gate、sound 和 final evidence
current。compose 不信任 status 缓存，仍会执行 source/review/owner、sound、
snapshot、输出 media validator 和原子发布强 gate。

### 9.5 Sound v2 迁移

当前 schema 是 `motion-comic-factory.pet-sitcom-sound-design.v2`，算法是
`pet-sitcom-three-act-v2`。本次 binding 新纳入 source metadata：

- container duration 与 audio stream duration；
- sample rate、channels 与 channel layout；
- codec type 与 codec name。

旧 v2 manifest 虽然 JSON shape 相同，但 binding 没有这些 metadata，会被新
loader/status 安全判 stale。Task 10 必须用 approved music 再次 prepare，不能
手工改 `binding_sha256`。旧 worker 和新 worker 会使用不同算法互相判 stale；
升级完成前不得并行混用，也不要让持续 worker 与人工 compose 同时写同一输出目录。

三幕 music cue 为 `light_interrogation`（0.0-26.5）、
`surveillance_investigation`（26.5-37.4）和 `comic_reveal`（37.4-54.0）。
source 只输入一次，禁止 `-stream_loop` / `aloop`。tail 与 footsteps 延续到
32.9 秒形成 L-bridge；release 总线使用独立 dialogue/foley/room/music mix。

### 9.6 产物、发布与恢复

输出根目录固定为 `$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2`。检查顺序：

- 计划与声音：`pet_sitcom_plan.json`、`audio_manifest.json`、
  `audio_probe.json`、`audio_probe_review.json`。
- 选择与人工门：`selected_candidates.json`、`shot_review.json`、
  `owner_native_audio_review.json`、`dialogue_timings.json`。
- 声音：`sound_design.json` 和 `audio/sound_design/` 的不可变版本 stems。
- 证据：`evidence/source_manifest.json`、`evidence/final_manifest.json`、
  technical QC 与 contact sheets。
- 最终文件：`final/冻干到底是谁偷吃的_清洁版.mp4`、
  `final/冻干到底是谁偷吃的_发布版.mp4`、`review.md`。

compose 获取 `.pet-sitcom-compose-publish.lock`，先恢复
`.pet-sitcom-compose-transaction.json` journal，再 snapshot 所有 current
输入到项目内私有临时目录。clean/release 都通过 1080x1920、H.264 High、
yuv420p、30fps、AAC stereo 48k、54 秒、响度、峰值、faststart 与无对白重叠
检查后才原子成对发布。中断后重新执行 compose 触发恢复；不要删除 journal、
lock、transaction 目录或只手工替换其中一个 final。

### 9.7 Task 10 运行前置

1. 确认代码版本一致，停止所有旧 worker，避免 mixed-version manifest 互相失效。
2. 只通过环境变量名称配置凭据，如 `GATEWAY_API_KEY`、
   `DOUBAO_TTS_APPID`、`DOUBAO_TTS_ACCESS_KEY`；不要把值写进命令、文档或
   review。
3. 用 status 查第一项未完成 gate，但以对应 stage 的强入口结果为准。
4. 完成人工 anchor approval、付费 audio probe 与人工 probe review。
5. 生成并人工选择 10 镜头，填写六镜 mouth timing、10-shot review 和 owner gate。
6. 用 current approved music 执行带 `--music-source` 的 compose，强制生成新
   source-metadata binding；之后才允许无参数复用。
7. 检查 clean/release、final evidence、`review.md`，再执行最终 video edit
   self-check。没有完成这些人工步骤时不得宣称 Task 10 生产通过。

安全边界：文档和产物不得包含真实 gateway key、API key、完整 secret URL 或
data URI；错误与 CLI 输出只保留脱敏结果。

### 9.8 Task 2 定版结论（2026-07-28）

本轮定版产物已经完成强验证：

- 清洁版与发布版均为 54.000 秒、1080x1920、30 fps CFR、H.264 High、
  yuv420p、AAC stereo 48 kHz。
- 两版最终响度均为 `-15.7 LUFS`，true peak 为 `-1.5 dBTP`；
  blackdetect 和 freezedetect 均为零。
- 8 条 Seed-TTS 2.0 对白使用三个固定角色音色和实测绝对时间线，无对白重叠，
  无 `atempo`。主人在 `shot_06` 的 -0.20 秒 J-cut 是有意设计。
- 配乐使用一条 70 秒人工批准的连续来源，不循环、不拉伸；三幕 cue 与对白退让、
  room tone、foley 和结尾按钮均绑定 `sound_design.json`。
- 全片九宫格及三组关键过渡抽帧已人工查看，未发现黑帧、意外冻结、字幕遮脸、
  多余角色、场景内无原因切换或关键道具状态冲突。
- 宠物工作流完整回归为 `563 passed`，只读 CLI 状态为
  `composition_ready=true`。

本轮新增的通用规则：

1. **一帧短缺不是补尾帧许可**。只有 hash-bound 的 24 fps 本地重剪、recipe
   时长匹配且短缺不超过 `1/24` 秒时，最终合成才允许把已有运动做均匀 PTS 微调。
   云端短源、普通候选和更大短缺继续 fail closed。
2. **最终编码也属于动作质量链**。源片无冻结但成片出现冻结时，先对同一源做编码
   A/B。若高保真设置恢复微动作，应修复编码参数，不回头污染源片，也不人工加运动。
3. **审核历史不能依赖可变文件仍保留旧字节**。历史 JSON 通过字段、路径边界、
   哈希格式、文件名和审核记录互相绑定；当前媒体字节由当前选择单独强验证。
4. **本地重剪必须继承真实失败来源**。重剪 provenance 中同镜头 source candidate
   必须命中有效失败审核归档；报告和发布门禁使用同一个解析函数，不能各写一套规则。
5. **口型通过不等于逐字认证**。当前通过条件是指定说话者、可见猫科下颌动作、
   静默猫闭嘴、开始/结束误差不超过 0.25 秒，以及完整主观试听。没有音素证据时，
   文档必须明确只达到自然同步。

最终证据索引：

- 成片：`$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2/final/`
- 人工审核总表：`$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2/review.md`
- 最终技术门禁：`$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2/evidence/final_technical_qc.json`
- 最终证据清单：`$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2/evidence/final_manifest.json`
- 自检抽帧：`$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2/evidence/self_check/`

## 10. 160 秒宠物长片坏样片闭环（2026-07-30）

### 10.1 长片生产契约

《斑斑来访》固定为 39 镜、33 句对白、160 秒。正式视频模型统一为
`doubao-seedance-2-0`，Fast 只用于接口诊断；对白统一为豆包 Seed-TTS 2.0，
四个说话者各自固定 voice ID。短镜头仍按 provider 的最短 4 秒生成，但计划、
审核和合成只消费编辑窗，不把补出的尾段算入质量指标。

每个动作镜头至少写出准备、执行、结果和停顿，涉及食物、碗、礼物等道具时还要写明
施力者和状态变化。后续镜头只能继承上游已经可见的结果，不能依赖“模型应该知道”。

### 10.2 为什么必须做两层复检

逐镜审核负责身份、解剖、动作、口型和局部连续性；最终成片审核负责剪辑边界、
对白排程、全片停帧、场景节奏和音频来源。两层都必须通过，因为 S002 和 S027
曾在单镜检查通过后，仍被最终 160 秒扫描发现 1.6 秒和 0.8 秒的长静止段。

长片的固定检查顺序：

1. 对 final MP4 做技术预检、完整解码、黑帧和低变化扫描。
2. 生成每秒接触表，按 40 秒区段逐格看角色、空间、道具和字幕。
3. 对命中的问题镜头生成 4 fps 或更密集抽帧，区分有意收势与真实冻结。
4. 只重做失败镜头，逐候选记录指标和人工拒绝原因；不要覆盖已通过基线。
5. 重排原始 TTS 时优先移动时间窗，不重新合成、不 `atempo`，并重新检查全片
   overlap、最小间隔和最终响度。
6. 重新 compose 后从第一步再跑一次，不能复用旧 final 的 QC 结论。

### 10.3 本轮可复用的判断

- **持续微动作要写进时间窗**：仅写“紧张地看”容易在动作完成后静止。S002
  拆成影子、回望、转身、收翼和肩羽收束后，内部最长低运动段从长停帧降到
  0.292 秒。
- **对白后也需要反应动作**：S027 在台词结束后安排转头、眨眼和呼吸，既避免
  静止尾巴，也不会让角色无意义持续张嘴。
- **口型以实测画面为准**：若候选的实际开口比脚本早，可移动未变速的原始 WAV。
  本轮鸟喙约 0.17 秒启动，音频设为 0.20 秒，视觉偏差约 0.03 秒。
- **静止检测不是绝对裁决**：镜头结束前短于约 0.7 秒的稳定姿态可保留，但必须
  在密集抽帧中看见动作先完整结束，且后续没有补尾帧、滑动或形变。
- **恢复状态按最小必要数据保存**：续跑只持久化安全 task ID、状态和端点指纹；
  不保存完整 secret URL。候选报告和恢复状态使用同一敏感信息边界。
- **环境也是工作流的一部分**：runtime probe 失败时先核对源码目录、解释器和依赖
  三层。源码存在而 `.venv` 不存在时，按锁定依赖重建，不把它误判为业务代码失败。

### 10.4 当前质量结论与剩余风险

当前清洁版和发布版均为 160.000 秒、1080x1920；最终 QC 通过 39 镜、33 句对白、
`-15.57 LUFS`、`-1.88 dBTP` 和音频白名单。33 条对白无重叠，最小间隔
0.6645 秒；全片 preflight 零 warnings，完整回归 `2467 passed`，Ruff 通过。

剩余风险应如实保留：

- 当前口型是逐帧人工确认的自然音节级同步，不是 phoneme/viseme 逐字认证。
- S001 至 S003 在切点前各有短暂稳定姿态，最长 0.667 秒；这是有意收势，
  未来替换候选时仍要重新扫描，不能永久加入忽略列表。
- 任何新镜头、字幕、声音或重新编码都会使 final 级结论失效，必须重新生成全片证据。

本轮证据：

- 发布版：`$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/斑斑来访_发布版.mp4`
- 最终 QC：`$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/final_qc.json`
- 每秒接触表与关键镜头密集抽帧：
  `$HOME/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/full_qc_final_v2/`

## 11. 源片逐镜重拍的失败关闭与网关恢复

### 11.1 先锁编辑合同，再生成新像素

逐镜参考不等于复制源片。可以锁定故事顺序、切点、构图功能、动作因果和评估音频
节奏，但最终像素必须由原创角色和场景重新生成。源帧只进入
`reference/shots/<shot_id>/<start|middle|end>.jpg` 证据目录，不得进入最终合成。

每条源证据同时绑定：

- 固定 shot ID、`start|middle|end` 标签和唯一规范路径；
- 源视频 SHA-256、采样时间和图片 SHA-256；
- 无 `..`、无 symlink、真实解析路径仍在项目根目录内；
- 评审、批准和 selection 再验证时重新计算，而不是信任旧报告。

只验证“文件在根目录内”或“图片哈希相同”都不够。同一图片被改名或搬到错误镜头
目录后，语义身份已经改变，必须失败。

### 11.2 图片上传以字节签名为准

模型下载结果可能把 JPEG 字节保存在 `.png` 名称下。生产入口应先限制后缀在安全
图片白名单，再通过 magic bytes 决定真实 MIME；不能让素材验证器和上传客户端使用
互相矛盾的规则。签名无效、格式不支持或文件为空继续在付费调用前失败。

### 11.3 空响应的排查顺序

多模态提交得到空响应时，按以下顺序缩小范围：

1. 离线构建完整请求，确认模型、4-15 秒时长、720p、9:16、参考图数量和音频格式。
2. 把短 WAV 只在尾部补静音到 provider 最短窗，不移动原对白；若仍失败，排除
   “音频过短”假设。
3. 用不产生有效任务的诊断请求测试不同 body 大小；结合历史成功请求，排除代理体积
   限制。
4. 把多张身份图压成清晰控制板，降到两张图加一条音频；若仍失败，排除参考图数量。
5. 用另一多模态端点做最小请求；视频和图片端点同时空响应时，优先判断网关适配层或
   上游服务状态，不继续改镜头脚本。

排查只改变一个变量，并记录请求摘要、字节数、结果和耗时。不能连续提交多个有效
候选来“碰运气”。

### 11.4 `submitting` 不是失败，也不是成功

客户端在 POST 后收到空响应或连接中断时，远端可能已经创建计费任务。状态必须视为
不确定：

- 正式候选不提升，旧候选不覆盖；
- 脱敏报告和状态进入
  `rejected/generation_attempts/<shot>/<candidate>/attempt_NNN/`；
- 保留安全 task ID、状态、端点指纹和签名哈希，不保留密钥、授权头或完整签名 URL；
- 登录网关任务日志或由同一 task ID 恢复前，禁止自动重提；
- 确认远端没有任务后，才允许同一镜头执行下一次显式提交。

事务清理的目标是保护正式目录，不是销毁失败证据。任何异常路径都应同时满足
“零误提升、旧结果可回滚、诊断可恢复、敏感信息不落盘”。

### 11.5 从试片扩展全片

首个 R001 候选恢复后，固定顺序是：

1. 验证 MP4、分辨率、帧率、provider 时长和当前输入签名。
2. 检查人物与猫身份、客厅几何、动作准备/执行/收势、道具受力和可见说话者。
3. 对对白起止前后密集抽帧，目标误差不超过 0.20 秒；无音素/viseme 真值时只声明
   自然口型，不声明逐字准确。
4. R001 人工通过后再生成 R002-R009，每镜最多三个候选。
5. 合成 12.3 秒试片，禁止转场、尾帧复制、`tpad`、`minterpolate` 和光流；
   只按源切点裁真实运动。
6. 对试片运行技术预检、黑帧/冻结/切点/源帧哈希和接触表人工检查；通过后才扩全片。

### 11.6 OCR、字幕帧与发布快照必须是同一个审核合同

源片字幕不能只抄成一个字符串。每个 OCR 检测都必须保存源镜头、整数起止帧、源框、
检测文字和内容哈希，再由人工事件一一映射到以下两类之一：

- `dialogue_subtitle`：允许进入字幕版母版；
- 水印、账号、头像、装饰字等排除类：保留审核证据，但永不进入 ASS。

空事件表只有在内容寻址证据明确声明 `reviewed_zero=true` 且检测数确实为零时才可
接受。实际含水印的镜头不得用“人工看过了”代替检测映射。

30 fps 字幕的权威时间是 `[start_frame,end_frame)`，不是浮点秒。ASS 虽然只能写
厘秒，也必须由整数帧确定性推导，并用解码后的真实变化帧反查，不能宽容前后各一帧。
中文字体同时绑定路径、family、SHA-256 和真实 glyph/render 证据，防止换机后静默
回退成方框字。

发布前最后一次验证必须重新读取当前文件，而不是复述内存中的摘要：

1. 重算全部 selected clip、源 AAC 和参考视频字节哈希。
2. 重新验证当前 selection 和 audio manifest。
3. 把当前批准路径与摘要绑定到本次已渲染 manifest。
4. 重算发布目录内双母版、标准化镜头、字幕、对比视频、审核快照和 QC 摘要。
5. 仅在回调全部通过后原子替换 current 指针；失败时删除新目标和事务，旧指针
   必须逐字节不变。

固定对抗测试至少覆盖文件字节、元数据、合法候选路径切换、合法音频路径切换、
摘要切换、symlink 和 `..` 别名。哈希检查与路径检查缺一不可。

文件最终读取与指针替换之间仍可能有操作系统级极短竞态。若业务需要完全消除，应
采用不可变输入对象或跨进程锁；在普通本地单作者工作流中，可把它记录为残余风险，
但不能假装重复读取可以提供数学上的原子性。
