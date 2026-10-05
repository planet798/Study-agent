"""Windows 进程句柄与升级锁；独立辅助程序无需 Qt。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import sys

from .models import UpdateError


def kernel():
    if os.name != "nt":
        raise UpdateError("自动安装仅支持 Windows 安装版。")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenMutexW.restype = wintypes.HANDLE
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
    api.WaitForMultipleObjects.restype = wintypes.DWORD
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return api


def lock_name() -> str:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise UpdateError("无法确定 Windows 用户目录。")
    identity = str(Path(local).resolve()).casefold().encode("utf-8")
    return "Local\\StudyAgentUpdate-" + hashlib.sha256(identity).hexdigest()[:24]


class UpdateLock:
    def __enter__(self):
        self.api = kernel()
        self.handle = self.api.CreateMutexW(None, False, lock_name())
        already_exists = ctypes.get_last_error() == 183
        if not self.handle:
            raise UpdateError("无法取得升级锁。")
        if already_exists:
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise UpdateError("另一个升级任务正在进行，请稍后重试。")
        return self

    def __exit__(self, *args):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def update_running() -> bool:
    if os.name != "nt":
        return False
    api = kernel()
    handle = api.OpenMutexW(0x00100000, False, lock_name())
    if handle:
        api.CloseHandle(handle)
        return True
    # 访问被拒绝也不应绕过正在进行的升级。
    return ctypes.get_last_error() == 5


class ProcessWaiter:
    def __init__(self, pids):
        self.api = kernel()
        self.handles = []
        try:
            for pid in pids:
                handle = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
                if not handle:
                    raise UpdateError("无法监测应用退出，升级已停止。")
                self.handles.append(handle)
        except Exception:
            self.close()
            raise

    def wait(self, timeout_ms=60_000):
        handles = (wintypes.HANDLE * len(self.handles))(*self.handles)
        result = self.api.WaitForMultipleObjects(len(self.handles), handles, True, timeout_ms)
        if result != 0:
            raise UpdateError("应用尚未完全退出，升级已停止。请退出后重新安装。")

    def close(self):
        for handle in self.handles:
            self.api.CloseHandle(handle)
        self.handles.clear()


def exit_process_ids() -> list[int]:
    """PyInstaller 可能有同路径的父引导进程，也必须等待其释放文件。"""
    pids = [os.getpid()]
    if os.name != "nt":
        return pids
    api = kernel()
    parent = api.OpenProcess(0x1000, False, os.getppid())
    if not parent:
        return pids
    try:
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if api.QueryFullProcessImageNameW(parent, 0, image, ctypes.byref(size)):
            if Path(image.value).resolve() == Path(sys.executable).resolve():
                pids.append(os.getppid())
    finally:
        api.CloseHandle(parent)
    return pids


def notify(message: str):
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "Study Agent 更新", 0x40)
