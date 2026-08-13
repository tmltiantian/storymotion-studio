# 宠物短剧项目放映室 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建并私密发布一个单页宠物短剧项目放映室，支持播放最终成片、查看四类问题修复证据和回顾迭代过程。

**Architecture:** 在 `sites/pet-video-showcase/` 新建 Sites 静态项目，以单路由 React 页面承载全部内容。最终成片和精选审查图作为站点静态资源内置，页面数据集中定义，标签交互在浏览器本地完成，不使用数据库、登录或外部 API。

**Tech Stack:** Sites starter、React、TypeScript、CSS、Lucide icons、HTML5 video

## Global Constraints

- 站点用途为个人项目成果库，不使用求职或客户营销话术。
- 首屏必须能播放《咪要去面试》最终封存成片，并露出下一部分内容。
- 使用 `成片`、`问题修复`、`迭代记录`、`历史素材` 四个标签页，不新增路由。
- 黑、白、浅灰为基础，少量亮绿色作为状态和操作强调色；不使用渐变和装饰光斑。
- 卡片圆角不超过 8px，不嵌套卡片，不使用大段功能说明文案。
- 最终视频使用 `sealed_delivery_v3_1_20260810/final_master.mp4`。
- 站点必须适配手机和电脑，标签支持键盘操作，并尊重系统减少动画设置。
- 使用 Sites 私密发布。

---

### Task 1: 初始化站点并整理项目素材

**Files:**
- Create: `sites/pet-video-showcase/`
- Create: `sites/pet-video-showcase/public/media/final-master.mp4`
- Create: `sites/pet-video-showcase/public/evidence/*.jpg`

**Interfaces:**
- Consumes: 最终封存视频和现有审查图片。
- Produces: `/media/final-master.mp4` 和 `/evidence/*.jpg` 静态资源。

- [ ] **Step 1: 初始化 Sites 项目**

```bash
mkdir -p sites/pet-video-showcase
/Users/tml/.codex/plugins/cache/openai-bundled/sites/0.1.34/scripts/init-site.sh "$PWD/sites/pet-video-showcase"
```

Expected: 生成 `app/page.tsx`、`app/layout.tsx`、`app/globals.css`、`package.json` 和 `.openai/hosting.json`。

- [ ] **Step 2: 启动并保持本地预览**

```bash
npm run dev
```

Expected: 输出一个可访问的 Local URL。

- [ ] **Step 3: 复制最终成片和精选证据素材**

```text
sealed_delivery_v3_1_20260810/final_master.mp4 -> public/media/final-master.mp4
motion_v3/V3_action_frame_audit.jpg -> public/evidence/action-frame-audit.jpg
motion_v3/V3_eight_transitions.jpg -> public/evidence/eight-transitions.jpg
physics_repair_v2/S004_dense.jpg -> public/evidence/physics-s004.jpg
physics_repair_v2/S012_dense.jpg -> public/evidence/physics-s012.jpg
physics_repair_v2/S018_dense.jpg -> public/evidence/physics-s018.jpg
sealed_delivery_v3_1_20260810/beat_contact_sheet.jpg -> public/evidence/beat-contact-sheet.jpg
```

Expected: 七个素材文件均存在且大小大于零。

- [ ] **Step 4: 检查视频元数据**

```bash
ffprobe -v error -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of json public/media/final-master.mp4
```

Expected: `1076x1920`、`30/1`、`204.766667` 秒。

### Task 2: 构建单页放映室体验

**Files:**
- Modify: `sites/pet-video-showcase/app/page.tsx`
- Modify: `sites/pet-video-showcase/app/globals.css`
- Modify: `sites/pet-video-showcase/app/layout.tsx`
- Delete: `sites/pet-video-showcase/app/_sites-preview/`

**Interfaces:**
- Consumes: `/media/final-master.mp4`、`/evidence/*.jpg`。
- Produces: `ProjectShowcasePage` 和 `TabId = "film" | "repairs" | "timeline"`。

- [ ] **Step 1: 定义页面数据**

```tsx
type TabId = "film" | "repairs" | "timeline";
const metrics = [
  { label: "成片时长", value: "204.77 秒" },
  { label: "镜头", value: "43" },
  { label: "画面", value: "30 fps" },
  { label: "验收", value: "PASS" },
];
```

Expected: 数据覆盖成片状态、四类修复和迭代节点，所有展示文案均为最终内容。

- [ ] **Step 2: 实现首屏放映区**

```tsx
<video controls playsInline preload="metadata" aria-label="《咪要去面试》最终成片">
  <source src="/media/final-master.mp4" type="video/mp4" />
</video>
```

Expected: 视频、片名、版本和四项状态同屏可读，首屏底部露出标签导航。

- [ ] **Step 3: 实现可访问标签交互**

标签按钮使用 `role="tab"`、`aria-selected`、`aria-controls`，支持左右方向键切换。

Expected: 鼠标、触摸和键盘均能在四个标签间切换，页面不跳转。

- [ ] **Step 4: 实现修复证据和时间线**

```text
多肢体与肢体归属 -> S004/S012 密集帧
道具物理 -> S018 单纸杯运动链
动作卡顿 -> 861 降至 229，减少约 73%
场景过渡 -> 八个剧情边界单帧淡黑，交叉叠化标记为未采用
```

Expected: 每张图片都有准确替代文本；时间线每项包含发现、判断、处理、效果。

- [ ] **Step 5: 完成视觉和响应式样式**

```css
:root {
  --ink: #111312;
  --paper: #f4f5f2;
  --surface: #ffffff;
  --line: #d8dcd5;
  --accent: #55d66b;
  --muted: #667067;
}
```

Expected: 无渐变、无装饰光斑、无文本溢出；手机和桌面均保持媒体比例与可读间距；减少动画设置下关闭非必要动画。

- [ ] **Step 6: 更新元数据并清理预览骨架**

标题使用 `宠物短剧项目放映室`，描述使用 `《咪要去面试》成片、问题修复与迭代档案`；删除 starter 预览组件和 `codex-preview` 标记。

Expected: 页面与分享信息不再出现 starter 文案。

### Task 3: 验证并私密发布

**Files:**
- Modify: `sites/pet-video-showcase/.openai/hosting.json`
- Create: `sites/pet-video-showcase/public/og.png` only when generated text is correct

**Interfaces:**
- Consumes: 完整站点源码和静态媒体。
- Produces: 成功构建的站点版本和私密 Sites URL。

- [ ] **Step 1: 生成并检查社交预览图**

生成一张包含准确文字 `宠物短剧项目放映室` 和 `咪要去面试` 的横向分享图，沿用黑白绿色调。文字错误时只重试一次；仍不可用则不配置分享图。

Expected: `public/og.png` 仅在文字和视觉均准确时存在。

- [ ] **Step 2: 执行生产构建**

```bash
npm run build
```

Expected: 构建成功并生成 Sites 可发布输出。

- [ ] **Step 3: 验证关键资源和页面内容**

```bash
test -s public/media/final-master.mp4
test -s public/evidence/action-frame-audit.jpg
rg -n "宠物短剧项目放映室|861|229|2847|62" app/page.tsx
```

Expected: 所有检查退出码为 0。

- [ ] **Step 4: 创建并保存 Sites 项目版本**

使用 Sites 创建项目，将 `project_id` 写入 `.openai/hosting.json`，打包已验证源码并保存一个版本。

Expected: 保存的站点版本与当前构建内容一致。

- [ ] **Step 5: 私密发布并等待成功**

使用私密部署接口发布版本并轮询到 `succeeded`。

Expected: 获得可访问的私密 Sites URL。

- [ ] **Step 6: 打开并交付站点**

在 Codex 内打开最终 URL，停止本地开发服务，并返回站点链接。

Expected: 用户可以播放成片、切换四个标签并查看修复、迭代与历史素材。

### Task 4: 增加历史素材库并发布 V2

**Files:**
- Modify: `sites/pet-video-showcase/app/page.tsx`
- Modify: `sites/pet-video-showcase/app/globals.css`
- Modify: `sites/pet-video-showcase/tests/rendered-html.test.mjs`
- Create locally for packaging: `sites/pet-video-showcase/public/archive/*.mp4`
- Create locally for packaging: `sites/pet-video-showcase/public/archive/*.jpg`

**Interfaces:**
- Consumes: 八条历史母版视频。
- Produces: `ArchiveCategory = "all" | "comic" | "voice" | "pet"`、八条 `archiveItems` 和互斥播放行为。

- [ ] **Step 1: 为八条历史素材生成网页播放副本**

每条视频使用 H.264、AAC、`+faststart` 和最长边 720 像素生成独立副本；六音色与三角色小样保持原文件。输出文件名固定为：

```text
old-city-letter.mp4
six-voices.mp4
three-role-dialogue.mp4
freeze-dried-v1.mp4
freeze-dried-refined.mp4
cat-ideas-pilot.mp4
cat-ideas-final.mp4
interview-cat-v2.mp4
```

Expected: 八条副本均有视频流、非零时长且适合网页渐进播放，原始母版哈希不变。

- [ ] **Step 2: 为每条素材提取独立封面**

从每条网页副本的有效画面中提取 JPEG 封面，文件名与视频一致，仅把扩展名改为 `.jpg`。

Expected: 八张封面均存在，画面不为纯黑，不使用失败候选帧。

- [ ] **Step 3: 先补充历史素材与播放互斥测试**

在 `tests/rendered-html.test.mjs` 中断言：

```text
页面源码包含 archiveItems 的八条固定文件名
页面包含 ArchiveCategory 四种筛选值
页面包含 onPlayVideo，并暂停所有不等于当前播放器的 video 元素
CSS 包含 archive-grid、archive-card 和 720px 移动断点
```

Expected: 在页面实现前新增测试失败，失败原因是历史素材结构不存在。

- [ ] **Step 4: 实现第 4 个历史素材标签**

在标签数组增加 `library`，并实现四类分段筛选。每条素材显示封面、名称、日期、阶段、时长、分类和简短说明，点击原生视频控件即可播放。

Expected: 筛选只改变当前列表，不新增路由；每条视频具有独立 `aria-label` 和 poster。

- [ ] **Step 5: 实现互斥播放**

`onPlayVideo(current)` 遍历页面所有视频元素，并对非当前且未暂停的视频调用 `pause()`。

Expected: 首屏主成片与八条历史视频同时最多只有一个处于播放状态。

- [ ] **Step 6: 完成素材库响应式样式**

桌面端使用两列作品网格，移动端改为单列；分段筛选可横向滚动，视频始终保持稳定的 9:16 或源比例，不因加载状态改变布局。

Expected: 无文本溢出、卡片嵌套或媒体遮挡。

- [ ] **Step 7: 构建、测试并发布 V2**

```bash
npm test
```

Expected: 全部测试通过；保存 Sites 新版本并私密发布到现有站点地址。
