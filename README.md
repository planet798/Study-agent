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

## AI 配置（可选）

通过**环境变量**配置，不要把 API Key 写进任何代码/配置文件：

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

- 不配置则 GUI 正常运行，AI 区域提示"AI 未配置"，其余功能不受影响
- BASE_URL 缺省为 https://api.deepseek.com；MODEL 未设置时视为未配置
- 也可替换为任何 OpenAI-compatible 服务（Gemini / OpenAI / USTC 等）
- `AIClient.chat(..., json_mode=True)`（默认）发送 `response_format=json_object`；
  通用文本对话可传 `json_mode=False`。注意 DeepSeek 要求 prompt 中包含
  "json" 字样才能配合 json_object 模式，否则返回 HTTP 400

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
