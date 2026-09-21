# Study Agent

Windows 桌面学习管理工具（Python + PySide6 + SQLite）

## 当前阶段：MVP

- 今日学习任务展示
- 任务完成勾选 / 未完成原因 / 延期
- 日期切换与延期任务自动搬运
- 今日 / 周 / 月统计

## 环境

- Python 3.12+
- SQLite（Python 内置模块，无需额外安装）

## 开发环境设置

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 运行

```bash
cd app
python main.py   # 或从项目根目录: python -m app.main
```

首次启动会在 data/ 生成 study_agent.db（SQLite）。
最小化窗口会收进系统托盘（若平台支持），右键托盘图标可“打开 / 退出”。

## 测试

```bash
source .venv/bin/activate
pytest
```

GUI 测试在无显示环境下自动使用 offscreen 平台，并通过注入固定日期，
不依赖真实系统日期。

### 开发/测试：模拟日期启动（Windows PowerShell）

```powershell
python app\main.py --date 2026-09-05
```

仅作开发/测试用：把“今天”模拟为指定日期，
只改内存中的 date provider（`app/utils/date_utils.py` 的 `set_today_provider`），
不写数据库、不改系统时间、不改任何业务规则；不传 `--date` 时完全使用系统真实日期。

## 当前功能（GUI 阶段）

- 主窗口：标题 + 日期 + 今日学习任务列表 + 今日统计
- 任务卡片：标题/描述/分类/优先级/预计时间/状态
- 完成：勾选后即时完成并更新统计
- 未完成：必须填写原因（非空校验），否则不能提交
- 延期：not_done 任务可“延期到明天”，延期次数自动 +1，
  连续 3 次显示警告
- 启动自动执行日期切换（幂等，不重复生成任务）
- 系统托盘：最小化到托盘、右键菜单（打开/退出）
- 全部数据持久化在 SQLite
- AI 复核：标记未完成 → 后台调用 DeepSeek → 结构化结果显示（合理程度/建议延期/分析/建议）
  - AI 未配置或调用失败时不崩溃，自动降级为纯本地功能
- AI 动态规划：根据最近 7 天完成情况/延期/学习时间调整每日任务
  - 主窗口新增“AI 今日规划”区域（状态 + 重新规划今天按钮）
  - AI 只调建议，本地校验后创建；失败自动回退规则型生成
  - 重规划只动 active+generated 任务，永不动 done/not_done/延期/手动任务
  - 每次决策保存到 planner_decisions 表供审计
- 周/月总结与学习趋势：
  - 主窗口顶部导航 [今日] [周总结] [月总结]，使用 QStackedWidget（无复杂 Dashboard）
  - 统计全部本地实时计算（完成率/学习时间/学习天数/连续学习/分类/主题/延期）
  - AI 只解读数据、找问题、给建议；AI 不可用则仅显示本地统计并提示
  - 周/月总结结果缓存到 weekly_summaries/monthly_summaries，统计未变则直接复用
  - 习惯指标：最常延期分类/Topic、平均每日任务数、平均完成率、连续天数等

## AI 设置中心（推荐）

在顶部导航打开 **AI 设置**，无需修改任何源码或环境变量：

- **模型 / API**：添加 / 删除 / 重命名 API 配置，修改 Base URL / Model / API Key，
  切换当前配置，测试连接。切换后**下一次 AI 请求立即生效**（无需重启）。
- **Prompt 管理**：查看 Study Agent 全部内置 Prompt、用途、变量；直接编辑并保存；
  恢复系统默认；查看最终实际发送给模型的 Prompt 预览（含真实运行时上下文）。

### API Key 存在哪里

- API Key **绝不写入 SQLite / JSON / 源码 / 日志**；
- 数据库只保存 `secret_ref`，真实 Key 由 `keyring` 写入系统凭据存储
  （Windows 上为 **Windows Credential Manager**）；
- 需要 `keyring` 依赖（见 `requirements.txt`）。

### 环境变量（legacy 兼容）

数据库还没有任何 API 配置时，仍会回退使用环境变量：

```bash
# Linux/macOS
export DEEPSEEK_API_KEY="你的 Key"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-chat"

# Windows PowerShell
$env:DEEPSEEK_API_KEY="你的 Key"
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
$env:DEEPSEEK_MODEL="deepseek-chat"
```

此时 AI 设置页会显示“当前正在使用环境变量配置”，可点击【保存为配置】把
Base URL / Model 迁入数据库，并把环境变量中的 Key 写入系统凭据存储。

- 不配置则 GUI 正常运行，AI 区域提示"AI 未配置"，其余功能不受影响
- BASE_URL 缺省为 https://api.deepseek.com；MODEL 未设置时视为未配置
- 可替换为任何 OpenAI-compatible 服务（Gemini / OpenAI / USTC 等）
- `AIClient.chat(..., json_mode=True)`（默认）发送 `response_format=json_object`；
  通用文本对话可传 `json_mode=False`。注意 DeepSeek 要求 prompt 中包含
  "json" 字样才能配合 json_object 模式，否则返回 HTTP 400

### Prompt 覆盖机制

- 系统默认 Prompt 永远保留在代码中（`app/ai/prompt_defaults.py`），是 canonical default；
- 用户在 UI 中的修改只写 `prompt_overrides` 表，`git pull` 不会覆盖；
- “恢复默认” = 删除对应 override，重新使用代码默认；
- 保存时会校验必需变量与未知变量（`{{variable}}` 语法，安全替换，绝不 eval/format）。

## 真实 API 冒烟测试

配置好环境变量后运行：

```bash
.venv/bin/python scripts/smoke_test_ai.py
```

预期输出：
```
API connectivity: OK
Model: <DEEPSEEK_MODEL>
JSON output: OK
```
（不会打印 API Key）

## Windows 一键启动

不需要打开 PowerShell、不需要手动激活虚拟环境，
双击桌面快捷方式即可启动 Study Agent。

### 第一次：创建桌面快捷方式

在 PowerShell（项目目录任意位置）执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1
```

执行后：
- 桌面出现 `Study Agent.lnk`；
- 同时创建开始菜单入口「Study Agent」；
- 已存在同名快捷方式时**原地更新**，不会重复创建。

### 以后：双击启动

双击桌面 `Study Agent` 即可：
- 自动切换到项目目录；
- 自动使用项目 `.venv\Scripts\python.exe` 启动 `app\main.py`；
- 启动器以**隐藏窗口**运行，不出现长期停留的 PowerShell 黑窗口；
- Study Agent 自身的 PySide6 窗口正常显示。

### 删除快捷方式

桌面：直接删除 `Study Agent.lnk`。
开始菜单：删除「Study Agent」文件夹。
或再次运行创建脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_shortcut.ps1 -Remove
```

### 项目目录移动后

重新运行一次 `create_shortcut.ps1` 即可，快捷方式会自动指向新位置。

### 诊断脚本

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\diagnose_windows.ps1
```

输出项目根目录、Python 路径/版本、PySide6 版本、DeepSeek 配置状态。
**DEEPSEEK_API_KEY 只显示 Configured / Missing，绝不打印 Key。**

### DeepSeek 环境变量说明

桌面快捷方式启动的程序会继承当前 Windows 用户的**用户级环境变量**。
请在 Windows 系统设置（或 `setx`）中把以下变量配置为**用户环境变量**，
而不是只在 PowerShell 会话里 `$env:` 临时设置（临时设置不会被桌面启动继承）：

```
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
```

未配置时 Study Agent 正常运行，AI 区域显示“AI 未配置”。
API Key 不会写入任何脚本 / 快捷方式 / 配置文件 / 日志。

### 启动日志

启动器把启动/失败信息记录到 `data\logs\launcher.log`，
日志不含 API Key、Authorization、完整 Prompt 或完整 API Response。

## 项目结构

```
study-agent/
├── app/
│   ├── main.py                 # 入口
│   ├── ui/                     # GUI 层（开发中）
│   ├── database/               # 数据层
│   ├── services/               # 业务逻辑层
│   ├── ai/                     # AI 接口（占位，未接入 API）
│   └── utils/                  # 工具函数
├── data/                       # SQLite 数据库文件（logs/ 为启动日志）
├── scripts/                    # Windows 启动/快捷方式/诊断脚本
│   ├── create_shortcut.ps1     #   创建桌面+开始菜单快捷方式
│   ├── start_study_agent.ps1   #   一键启动器（隐藏窗口）
│   ├── diagnose_windows.ps1    #   启动诊断（不打印 API Key）
│   └── smoke_test_ai.py        #   真实 AI 冒烟测试
├── tests/                      # 测试
├── requirements.txt
└── README.md
```

## 六技术路线（R1–R6）与历史迁移

正式技术学习体系固定为六条 canonical learning route：

```
求职准备 (JOB_PREP)
├─ R1 LLM Fundamentals          (R1_LLM_FUNDAMENTALS)
├─ R2 LLM Post-Training         (R2_LLM_POST_TRAINING)
├─ R3 LLM Infra                 (R3_LLM_INFRA)
├─ R4 AI Agent                  (R4_AI_AGENT)
├─ R5 Recommendation & Search   (R5_RECOMMENDATION_SEARCH)
└─ R6 CS Fundamentals           (R6_CS_FUNDAMENTALS)
```

- 系统身份使用稳定的 `learning_routes.route_key`（不依赖显示名称）。
- 旧 `搜广推 + LLM` 被标记为 `LEGACY_SEARCH_LLM` 并 **archive 保留**，不删除、不复用。
- 历史 Topic 采用三种策略迁移：
  - **MOVE**：保留原 topic id，reparent 到新路线对应的 phase；
    topic-linked `tasks.route_id` / `knowledge_points.route_id` 同步更新。
    assessment / review / mastery 不复制、不删除、不改分。
  - **SPLIT_NEW**：跨路线语义的旧 Topic 保留在 LEGACY；新的 route-specific
    Topic 由 canonical seed 创建，**不继承**旧 mastery。
  - **KEEP_LEGACY / MANUAL_REVIEW**：保留在旧路线，不迁移。
- 同一 route 只允许一个 active plan（v15 partial unique index）。

### CLI（真机迁移前先备份）

```bash
# 1) 只读盘点（不写库）
python -m app.main six-routes inventory --db "D:\\Projects\\study-agent\\data\\study_agent.db"

# 2) 迁移预览（无 conflict 才 apply）
python -m app.main six-routes preview --db "...\\study_agent.db"

# 3) 执行迁移（默认跳过有冲突的 Topic；--strict 则遇到 conflict 整体拒绝）
python -m app.main six-routes apply --db "...\\study_agent.db"
```

正常启动时，应用会自动执行 `seed + apply_if_safe`（无 conflict 才迁移），
因此通常无需手动运行 CLI。迁移是幂等的：重复运行不会重复 seed / 迁移。

## Capability Evidence（能力证据）

Capability 与 Mastery 是两个独立维度：Capability 只由真实证据推导，
不使用 mastery 阈值，也不修改 mastery / review。

| Level | 含义 | 来源 |
| --- | --- | --- |
| 0 | UNLEARNED 未学习 | 无证据 |
| 1 | AWARE 知道概念 | 完成的正式学习任务 |
| 2 | EXPLAIN 能够解释 | 验收（概念题） |
| 3 | IMPLEMENT 能够写代码 | 验收（实现题） |
| 4 | EXPERIMENT 完成独立实验 | 实验成果（含可验证产物） |
| 5 | PROJECT 已在真实项目中使用 | PracticeTopicEvidence（用户显式确认） |

`current capability = MAX(active evidence level)`；撤销证据后自动回落到其它仍生效的证据。

### Practice → Capability（v19）

Level 5 **只能**由实践项目的“项目使用证据”产生，且必须同时满足：

1. 项目状态为 `completed`（未归档）；
2. Topic 已关联该项目，且 Topic 所属路线在项目关联路线内；
3. 用户填写非空的“项目使用说明”；
4. 至少选择一个属于该项目的“支撑成果”，其中至少一个是 qualifying artifact
   （repository / code / result / benchmark / checkpoint / demo / paper_reproduction，
   且结构有效，例如 Repository 必须有链接）；
5. 用户**显式勾选确认**。

数据链：`PracticeProject → Project Topic → PracticeTopicEvidence → selected Outputs
→ CapabilityEvidence(evidence_type='practice_project', evidence_key='practice_topic_evidence:<id>')`。

- 不会因为项目 completed / 成果数量 / 关联 Topic 自动产生 Level 5；
- 不自动遍历项目 Topic（必须逐 Topic 确认）；
- 已存在的 Phase 4 项目**不会自动 backfill**；
- 撤销 = `is_active=0` + `revoked_at` + `revocation_reason`，历史行保留，
  并同步撤销对应 capability evidence；
- **历史完整性（Phase 5.1）**：revoke 的语义是“不再参与 current capability”，
  不是“历史上从未存在”。因此只要 Output / Project-Topic 曾经被任意
  PracticeTopicEvidence 使用过（active 或 revoked）：
  - Output 禁止物理删除；`output_type / description / uri / details_json`
    永久冻结（仅允许改 `title`）；
  - Project-Topic 关联禁止移除，其对应 Route 关联也不能因此被拆掉；
  - Evidence Timeline 仍展示已撤销证据的创建/撤销时间、撤销原因、来源项目、
    Topic 与当时关联的支撑产出；
  - 从未被 evidence 使用过的 Output 仍按 Phase 4 原逻辑正常编辑/删除；
- archive / reopen 项目不会撤销或降低已产生的历史能力证据；
- 产生过证据（含已撤销历史）的项目禁止物理删除。

> DB 层 `practice_topic_evidence_outputs.output_id` 仍为 `ON DELETE CASCADE`；
> 历史完整性目前由 Service invariant 保证（生产代码永不删除被引用的 Output），
> 并有测试锁定。Phase 5.1 不做危险的表重建。

Project Skill / Activity / Curriculum / Mastery / Review / Planner / Scheduler
均**不**因 PROJECT evidence 改变。
