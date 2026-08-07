"""Notification-only update checks against GitHub Releases."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QThread, Signal

from settings import load_settings, save_settings
from version_info import GITHUB_REPO, load_build_info

USER_AGENT = "MacroReview-UpdateCheck/1.0"
CHECK_INTERVAL = timedelta(hours=24)


@dataclass
class UpdateInfo:
    available: bool
    tag_name: str
    html_url: str
    body: str
    manual: bool
    message: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    cleaned = text.strip().lstrip("vV")
    cleaned = cleaned.split("+", 1)[0].split("-", 1)[0]
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def preview_commit_differs(target: str, body: str, local: str) -> bool:
    """Compare the rolling preview commit using API target or release notes."""
    if local in {"", "unknown", "dev"}:
        return False
    remote = ""
    target = target.strip().lower()
    if re.fullmatch(r"[0-9a-f]{7,40}", target):
        remote = target
    if not remote:
        match = re.search(r"Commit:\s*`?([0-9a-f]{7,40})", body, re.IGNORECASE)
        if match:
            remote = match.group(1).lower()
    if not remote:
        return False
    return remote[:7] != local.lower()[:7]


def _api_url(channel: str, repo: str) -> str:
    if channel == "preview":
        return f"https://api.github.com/repos/{repo}/releases/tags/preview"
    return f"https://api.github.com/repos/{repo}/releases/latest"


def _fetch_release(channel: str, repo: str) -> dict:
    req = urllib.request.Request(
        _api_url(channel, repo),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub response")
    return payload


def _mark_checked() -> None:
    settings = load_settings()
    settings.last_update_check = datetime.now(timezone.utc).isoformat()
    try:
        save_settings(settings)
    except OSError:
        pass


def should_check(*, manual: bool, force: bool = False) -> bool:
    if manual or force:
        return True
    settings = load_settings()
    if not settings.check_updates:
        return False
    raw = (settings.last_update_check or "").strip()
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= CHECK_INTERVAL


def check_for_update(*, manual: bool = False, force: bool = False) -> UpdateInfo:
    settings = load_settings()
    if not should_check(manual=manual, force=force):
        return UpdateInfo(
            available=False,
            tag_name="",
            html_url="",
            body="",
            manual=manual,
            message="Skipped (checked recently)",
        )

    channel = (settings.update_channel or "stable").strip().lower()
    if channel not in {"stable", "preview"}:
        channel = "stable"
    info = load_build_info()
    repo = info.repo or GITHUB_REPO
    releases_url = f"https://github.com/{repo}/releases"

    try:
        release = _fetch_release(channel, repo)
    except urllib.error.HTTPError as exc:
        _mark_checked()
        if exc.code == 404:
            return UpdateInfo(
                available=False,
                tag_name="",
                html_url=releases_url,
                body="",
                manual=manual,
                message="No release published yet",
            )
        raise
    except Exception:
        _mark_checked()
        raise
    else:
        _mark_checked()

    tag = str(release.get("tag_name") or "")
    html_url = str(release.get("html_url") or releases_url)
    body = str(release.get("body") or "")
    remote_version = tag
    preview_changed = False
    if channel == "preview":
        name = str(release.get("name") or "")
        match = re.search(r"v?\d+\.\d+\.\d+", name) or re.search(r"v?\d+\.\d+\.\d+", body)
        if match:
            remote_version = match.group(0)
        target = str(release.get("target_commitish") or "")
        preview_changed = preview_commit_differs(target, body, info.commit)

    available = is_newer(remote_version, info.version) or preview_changed
    return UpdateInfo(
        available=available,
        tag_name=tag or remote_version,
        html_url=html_url,
        body=body,
        manual=manual,
        message="Update available" if available else "Up to date",
    )


class UpdateCheckWorker(QThread):
    finished_info = Signal(object)
    failed = Signal(str, bool)

    def __init__(self, *, manual: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.manual = manual

    def run(self) -> None:  # noqa: N802
        try:
            info = check_for_update(manual=self.manual)
            self.finished_info.emit(info)
        except Exception as exc:  # noqa: BLE001 — surface to UI
            self.failed.emit(f"Update check failed: {exc}", self.manual)
