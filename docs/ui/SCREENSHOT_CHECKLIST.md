# Study-Agent — Screenshot Baseline Checklist (UI-0)

> 基线 commit: `a36e688`
> 目的：为 UI-1 → UI-5 提供**人工可对比的视觉基线**。
> **不要为了截图修改 production UI。**
> 截图由用户在 Windows 上手动完成（当前 WSL 环境不适合运行完整 GUI 应用）。

---

## 0. 环境说明

- UI-0 期间已验证：WSL 下 `QT_QPA_PLATFORM=offscreen` 可以创建并抓取最小 widget
  （探针输出 `offscreen OK`）。但完整 `Study Agent` 启动需要真实 DB、Migration Gate、
  service 装配与跨日 preflight 对话框，**不适合在 audit 阶段自动生成生产页面截图**。
- 因此本阶段**以人工 Windows 截图为基线**；offscreen 自动截图能力可在 UI-1/UI-2 通过
  独立 demo fixture（临时 DB + 假数据）择机引入，不在 UI-0 实现。

---

## 1. 建议窗口尺寸

每个页面至少两张：

| 尺寸 | 用途 |
|---|---|
| **1440 × 900** | 主流笔记本 / 中等窗口，验证默认密度 |
| **1920 × 1080** | 大屏，验证留白与最大宽度 |

可选补充：**1250 × 780**（最小可用窗口附近，接近当前 `setMinimumSize(560,460)` 之上的实际使用尺寸）。

---

## 2. 必拍清单

请在拍摄时记录：**commit `a36e688`、Windows 缩放比例、窗口尺寸**。

| # | 截图 | 页面/入口 | 备注 |
|---|---|---|---|
| 1 | Today（有任务） | 启动后默认页 | 今日新知识 + 今日复习 + 统计 + 技能概览 + JD 趋势 |
| 2 | Routes overview | 导航「学习路线」 | 含分组卡片 + 子路线卡片 + 归档开关 |
| 3 | Route detail | 路线卡片「查看路线」 | 进度/Mastery/Capability/Phase/Topic/Skill/Gap/Project |
| 4 | Practice overview | 导航「实践项目」 | 项目卡片列表 + 状态筛选 |
| 5 | Practice detail | 项目卡片「查看」 | Milestone/Output/Readiness/Evidence 各 section |
| 6 | Monthly | 导航「月总结」 | 统计 + 分类排行 + 路线维度 + 项目能力证据 + AI 解读 |
| 7 | AI Settings — Profiles | 导航「AI 设置」→ 模型/API | Profile 列表 + 详情 + 连接测试区 |
| 8 | Prompt Manager | 导航「AI 设置」→ Prompt 管理 | 树 + 编辑器 + 变量 + 预览 + 路线选择 |
| 9 | Add Task dialog | Today「＋ 添加学习任务」 | 普通 To-do / 正式知识任务切换 |
| 10 | Assessment dialog | 任务卡「开始验收」 | 问答区 + 提交 + loading/error |
| 11 | Empty state | 各页面无数据时 | Today 无任务 / Practice 无项目 / Monthly 无数据 |
| 12 | Populated state | 各页面有数据时 | 与 #1–#6 对应，可复用 |

### 建议补充（P1 polish 基线）
| # | 截图 | 入口 |
|---|---|---|
| 13 | 路线/阶段/主题新建对话框 | 新建学习路线 / 添加阶段 / 添加 Topic |
| 14 | JD 汇总输入 & 候选接受 | Today「添加今日 JD 技术汇总」/「加入技能体系」 |
| 15 | Capability 证据 / 实验成果 | Route detail「查看证据」/「记录实验成果」 |
| 16 | 未完成原因 + AI 复核结果 | Today「未完成」流程 |
| 17 | 跨日未确认任务确认框 | 启动 preflight（如可复现） |
| 18 | 托盘右键菜单 | 系统托盘图标 |

---

## 3. 每张截图要额外观察并记录（为 UI-1 输入）

- 控件是否使用系统原生外观（QComboBox / QTab / 滚动条 / 菜单）
- 文字是否被截断（长路线名 / 长描述 / 大字体）
- 标签是否只能靠 `【】` 文本区分
- 按钮层级是否清晰（Primary / Secondary / Danger）
- 错误/警告/成功是否只靠颜色
- 空状态是否有引导动作
- 窗口缩放 100% / 125% / 150% / 175% 下布局是否破裂

---

## 4. 输出目录约定（建议）

```
docs/ui/screenshots/
    baseline_a36e688/
        today_1440x900.png
        today_1920x1080.png
        routes_overview_1440x900.png
        ...
```

命名：`<page>_<width>x<height>.png`，可选后缀 `_125dpi`。

---

## 5. UI-0 状态

- [x] 已确认 offscreen 最小探针可用（不足以自动截取生产页面）
- [ ] 用户在 Windows 完成 #1–#12 截图（UI-1 开始前）
- [ ] 记录 Windows 缩放比例与窗口尺寸
