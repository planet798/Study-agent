# Windows 安装版构建与验收

应用采用 PyInstaller onedir，安装器采用 Inno Setup 6。默认安装到 `%LOCALAPPDATA%\Programs\StudyAgent`，安装向导允许自主选择目录。用户数据独立存于 `%LOCALAPPDATA%\StudyAgent\data`，程序资源放在安装目录的 `_internal`，更新安装路径不会更改数据位置。

## 构建

在 GitHub Actions 的 **Windows release** 工作流选择 **Run workflow**，版本填写 `0.2.2`。手动触发仅生成构建附件；推送 `vX.Y.Z` 标签则在所有检查通过后创建 Release 草稿，不自动公开发布。已成功的手动构建可通过 **Create verified release draft** 工作流输入原构建 Run ID 与版本生成草稿；它复核原构建通过状态、提交、安装包校验值与冻结自检报告，并直接在 GitHub 上传附件，避免重跑构建或经本机转传大文件。

工作流并行执行 Linux 完整 pytest 回归/OAuth Node 测试和 Windows 数据/迁移/启动测试、构建、安装及离线冻结程序自检；两条检查链均通过后才能创建发布草稿。Node 固定为 22.19.0，Python 为 3.12，Linux 回归、Windows 生产依赖和打包工具由 `packaging` 下锁定清单管理；两端应用运行库版本一致。

本机 Windows 构建需要 Python 3.12 x64、Node.js 22.19.0、Inno Setup 6.5.4。在隔离的打包虚拟环境中执行：

```powershell
py -3.12 -m venv .venv-build
.venv-build\Scripts\Activate.ps1
python -m pip install -r packaging/windows-build.txt
python -m pip check
.\scripts\build_windows.ps1 -Version 0.2.2
```

脚本根据版本生成 `app/version.py`；本机执行会修改该文件，发布标签必须与构建版本一致。产物位于 `dist/installer`。不要提交 `.venv-build`、`build` 或 `dist`；源码、用户 `data`、真实配置、开发测试工具不会被作为应用资源打包。

冻结程序支持 `StudyAgent.exe --self-test <报告路径>`，使用离线模型目录检验 Node/OAuth 桥、Qt 图标与主题和 Windows 凭据后端，不读取真实凭据或创建学习数据库。CI 自动验证中文/空格安装路径、隔离 PATH、覆盖安装及卸载后数据保留；这些检查不能代替正常交互验收。

## 应用内更新（0.2.2 起）

设置页保留三个标签，在外观下方显示应用更新。只有用户点击检查时联网，从 `planet798/Study-agent` 最新正式 Release 匹配版本、安装程序和 `SHA256SUMS.txt`；不读取 GitHub Token 或模型凭据。后台下载使用 HTTPS 白名单，交叉检查 Release 附件摘要，完整包再次使用前仍校验。第一版使用完整安装包，不做断点续传或增量升级。

下载缓存位于 `%LOCALAPPDATA%\StudyAgent\updates`。点击安装前检查后台任务、隐藏 Prompt 草稿、Agent 说明和未发送聊天内容；需要用户先完成或明确放弃修改，不自动保存或丢弃。检查、下载和校验失败时应用继续运行。

构建包含独立 onefile `StudyAgentUpdater.exe`，其 Python 与 PyInstaller 许可沿用安装包内的许可收集结果。辅助程序先复制到缓存，取得升级锁并握手就绪；主程序随后通过真正退出路径释放线程、桥接、数据库和单实例资源。辅助程序以 Windows 进程句柄等待退出（最多 60 秒），再用 Inno Setup `/SILENT` 显示安装进度，沿用原目录和快捷方式，禁止强杀其他程序及重启电脑。仅成功安装后自动启动一次新版，新版进入主窗口并核对版本才确认成功。

安装失败保留安装包和最近失败任务的 `result.json` / `installer.log`；辅助程序显示实际安装包路径，用户可重新运行它修复安装。安装中途失败可能部分替换文件，不自动启动可能不完整的程序，不实现自动回滚。SHA256 用于完整性校验，不等同于代码签名。旧版首次升级到 0.2.2 仍需手动安装。

Windows CI 增加真实打包辅助程序测试：在 `RUNNER_TEMP` 下隔离用户目录，安装较旧正式包（有较旧版本时），保存合成任务、会话、消息和文件，验证等待进程退出、覆盖安装、自动启动及历史保留。产物 `updater-self-test.json` 记录实际基线和目标版本；0.2.2 起发布草稿工作流要求此报告通过。这些自动检查不代替用户正常桌面交互验收。

## 数据保护

导入只允许目标数据目录不存在时执行，不合并已有数据库。SQLite Backup API 捕获已提交 WAL，托管工作区和配置在暂存目录复制，检查通过后用同盘目录改名启用。拒绝工作区 symlink/junction，防止沿链接复制外部内容；日志不导入。旧项目 `docs/career_context.json` 存在时一并导入，外部项目绑定保持原路径。

数据库升级需用户明确确认。原库一致备份和历史盘点存于 `data/backups/<操作编号>`；只在副本升级，历史校验成功且原库未发生变化时替换。不可读、升级预演失败、历史校验失败或高于程序支持版本均停止启动，保留源库。源码运行仍保留既有目录与发布迁移 CLI；安装版忽略开发用自动迁移绕过开关。

安装器只卸载自身登记的文件，不删除用户数据或系统凭据。用户自主选择的外部项目、MCP 服务和 Docker 均不在安装器管理范围内。

## 正式发布前的 Windows 实机验收

- [ ] Windows 10/11 x64，无 Python/Node 环境，下载安装并双击快捷方式启动。
- [ ] 自选安装目录、中文/空格路径，桌面和开始菜单快捷方式及应用图标正常。
- [ ] 全新开始、取消首次启动、导入旧数据成功/失败后的再次启动。
- [ ] 对比导入前后的任务、会话历史、托管文件、外部项目绑定和学习上下文。
- [ ] API Key 配置、订阅账号浏览器登录、模型调用及 Windows 凭据保存。
- [ ] 重复启动恢复原窗口，最小化/关闭进入托盘，托盘退出正常。
- [ ] 旧库升级确认、拒绝升级、失败保留原库、备份可恢复。
- [ ] 设置中检查、下载、取消下载和安装重启；草稿/后台任务阻止安装、失败显示真实状态。
- [ ] 覆盖安装不丢数据；卸载保留数据，重装可继续使用。
- [ ] 用户数据不出现在安装目录，安装包包含第三方许可文件。

完整记录 CI 结果与上述人工结果后，才将 Release 草稿公开发布。0.2.0/0.2.1 无应用内更新；0.2.2 加入手动检查、下载校验和自动安装重启，安装程序仍未签名，不把自动检查通过记为实机验收通过。

## 0.2.0 自动验证记录（2026-10-04）

- 安装包对应应用提交 `e3dd3bf0580716bd4b9cff9542a3fc5df8b979c0`；[构建与完整回归](https://github.com/planet798/Study-agent/actions/runs/37192762034) 全部通过：Linux 3034 项、Windows 123 项。
- 冻结程序离线自检通过，内置 OAuth 桥返回 9 个提供商；中文/空格自选安装目录、隔离 PATH、覆盖安装与卸载后数据保留检查通过。本机下载后 SHA256 复核通过。
- [发布草稿生成](https://github.com/planet798/Study-agent/actions/runs/37194089299) 已通过；[0.2.0 草稿](https://github.com/planet798/Study-agent/releases/tag/untagged-16f1b51bde46709dee1b) 包含安装程序与 SHA256 校验文件。
- 上述为自动验证；正常桌面交互、真实订阅登录及人工验收仍待用户执行，未公开正式发布。
