import json
import unittest

from backend.llm import ScriptedChatLLM
from backend.meeting.config import MeetingSummaryConfig
from backend.meeting.engine import MeetingEngine
from backend.meeting.store import MeetingStore
from tests.support import state_update


class EngineTestCase(unittest.TestCase):
    def build(self, responses, config=None):
        store = MeetingStore()
        llm = ScriptedChatLLM(responses)
        engine = MeetingEngine(store, config or MeetingSummaryConfig(), llm=llm)
        engine.create_meeting("m1")
        self.addCleanup(store.close)
        return store, engine, llm

    def speak(self, engine, text, start=0.0, end=5.0):
        return engine.add_segments("m1", [{"text": text, "start": start, "end": end}])


class TopicOperationsTest(EngineTestCase):
    def test_continue_updates_the_current_section_without_creating_one(self):
        store, engine, _ = self.build(
            [state_update("new_section"), state_update("continue", summary="補充了驗證排程。")]
        )
        self.speak(engine, "先談控制架構" * 5)
        engine.run_rollout("m1", force=True)
        self.speak(engine, "再補充驗證排程" * 5, 5, 10)
        result = engine.run_rollout("m1", force=True)

        state = store.load_state("m1")
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(store.sections_in_order("m1")), 1)
        self.assertEqual(state.current_section.summary, "補充了驗證排程。")
        self.assertEqual(state.current_section.source_segment_ids, ["s1", "s2"])
        self.assertEqual(state.section_index, [])

    def test_new_section_archives_the_previous_topic_into_the_index(self):
        store, engine, _ = self.build(
            [state_update("new_section"), state_update("new_section", title="部署排程", descriptor="九月現場驗證")]
        )
        self.speak(engine, "先談控制架構" * 5)
        engine.run_rollout("m1", force=True)
        self.speak(engine, "換個主題談部署" * 5, 5, 10)
        engine.run_rollout("m1", force=True)

        state = store.load_state("m1")
        self.assertEqual(state.current_section.title, "部署排程")
        self.assertEqual([entry.title for entry in state.section_index], ["控制架構"])
        self.assertEqual(state.section_index[0].short_descriptor, "MPC 串接 BMS")
        self.assertEqual(state.archived_section_ids, ["sec1"])

    def test_return_to_section_reloads_the_full_archived_section(self):
        store, engine, llm = self.build(
            [
                state_update("new_section", key_points=["MPC 由 BMS 下發", "逾時退回手動"]),
                state_update("new_section", title="部署排程", descriptor="九月現場驗證"),
                state_update("return_to_section", target_section_id="sec1"),
                state_update(
                    "continue",
                    summary="補上 fallback 細節。",
                    key_points=["MPC 由 BMS 下發", "逾時退回手動", "fallback 由人工確認"],
                ),
            ]
        )
        self.speak(engine, "先談控制架構" * 5)
        engine.run_rollout("m1", force=True)
        self.speak(engine, "換個主題談部署" * 5, 5, 10)
        engine.run_rollout("m1", force=True)
        self.speak(engine, "回頭補 fallback" * 5, 10, 15)
        engine.run_rollout("m1", force=True)

        state = store.load_state("m1")
        self.assertEqual(state.current_section.section_id, "sec1")
        self.assertEqual(state.current_section.summary, "補上 fallback 細節。")
        self.assertEqual(state.current_section.source_segment_ids, ["s1", "s3"])
        self.assertEqual([entry.section_id for entry in state.section_index], ["sec2"])
        # The merge prompt carried the archived content, not just its descriptor.
        prompt = llm.calls[-1][1]["content"]
        self.assertIn("逾時退回手動", prompt)
        self.assertIn("回頭補 fallback", prompt)

    def test_unknown_target_section_is_rejected_and_state_is_unchanged(self):
        store, engine, _ = self.build(
            [
                state_update("new_section"),
                state_update("return_to_section", target_section_id="sec99"),
                state_update("return_to_section", target_section_id="sec99"),
            ]
        )
        self.speak(engine, "先談控制架構" * 5)
        engine.run_rollout("m1", force=True)
        before = store.load_state("m1")
        self.speak(engine, "說些別的" * 5, 5, 10)
        result = engine.run_rollout("m1", force=True)

        after = store.load_state("m1")
        self.assertEqual(result.status, "failed")
        self.assertEqual(after.state_version, before.state_version)
        self.assertEqual(after.current_section.to_dict(), before.current_section.to_dict())
        self.assertEqual([segment.segment_id for segment in after.pending_segments], ["s2"])


class FailureHandlingTest(EngineTestCase):
    def test_invalid_json_does_not_overwrite_state_and_keeps_segments_pending(self):
        store, engine, _ = self.build(["這不是 JSON", "還是不是 JSON"])
        self.speak(engine, "先談控制架構" * 5)
        result = engine.run_rollout("m1", force=True)

        state = store.load_state("m1")
        self.assertEqual(result.status, "failed")
        self.assertEqual(state.state_version, 0)
        self.assertIsNone(state.current_section)
        self.assertEqual([segment.segment_id for segment in state.pending_segments], ["s1"])

    def test_a_retry_recovers_after_one_bad_response(self):
        store, engine, _ = self.build([RuntimeError("model timeout"), state_update("new_section")])
        self.speak(engine, "先談控制架構" * 5)
        result = engine.run_rollout("m1", force=True)

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.metrics["retry_count"], 1)
        self.assertEqual(store.load_state("m1").state_version, 1)

    def test_segments_are_only_consumed_after_a_successful_commit(self):
        store, engine, _ = self.build([state_update("new_section")])
        self.speak(engine, "先談控制架構" * 5)
        self.assertEqual(len(store.load_state("m1").pending_segments), 1)
        engine.run_rollout("m1", force=True)
        self.assertEqual(store.load_state("m1").pending_segments, [])
        # Raw ASR survives summarisation.
        self.assertEqual([segment.segment_id for segment in store.transcript("m1")], ["s1"])

    def test_restart_resumes_from_the_last_committed_state(self):
        store, engine, _ = self.build([state_update("new_section")])
        self.speak(engine, "先談控制架構" * 5)
        engine.run_rollout("m1", force=True)
        self.speak(engine, "尚未整理的內容" * 5, 5, 10)

        # A fresh engine over the same store is what a process restart looks like.
        revived = MeetingEngine(store, MeetingSummaryConfig(), llm=ScriptedChatLLM([state_update("continue")]))
        state = store.load_state("m1")
        self.assertEqual(state.state_version, 1)
        self.assertEqual([segment.segment_id for segment in state.pending_segments], ["s2"])
        self.assertEqual(revived.run_rollout("m1", force=True).status, "applied")


class RolloutPolicyTest(EngineTestCase):
    def test_short_pending_text_does_not_trigger_a_rollout(self):
        store, engine, llm = self.build([state_update("new_section")])
        self.speak(engine, "嗯")
        self.assertEqual(engine.maybe_rollout("m1").reason, "policy_not_triggered")
        self.assertEqual(llm.calls, [])

    def test_enough_pending_tokens_triggers_a_rollout(self):
        config = MeetingSummaryConfig(rollout_trigger_tokens=20, rollout_min_tokens=5)
        store, engine, _ = self.build([state_update("new_section")], config)
        self.speak(engine, "這是一段夠長的發言內容" * 4)
        self.assertEqual(engine.maybe_rollout("m1").status, "applied")

    def test_the_interval_trigger_fires_for_a_quiet_meeting(self):
        config = MeetingSummaryConfig(
            rollout_trigger_tokens=10_000, rollout_min_tokens=5, rollout_max_interval_seconds=0
        )
        store, engine, _ = self.build([state_update("new_section")], config)
        self.speak(engine, "只有一小段話而已")
        self.assertEqual(engine.maybe_rollout("m1").status, "applied")

    def test_an_oversized_asr_segment_is_split_rather_than_truncated(self):
        config = MeetingSummaryConfig(max_pending_asr_tokens=60)
        store, engine, _ = self.build([], config)
        text = "。".join(f"這是第{index}句話，內容相當長需要分批處理" for index in range(20))
        created = engine.add_segments("m1", [{"text": text, "start": 0, "end": 120}])

        self.assertGreater(len(created), 1)
        self.assertEqual("".join(segment.text for segment in created), text)

    def test_flush_drains_the_queue_before_the_meeting_ends(self):
        config = MeetingSummaryConfig(max_pending_asr_tokens=40)
        store, engine, _ = self.build(
            [state_update("new_section"), state_update("continue"), state_update("continue")], config
        )
        for index in range(3):
            self.speak(engine, f"第{index}段發言內容需要被整理進紀要。", index * 5, index * 5 + 5)
        results = engine.flush("m1")

        self.assertTrue(any(result.status == "applied" for result in results))
        self.assertEqual(store.load_state("m1").pending_segments, [])


if __name__ == "__main__":
    unittest.main()
