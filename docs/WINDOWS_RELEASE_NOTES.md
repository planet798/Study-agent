Windows 10（1809 或更新版本）/11 x64 安装版，内置 Python、Qt、Node.js 与订阅登录组件。

1. 下载 `StudyAgent-Setup-版本-x64.exe`，双击安装；默认安装到当前用户目录，也可自主选择安装路径。
2. 从桌面或开始菜单快捷方式启动。无需执行终端命令；AI 功能仍需在设置中配置 API Key 或登录订阅账号。
3. 首次启动可全新开始，也可选择源码版 `data` 目录导入。请先退出旧版；原数据保留，导入包含数据库、托管工作区及本地配置。

用户数据位于 `%LOCALAPPDATA%\StudyAgent\data`，启动诊断日志位于 `%LOCALAPPDATA%\StudyAgent\logs`，默认笔记导出位于 `%LOCALAPPDATA%\StudyAgent\notes`。

升级请先退出程序，再运行新版安装包。数据库需要升级时会要求确认，先备份并在副本中校验；失败停止启动并保留原数据。备份位于数据目录的 `backups` 子目录。卸载保留学习数据。

同一 Windows 用户可继续使用系统凭据存储中的登录凭据；换电脑后需重新配置或登录。外部项目路径失效时需重新绑定；Docker 和外部 MCP 服务不随安装包提供。

首版未签名，Windows 可能显示来源提示。附件 `SHA256SUMS.txt` 可用于检查下载完整性。

本 Release 应在 Windows 实机验收通过后，从草稿发布。
