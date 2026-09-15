import json
import threading
import unittest
import urllib.error
import urllib.request
from array import array

from backend.asr import BackendState
from backend.config import SAMPLE_RATE
from backend.http_app import create_server
from backend.media import MediaFetchError



class ServerSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = BackendState()
        cls.state.load("fixture", "unused")
        cls.server = create_server("127.0.0.1", 0, cls.state)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get_json(self, path):
        with urllib.request.urlopen(self.base_url + path, timeout=2) as response:
            return response.status, json.load(response)

    def test_health_and_static_site(self):
        status, payload = self.get_json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["summary"]["status"], "ready")
        self.assertEqual(payload["sample_rate"], SAMPLE_RATE)
        with urllib.request.urlopen(self.base_url + "/", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Murmur", response.read())

    def test_float32_chunk_is_transcribed(self):
        pcm = array("f", [0.0]) * SAMPLE_RATE
        request = urllib.request.Request(
            self.base_url + "/api/transcribe",
            data=pcm.tobytes(),
            method="POST",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Audio-Start": "3.25",
                "X-Audio-End": "4.25",
                "X-Language": "Chinese",
            },
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["text"], "測試音訊 1.0 秒")
        self.assertEqual(payload["start"], 3.25)
        self.assertEqual(payload["end"], 4.25)
        self.assertEqual(payload["context_samples"], SAMPLE_RATE)

    def test_short_chunk_is_rejected(self):
        request = urllib.request.Request(
            self.base_url + "/api/transcribe",
            data=b"\0" * 16,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)

    def test_transcript_is_summarized(self):
        request = urllib.request.Request(
            self.base_url + "/api/summarize",
            data=json.dumps(
                {"segments": [{"start": 1.0, "end": 3.0, "text": "今天確認摘要功能。"}]}
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["summary"], "這是一份測試逐字稿摘要。")
        self.assertEqual(payload["key_points"], ["摘要 API 已收到逐字稿"])
        self.assertEqual(payload["action_items"], [])
        self.assertEqual(payload["mode"], "full")
        self.assertEqual(payload["input_segments"], 1)
        self.assertGreater(payload["input_chars"], 0)

    def test_previous_summary_enables_incremental_update(self):
        request = urllib.request.Request(
            self.base_url + "/api/summarize",
            data=json.dumps(
                {
                    "segments": [{"start": 3.0, "end": 5.0, "text": "接著確認下一步。"}],
                    "previous_summary": {
                        "summary": "先前摘要",
                        "key_points": ["已完成第一段"],
                        "decisions": [],
                        "action_items": [],
                    },
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["mode"], "incremental")
        self.assertEqual(payload["summary"], "這是一份測試逐字稿摘要。")

    def test_invalid_previous_summary_is_rejected(self):
        request = urllib.request.Request(
            self.base_url + "/api/summarize",
            data=json.dumps(
                {
                    "segments": [{"start": 3.0, "end": 5.0, "text": "新的逐字稿。"}],
                    "previous_summary": {"summary": ""},
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)

    def test_empty_transcript_is_rejected(self):
        request = urllib.request.Request(
            self.base_url + "/api/summarize",
            data=json.dumps({"segments": []}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)


class FakeMediaFetcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.result


class FetchMediaTest(unittest.TestCase):
    def _make_server(self, fetcher):
        state = BackendState()
        state.load("fixture", "unused")
        server = create_server("127.0.0.1", 0, state, media_fetcher=fetcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=2))
        return f"http://127.0.0.1:{server.server_port}"

    def _post_json(self, base_url, path, payload):
        request = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_rejects_non_youtube_url(self):
        base_url = self._make_server(FakeMediaFetcher())
        status, payload = self._post_json(base_url, "/api/fetch-media", {"url": "https://example.com/video"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unsupported_url")

    def test_rejects_malformed_body(self):
        base_url = self._make_server(FakeMediaFetcher())
        request = urllib.request.Request(
            base_url + "/api/fetch-media",
            data=b"not json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)

    def test_fetches_youtube_audio_via_stubbed_downloader(self):
        fetcher = FakeMediaFetcher(result={"url": "/uploads/abc123.m4a", "title": "Stub Video"})
        base_url = self._make_server(fetcher)
        status, payload = self._post_json(base_url, "/api/fetch-media", {"url": "https://youtu.be/abc123"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["url"], "/uploads/abc123.m4a")
        self.assertEqual(payload["title"], "Stub Video")
        self.assertEqual(fetcher.calls, ["https://youtu.be/abc123"])

    def test_fetch_error_is_reported(self):
        fetcher = FakeMediaFetcher(error=MediaFetchError("下載失敗", status=502))
        base_url = self._make_server(fetcher)
        status, payload = self._post_json(base_url, "/api/fetch-media", {"url": "https://youtu.be/abc123"})
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "fetch_failed")




if __name__ == "__main__":
    unittest.main()
