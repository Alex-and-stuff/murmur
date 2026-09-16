"""HTTP routing for the Murmur UI and inference endpoints."""

from __future__ import annotations

import json
import re
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
    MAX_SEGMENTS_BODY_BYTES,
    SAMPLE_RATE,
    STATIC_DIR,
    UPLOADS_DIR,
)
from backend.media import MediaFetchError, MediaFetcher, YtDlpFetcher, is_allowed_media_url
from backend.meeting.service import MeetingService

MEETING_PATH = re.compile(r"^/api/meetings/([A-Za-z0-9_-]{1,64})(/[a-z-]+)?$")
STREAM_PATH = re.compile(r"^/api/streams/([a-f0-9]{32})/(chunk|finish|abort)$")


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
    def meetings(self) -> MeetingService:
        return self.server.meeting_service  # type: ignore[attr-defined]

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

    def _read_json(self, max_bytes: int) -> dict[str, object] | None:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_json"})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > max_bytes:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_body"})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return None
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return None
        return payload

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            payload = self.state.snapshot()
            payload["summary"] = self.meetings.snapshot()
            self._json(HTTPStatus.OK, payload)
            return
        match = MEETING_PATH.match(path)
        if match:
            self._handle_meeting_get(match.group(1), (match.group(2) or "").lstrip("/"))
            return
        super().do_GET()

    def _handle_meeting_get(self, meeting_id: str, action: str) -> None:
        if not self.meetings.store.meeting_exists(meeting_id):
            self._json(HTTPStatus.NOT_FOUND, {"error": "unknown_meeting"})
            return
        if action in ("", "state"):
            self._json(HTTPStatus.OK, self.meetings.state(meeting_id))
            return
        if action == "transcript":
            self._json(HTTPStatus.OK, self.meetings.transcript(meeting_id))
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    # -- POST --------------------------------------------------------------

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/streams":
            self._handle_stream_start()
            return
        stream_match = STREAM_PATH.match(path)
        if stream_match:
            self._handle_stream_audio(stream_match.group(1), stream_match.group(2))
            return
        if path == "/api/fetch-media":
            self._handle_fetch_media()
            return
        if path == "/api/meetings":
            self._json(HTTPStatus.OK, self.meetings.create_meeting())
            return
        match = MEETING_PATH.match(path)
        if match:
            self._handle_meeting_post(match.group(1), (match.group(2) or "").lstrip("/"))
            return
        if path != "/api/transcribe":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._handle_transcribe()

    def _streaming_backend(self):
        backend = self.state.backend
        if self.state.snapshot()["status"] != "ready" or not getattr(backend, "streaming", False):
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "streaming_not_ready"})
            return None
        return backend

    def _read_pcm(self, *, allow_empty: bool = False) -> np.ndarray | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/octet-stream":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_float32_pcm"})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        minimum = 0 if allow_empty else SAMPLE_RATE * 4
        if length < minimum or length > MAX_AUDIO_BYTES or length % 4:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_audio_length"})
            return None
        samples = np.frombuffer(self.rfile.read(length), dtype="<f4").copy()
        if not np.isfinite(samples).all():
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_audio", "detail": "audio contains non-finite samples"})
            return None
        return np.clip(samples, -1.0, 1.0)

    def _stream_response(self, text: str, language: str, started_at: float) -> None:
        self._json(
            HTTPStatus.OK,
            {
                "text": text,
                "language": language,
                "inference_seconds": round(time.monotonic() - started_at, 3),
            },
        )

    def _handle_stream_start(self) -> None:
        backend = self._streaming_backend()
        if backend is None:
            return
        language = self.headers.get("X-Language", "Chinese")[:32]
        try:
            self._json(HTTPStatus.OK, {"stream_id": backend.start_stream(language)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "stream_start_failed", "detail": f"{type(exc).__name__}: {exc}"})

    def _handle_stream_audio(self, stream_id: str, action: str) -> None:
        backend = self._streaming_backend()
        if backend is None:
            return
        if action == "abort":
            backend.abort_stream(stream_id)
            self._json(HTTPStatus.OK, {"aborted": True})
            return
        samples = self._read_pcm(allow_empty=action == "finish")
        if samples is None:
            return
        began = time.monotonic()
        try:
            if action == "chunk":
                text, language = backend.push_stream(stream_id, samples)
            else:
                text, language = backend.finish_stream(stream_id, samples)
            self._stream_response(text, language, began)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "unknown_stream"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "stream_inference_failed", "detail": f"{type(exc).__name__}: {exc}"})

    def _handle_meeting_post(self, meeting_id: str, action: str) -> None:
        if not self.meetings.store.meeting_exists(meeting_id):
            self._json(HTTPStatus.NOT_FOUND, {"error": "unknown_meeting"})
            return
        try:
            if action == "segments":
                payload = self._read_json(MAX_SEGMENTS_BODY_BYTES)
                if payload is None:
                    return
                segments = payload.get("segments")
                if not isinstance(segments, list) or not segments:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_segments"})
                    return
                if any(not isinstance(item, dict) for item in segments):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_segments"})
                    return
                self._json(HTTPStatus.OK, self.meetings.add_segments(meeting_id, segments))
                return
            if action == "auto":
                payload = self._read_json(1024)
                if payload is None:
                    return
                enabled = bool(payload.get("enabled", True))
                self._json(HTTPStatus.OK, self.meetings.set_auto_rollout(meeting_id, enabled))
                return
            if action == "rollout":
                self._json(HTTPStatus.OK, self.meetings.rollout(meeting_id))
                return
            if action == "finalize":
                self._json(HTTPStatus.OK, self.meetings.finalize(meeting_id))
                return
        except Exception as exc:
            print(
                f"[{self.log_date_time_string()}] /api/meetings/{meeting_id}/{action} failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "meeting_request_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _handle_transcribe(self) -> None:
        snapshot = self.state.snapshot()
        if snapshot["status"] != "ready" or self.state.backend is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "model_not_ready", **snapshot})
            return

        try:
            start = max(0.0, float(self.headers.get("X-Audio-Start", "0")))
            end = max(start, float(self.headers.get("X-Audio-End", "0")))
            language = self.headers.get("X-Language", "Chinese")[:32]
            samples = self._read_pcm()
            if samples is None:
                return
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

    def _handle_fetch_media(self) -> None:
        payload = self._read_json(MAX_FETCH_BODY_BYTES)
        if payload is None:
            return
        url = str(payload.get("url") or "").strip()
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
    meeting_service: MeetingService | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), MurmurHandler)
    server.backend_state = state  # type: ignore[attr-defined]
    server.media_fetcher = media_fetcher or YtDlpFetcher(UPLOADS_DIR)  # type: ignore[attr-defined]
    service = meeting_service or MeetingService()
    service.start()
    server.meeting_service = service  # type: ignore[attr-defined]
    return server
