"""HTTP routing for the Murmur UI and inference endpoints."""

from __future__ import annotations

import json
import sys
import time
import traceback
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import numpy as np

from backend.asr import BackendState
from backend.config import (
    MAX_AUDIO_BYTES,
    MAX_FETCH_BODY_BYTES,
    MAX_SUMMARY_BODY_BYTES,
    MAX_SUMMARY_TRANSCRIPT_CHARS,
    SAMPLE_RATE,
    STATIC_DIR,
    UPLOADS_DIR,
)
from backend.media import MediaFetchError, MediaFetcher, YtDlpFetcher, is_allowed_media_url
from backend.summary import SummaryState, normalize_summary


class MurmurHandler(SimpleHTTPRequestHandler):
    server_version = "Murmur/0.1"
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
        ".mp3": "audio/mpeg",
    }

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    @property
    def state(self) -> BackendState:
        return self.server.backend_state  # type: ignore[attr-defined]

    @property
    def media_fetcher(self) -> MediaFetcher:
        return self.server.media_fetcher  # type: ignore[attr-defined]

    @property
    def summary_state(self) -> SummaryState:
        return self.server.summary_state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/health":
            payload = self.state.snapshot()
            payload["summary"] = self.summary_state.snapshot()
            self._json(HTTPStatus.OK, payload)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/fetch-media":
            self._handle_fetch_media()
            return
        if path == "/api/summarize":
            self._handle_summarize()
            return
        if path != "/api/transcribe":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        snapshot = self.state.snapshot()
        if snapshot["status"] != "ready" or self.state.backend is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "model_not_ready", **snapshot})
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/octet-stream":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_float32_pcm"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < SAMPLE_RATE * 4 or length > MAX_AUDIO_BYTES or length % 4:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_audio_length"})
            return

        try:
            start = max(0.0, float(self.headers.get("X-Audio-Start", "0")))
            end = max(start, float(self.headers.get("X-Audio-End", "0")))
            language = self.headers.get("X-Language", "Chinese")[:32]
            samples = np.frombuffer(self.rfile.read(length), dtype="<f4").copy()
            if not np.isfinite(samples).all():
                raise ValueError("audio contains non-finite samples")
            samples = np.clip(samples, -1.0, 1.0)
            began = time.monotonic()
            text, detected_language = self.state.backend.transcribe(samples, language)
            self._json(
                HTTPStatus.OK,
                {
                    "text": text,
                    "language": detected_language,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "audio_seconds": round(len(samples) / SAMPLE_RATE, 3),
                    "context_samples": len(samples),
                    "inference_seconds": round(time.monotonic() - began, 3),
                },
            )
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "inference_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )

    def _handle_summarize(self) -> None:
        snapshot = self.summary_state.snapshot()
        if snapshot["status"] != "ready" or self.summary_state.backend is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "summary_model_not_ready", **snapshot})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_SUMMARY_BODY_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            segments = payload["segments"]
            if not isinstance(segments, list) or not segments:
                raise ValueError("segments must be a non-empty list")
            lines = []
            for item in segments:
                if not isinstance(item, dict):
                    raise ValueError("invalid segment")
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                start = max(0.0, float(item.get("start", 0)))
                end = max(start, float(item.get("end", start)))
                lines.append(f"[{start:.1f}s–{end:.1f}s] {text}")
            transcript = "\n".join(lines)
            if not transcript:
                raise ValueError("transcript is empty")
            if len(transcript) > MAX_SUMMARY_TRANSCRIPT_CHARS:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "transcript_too_long", "max_chars": MAX_SUMMARY_TRANSCRIPT_CHARS},
                )
                return
            raw_previous = payload.get("previous_summary")
            previous_summary = normalize_summary(raw_previous) if raw_previous is not None else None
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_transcript", "detail": str(exc)})
            return

        try:
            began = time.monotonic()
            result = self.summary_state.backend.summarize(transcript, previous_summary)
            result["inference_seconds"] = round(time.monotonic() - began, 3)
            result["mode"] = "incremental" if previous_summary else "full"
            result["input_chars"] = len(transcript) + (
                len(json.dumps(previous_summary, ensure_ascii=False)) if previous_summary else 0
            )
            result["input_segments"] = len(lines)
            self._json(HTTPStatus.OK, result)
        except Exception as exc:
            print(
                f"[{self.log_date_time_string()}] /api/summarize failed "
                f"(transcript_chars={len(transcript)}, has_previous_summary={previous_summary is not None}): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "summary_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )

    def _handle_fetch_media(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_FETCH_BODY_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_body"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            url = str(payload["url"]).strip()
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        if not url or not is_allowed_media_url(url):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "unsupported_url", "detail": "只接受 YouTube 連結"})
            return

        try:
            result = self.media_fetcher.fetch(url)
        except MediaFetchError as exc:
            self._json(exc.status, {"error": "fetch_failed", "detail": str(exc)})
            return
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "fetch_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )
            return

        self._json(HTTPStatus.OK, result)


def create_server(
    host: str,
    port: int,
    state: BackendState,
    media_fetcher: MediaFetcher | None = None,
    summary_state: SummaryState | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), MurmurHandler)
    server.backend_state = state  # type: ignore[attr-defined]
    server.media_fetcher = media_fetcher or YtDlpFetcher(UPLOADS_DIR)  # type: ignore[attr-defined]
    if summary_state is None:
        summary_state = SummaryState()
        summary_state.load("fixture", "unused")
    server.summary_state = summary_state  # type: ignore[attr-defined]
    return server
