import json
import threading
import unittest
import urllib.error
import urllib.request
from array import array

from backend.asr import BackendState
from backend.config import SAMPLE_RATE
from backend.http_app import create_server
from backend.llm import LLMState, ScriptedChatLLM
from backend.media import MediaFetchError
from backend.meeting.service import MeetingService
from tests.support import state_update


def make_service(test, responses=()):
    llm_state = LLMState("summary")
    llm_state.adopt(ScriptedChatLLM(list(responses)))
    # A long ticker interval keeps rollouts under the test's control.
    service = MeetingService(llm_state=llm_state, scheduler_interval=3_600)
    test.addCleanup(service.stop)
    return service


class ServerSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = BackendState()
        cls.state.load("fixture", "unused")
        cls.llm_state = LLMState("summary")
        cls.llm_state.adopt(ScriptedChatLLM())
        cls.service = MeetingService(llm_state=cls.llm_state, scheduler_interval=3_600)
        cls.server = create_server("127.0.0.1", 0, cls.state, meeting_service=cls.service)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
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


class MeetingApiTest(unittest.TestCase):
    def serve(self, responses=()):
        state = BackendState()
        state.load("fixture", "unused")
        self.service = make_service(self, responses)
        server = create_server("127.0.0.1", 0, state, meeting_service=self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: thread.join(timeout=2))
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def post(self, base_url, path, payload=None):
        request = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def get(self, base_url, path):
        with urllib.request.urlopen(base_url + path, timeout=5) as response:
            return response.status, json.load(response)

    def test_segments_are_stored_and_rolled_up_into_a_section(self):
        base_url = self.serve([state_update("new_section")])
        _, created = self.post(base_url, "/api/meetings")
        meeting_id = created["meeting_id"]

        status, appended = self.post(
            base_url,
            f"/api/meetings/{meeting_id}/segments",
            {"segments": [{"text": "我們先確認控制架構的權限邊界。", "start": 0, "end": 6}]},
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["segment_id"] for item in appended["accepted"]], ["s1"])
        self.assertEqual(appended["state"]["pending_segment_count"], 1)

        status, rolled = self.post(base_url, f"/api/meetings/{meeting_id}/rollout")
        self.assertEqual(status, 200)
        self.assertEqual(rolled["status"], "applied")
        self.assertEqual(rolled["state"]["current_section"]["title"], "控制架構")
        self.assertEqual(rolled["state"]["pending_segments"], [])
        self.assertLessEqual(rolled["metrics"]["total_input"], rolled["state"]["budget"]["max_input_tokens"])

        status, state = self.get(base_url, f"/api/meetings/{meeting_id}/state")
        self.assertEqual(state["state_version"], 1)
        self.assertEqual(len(state["rollouts"]), 1)

        status, transcript = self.get(base_url, f"/api/meetings/{meeting_id}/transcript")
        self.assertEqual([item["text"] for item in transcript["segments"]], ["我們先確認控制架構的權限邊界。"])

    def test_finalize_flushes_pending_segments_before_building_the_document(self):
        base_url = self.serve([state_update("new_section"), '{"executive_summary":"整場會議聚焦控制架構。","risks":[]}'])
        _, created = self.post(base_url, "/api/meetings")
        meeting_id = created["meeting_id"]
        self.post(
            base_url,
            f"/api/meetings/{meeting_id}/segments",
            {"segments": [{"text": "我們先確認控制架構的權限邊界。", "start": 0, "end": 6}]},
        )

        status, finalized = self.post(base_url, f"/api/meetings/{meeting_id}/finalize")
        self.assertEqual(status, 200)
        self.assertEqual(finalized["flushes"][0]["status"], "applied")
        self.assertEqual(finalized["document"]["executive_summary"], "整場會議聚焦控制架構。")
        self.assertEqual([topic["title"] for topic in finalized["document"]["topics"]], ["控制架構"])
        self.assertEqual(finalized["state"]["pending_segments"], [])

    def test_unknown_meeting_and_empty_segment_lists_are_rejected(self):
        base_url = self.serve()
        status, _ = self.post(base_url, "/api/meetings/deadbeef/segments", {"segments": []})
        self.assertEqual(status, 404)
        _, created = self.post(base_url, "/api/meetings")
        status, payload = self.post(
            base_url, f"/api/meetings/{created['meeting_id']}/segments", {"segments": []}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_segments")


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
        server = create_server(
            "127.0.0.1", 0, state, media_fetcher=fetcher, meeting_service=make_service(self)
        )
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
