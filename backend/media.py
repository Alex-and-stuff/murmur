"""YouTube audio fetching for local testing."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def is_allowed_media_url(url: str) -> bool:
    """Only YouTube links are accepted; this must never become an open URL fetcher (SSRF risk)."""

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in ALLOWED_YOUTUBE_HOSTS


class MediaFetchError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_GATEWAY):
        super().__init__(message)
        self.status = status


class MediaFetcher(Protocol):
    def fetch(self, url: str) -> dict[str, object]: ...


class YtDlpFetcher:
    """Downloads audio-only from an allow-listed YouTube URL, cached on disk by video id."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, url: str) -> dict[str, object]:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True}) as probe:
                info = probe.extract_info(url, download=False)
        except Exception as exc:
            raise MediaFetchError(f"無法讀取這個 YouTube 連結：{exc}") from exc

        video_id = info.get("id") if isinstance(info, dict) else None
        if not video_id:
            raise MediaFetchError("無法辨識 YouTube 影片 ID")

        existing = sorted(self.cache_dir.glob(f"{video_id}.*"))
        if not existing:
            options = {
                "quiet": True,
                "noplaylist": True,
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": str(self.cache_dir / f"{video_id}.%(ext)s"),
            }
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    downloader.download([url])
            except Exception as exc:
                raise MediaFetchError(f"下載 YouTube 音訊失敗：{exc}") from exc
            existing = sorted(self.cache_dir.glob(f"{video_id}.*"))
            if not existing:
                raise MediaFetchError("下載完成但找不到音訊檔案", status=HTTPStatus.INTERNAL_SERVER_ERROR)

        return {"url": f"/uploads/{existing[0].name}", "title": str(info.get("title") or video_id)}
