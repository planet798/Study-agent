"""更新使用隔离缓存和假 GitHub，不连接真实账户或网络。"""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.app_update_service import AppUpdateService
from app.updates.models import LATEST_URL, REPOSITORY, UpdateCancelled, UpdateError
from app.updates.network import GitHubRedirectHandler, GitHubTransport, validate_url


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)


class Transport:
    def __init__(self, data):
        self.data, self.calls = data, []

    def open(self, url, timeout):
        self.calls.append((url, timeout))
        value = self.data[url]
        if isinstance(value, Exception):
            raise value
        return Response(value)


def release_fixture(version="0.2.10", payload=b"test-installer", *, checksum_override=None):
    filename = f"StudyAgent-Setup-{version}-x64.exe"
    prefix = f"https://github.com/{REPOSITORY}/releases/download/v{version}/"
    sha = hashlib.sha256(payload).hexdigest()
    checksums = checksum_override or ("\ufeff" + sha + "  " + filename + "\r\n").encode()
    release = {"id": 3, "draft": False, "prerelease": False, "tag_name": "v" + version,
               "body": "更新说明 <script>仅纯文本</script>", "published_at": "2026-10-05T10:00:00Z",
               "assets": [
                   {"id": 4, "name": filename, "size": len(payload), "state": "uploaded",
                    "digest": "sha256:" + sha, "browser_download_url": prefix + filename},
                   {"id": 5, "name": "SHA256SUMS.txt", "size": len(checksums), "state": "uploaded",
                    "digest": "sha256:" + hashlib.sha256(checksums).hexdigest(),
                    "browser_download_url": prefix + "SHA256SUMS.txt"},
               ]}
    data = {LATEST_URL: json.dumps(release).encode(), prefix + filename: payload,
            prefix + "SHA256SUMS.txt": checksums}
    return release, data


@pytest.fixture
def update_service(tmp_path):
    release, data = release_fixture()
    transport = Transport(data)
    return AppUpdateService(current_version="0.2.9", cache_root=tmp_path / "updates", transport=transport)


def test_numeric_version_and_utf8_bom_crlf_checksum(update_service):
    release = update_service.check()
    assert release.version == "0.2.10"
    assert release.sha256 == hashlib.sha256(b"test-installer").hexdigest()
    assert all(timeout == 15 for _, timeout in update_service.transport.calls)


@pytest.mark.parametrize("version", ["0.2.9", "0.2.8"])
def test_current_or_older_never_downloads_assets(tmp_path, version):
    _, data = release_fixture(version)
    transport = Transport(data)
    service = AppUpdateService(current_version="0.2.9", cache_root=tmp_path, transport=transport)
    assert service.check() is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(draft=True), lambda r: r.update(prerelease=True),
])
def test_ignores_unpublished_versions(tmp_path, mutation):
    release, data = release_fixture()
    mutation(release)
    data[LATEST_URL] = json.dumps(release).encode()
    service = AppUpdateService(cache_root=tmp_path, transport=Transport(data))
    assert service.check() is None


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(tag_name="vnext"), lambda r: r.update(assets=[]),
    lambda r: r["assets"][0].update(browser_download_url="https://evil.example/setup.exe"),
    lambda r: r["assets"][0].update(digest="sha256:" + "0" * 64),
    lambda r: r["assets"].append(r["assets"][0]),
    lambda r: r["assets"][1].update(size=999),
    lambda r: r["assets"][0].update(id=True),
])
def test_invalid_release_is_rejected(tmp_path, mutation):
    release, data = release_fixture()
    mutation(release)
    data[LATEST_URL] = json.dumps(release).encode()
    with pytest.raises(UpdateError):
        AppUpdateService(cache_root=tmp_path, transport=Transport(data)).check()


def test_download_progress_and_complete_cache_reverified(update_service):
    release = update_service.check()
    progress, phases = [], []
    downloaded = update_service.download(release, progress=lambda a, b: progress.append((a, b)),
                                         verifying=lambda: phases.append(True))
    assert downloaded.installer.read_bytes() == b"test-installer"
    assert progress[-1] == (release.size, release.size)
    assert phases and update_service.transport.calls[-1][1] == 30
    calls = len(update_service.transport.calls)
    assert update_service.download(release) == downloaded
    assert len(update_service.transport.calls) == calls
    downloaded.installer.write_bytes(b"X" * release.size)
    assert update_service.download(release).installer.read_bytes() == b"test-installer"
    assert len(update_service.transport.calls) == calls + 1


@pytest.mark.parametrize("payload", [b"truncated", b"x" * len(b"test-installer"), b"x" * 100])
def test_corruption_does_not_leave_installable_or_partial_file(update_service, payload):
    release = update_service.check()
    update_service.transport.data[release.url] = payload
    with pytest.raises(UpdateError):
        update_service.download(release)
    assert not list(update_service.cache_root.rglob("*.exe"))
    assert not list(update_service.cache_root.rglob("*.part"))


def test_cancellation_removes_partial(update_service):
    release = update_service.check()
    cancelled = False
    def progress(a, b):
        nonlocal cancelled
        cancelled = True
    with pytest.raises(UpdateCancelled):
        update_service.download(release, cancel=lambda: cancelled, progress=progress)
    assert not list(update_service.cache_root.rglob("*.part"))


def test_disk_full_and_linked_cache_are_rejected(update_service, monkeypatch, tmp_path):
    release = update_service.check()
    monkeypatch.setattr("app.services.app_update_service.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(UpdateError, match="空间不足"):
        update_service.download(release)
    link = tmp_path / "link"
    try:
        link.symlink_to(update_service.cache_root, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    update_service.cache_root = link
    with pytest.raises(UpdateError, match="链接"):
        update_service.download(release)


@pytest.mark.parametrize("url", ["http://github.com/x", "https://github.com.evil.example/x",
                                     "https://user:pass@github.com/x", "https://github.com:444/x",
                                     "https://evil.example/x", "file:///tmp/setup.exe"])
def test_untrusted_urls_rejected(url):
    with pytest.raises(UpdateError):
        validate_url(url)
    with pytest.raises(UpdateError):
        GitHubRedirectHandler().redirect_request(None, None, 302, "", {}, url)


def test_official_download_redirect_accepted():
    import urllib.request
    req = urllib.request.Request("https://github.com/test")
    target = "https://release-assets.githubusercontent.com/github-production-release-asset/x?signature=test"
    redirected = GitHubRedirectHandler().redirect_request(req, None, 302, "", {}, target)
    assert redirected.full_url == target


@pytest.mark.parametrize("code", [403, 429, 404, 500])
def test_http_failures_do_not_expose_response_or_signed_url(monkeypatch, code):
    import urllib.error
    transport = GitHubTransport()
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://github.com/?private-signature", code, "private-token", {}, None)
    monkeypatch.setattr(transport._opener, "open", fail)
    with pytest.raises(UpdateError) as caught:
        transport.open(LATEST_URL, 15)
    assert "private" not in str(caught.value)
