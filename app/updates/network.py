"""有界 GitHub HTTPS 下载；错误不包含临时 URL 或响应正文。"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from .models import UpdateCancelled, UpdateError

DOWNLOAD_HOSTS = frozenset({
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
})


def validate_url(url: str, *, api: bool = False) -> None:
    try:
        parts = urllib.parse.urlsplit(url)
        allowed = {"api.github.com"} if api else DOWNLOAD_HOSTS
        valid = (parts.scheme == "https" and parts.hostname in allowed
                 and parts.port in (None, 443) and not parts.username
                 and not parts.password and not parts.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise UpdateError("更新下载地址不受信任，已停止下载。")


class GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubTransport:
    def __init__(self):
        self._opener = urllib.request.build_opener(GitHubRedirectHandler())

    def open(self, url: str, timeout: int):
        validate_url(url, api=url.startswith("https://api.github.com/"))
        request = urllib.request.Request(url, headers={
            "User-Agent": "StudyAgent-Updater", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            return self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code in (403, 429):
                raise UpdateError("GitHub 请求受限，请稍后重新检查更新。") from None
            if error.code == 404:
                raise UpdateError("暂时找不到正式版本或安装包，请稍后重试。") from None
            raise UpdateError("更新服务器暂不可用，请稍后重试。") from None
        except (OSError, urllib.error.URLError):
            raise UpdateError("无法连接 GitHub，请检查网络后重试。") from None


def read_chunks(response, cancel, *, limit: int):
    count = 0
    while True:
        if cancel():
            raise UpdateCancelled("下载已取消。")
        try:
            block = response.read(64 * 1024)
        except OSError:
            raise UpdateError("下载中断或超时，请检查网络后重新下载。") from None
        if not block:
            break
        count += len(block)
        if count > limit:
            raise UpdateError("下载文件大小异常，请重新检查更新。")
        yield block
