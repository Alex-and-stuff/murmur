"""Simulates a multi-hour meeting to prove the online context stays bounded."""

import random
import unittest

from backend.llm import ScriptedChatLLM
from backend.meeting.config import MeetingSummaryConfig
from backend.meeting.engine import MeetingEngine
from backend.meeting.store import MeetingStore
from tests.support import state_update

TOPICS = ["控制架構", "感測佈點", "維運責任", "現場部署", "測試排程", "資安審查"]


class ModelSimulator:
    """Stands in for the 8B: switches topic occasionally, otherwise continues."""

    def __init__(self, seed=7):
        self.random = random.Random(seed)
        self.sections: list[str] = []
        self.current: str | None = None
        self.prompts: list[str] = []

    def __call__(self, messages):
        prompt = messages[1]["content"]
        self.prompts.append(prompt)
        roll = self.random.random()
        archived = [line for line in prompt.splitlines() if line.startswith(("1.", "2.", "3."))]
        if self.current is None or roll < 0.25:
            title = self.random.choice(TOPICS)
            self.current = title
            return state_update("new_section", title=title, descriptor=f"{title}的重點")
        if roll < 0.32 and archived:
            section_id = archived[-1].split("[", 1)[1].split("]", 1)[0]
            return state_update("return_to_section", target_section_id=section_id)
        return state_update(
            "continue",
            title=self.current,
            summary=f"{self.current}的討論持續推進，涵蓋介面、責任與驗證方式。" * self.random.randint(1, 4),
            key_points=[f"{self.current} 重點 {index}：這一項描述了一個獨立論點" for index in range(6)],
        )


class LongMeetingTest(unittest.TestCase):
    def test_a_multi_hour_meeting_never_grows_the_input_context(self):
        simulator = ModelSimulator()
        store = MeetingStore()
        self.addCleanup(store.close)
        config = MeetingSummaryConfig()
        engine = MeetingEngine(store, config, llm=ScriptedChatLLM([simulator] * 5_000))
        engine.create_meeting("long")

        totals: list[int] = []
        # ~3 hours of speech: 360 segments of roughly 30 seconds each.
        for index in range(360):
            engine.add_segments(
                "long",
                [
                    {
                        "text": f"第{index}段：我們接著討論後續的作法與需要確認的事項。" * 3,
                        "start": index * 30,
                        "end": index * 30 + 28,
                    }
                ],
            )
            result = engine.maybe_rollout("long")
            if result.status == "applied":
                totals.append(int(result.metrics["total_input"]))
            self.assertNotEqual(result.status, "failed", result.reason)

        for result in engine.flush("long"):
            self.assertNotEqual(result.status, "failed", result.reason)

        state = store.load_state("long")
        self.assertGreater(len(totals), 20)
        self.assertTrue(all(total <= config.max_input_tokens for total in totals))
        # No linear growth: the last quarter is no larger than the first.
        quarter = len(totals) // 4
        self.assertLessEqual(max(totals[-quarter:]), max(totals[:quarter]) + config.max_pending_asr_tokens)
        self.assertEqual(state.pending_segments, [])

        # Every raw segment is retained and traceable to exactly one section.
        transcript = store.transcript("long")
        self.assertEqual(len(transcript), 360)
        consumed = [
            segment_id
            for section in store.sections_in_order("long")
            for segment_id in section.source_segment_ids
        ]
        self.assertEqual(sorted(consumed), sorted(segment.segment_id for segment in transcript))
        self.assertEqual(len(consumed), len(set(consumed)))


if __name__ == "__main__":
    unittest.main()
