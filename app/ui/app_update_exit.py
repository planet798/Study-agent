"""主窗口更新退出编排；保护后台工作和仅驻留内存的草稿。"""
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog

from app.updates.models import UpdateError


def exit_block_reason(window) -> str | None:
    profiles = window.ai_settings_page.profiles_panel
    dialogs = window.findChildren(QDialog)
    workers = list(window._ai_workers) + window.findChildren(QThread)
    workers += [getattr(dialog, "_worker", None) for dialog in dialogs]
    workers += [profiles._test_worker, profiles._oauth_worker]
    for worker in workers:
        if worker is not None:
            try:
                if worker.isRunning():
                    return "后台任务尚未结束，请等待回复、审批、验收、规划或登录完成后重试。"
            except RuntimeError:
                continue  # 已销毁的完成线程不是正在运行的任务。
    if (window._agent_inflight_sessions or window._approval_inflight
            or window._assessment_inflight or window._memory_extraction_running()):
        return "后台任务尚未结束，请稍后安装更新。"
    settings = window.ai_settings_page
    if (settings.personalization_panel._drafts.has_pending
            or settings.prompt_panel._drafts.has_pending):
        return "设置中有未保存修改，请先保存或明确放弃所有 Agent 说明和 Prompt 草稿。"
    workspace = window.agent_workspace_page
    if workspace is not None and workspace.composer.text():
        return "学习会话中有未发送内容，请先发送或清空输入框后再安装更新。"
    if any(dialog.isVisible() for dialog in dialogs):
        return "请先完成或关闭当前弹窗，再安装更新。"
    return None


def begin_update(window):
    controller = window.ai_settings_page.update_panel.controller
    reason = exit_block_reason(window)
    if reason:
        controller.fail_preparation(reason)
        return
    if controller.downloaded is None:
        return
    window.centralWidget().setEnabled(False)
    controller.prepare()


def commit_update(window):
    controller = window.ai_settings_page.update_panel.controller
    reason = exit_block_reason(window)
    if reason:
        controller.fail_preparation(reason)
        return
    try:
        controller.commit_exit()
    except (OSError, UpdateError):
        controller.fail_preparation("无法确认升级任务，应用保持运行。")
        return
    window.quit_app()
