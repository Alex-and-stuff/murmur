import unittest

from backend.meeting.config import ContextBudgetExceeded, MeetingSummaryConfig
from backend.meeting.context import ContextBuilder
from backend.meeting.models import ASRSegment, MeetingSection, MeetingState, SectionIndexEntry
from backend.meeting.tokens import HeuristicTokenCounter


def section(section_id="sec1", points=3):
    return MeetingSection(
        section_id=section_id,
        title="控制架構",
        summary="討論 MPC 與 BMS 的介面與權限邊界。",
        short_descriptor="MPC 串接 BMS",
        key_points=[f"重點{index}：這一項描述了一個獨立的討論主題" for index in range(points)],
        decisions=["由 BMS 保留最終控制權"],
        open_questions=["逾時秒數尚未決定"],
    )


class BudgetTest(unittest.TestCase):
    def test_the_default_budget_reserves_output_and_is_not_over_subscribed(self):
        config = MeetingSummaryConfig()
        config.validate()
        parts = (
            config.max_instruction_tokens
            + config.max_index_tokens
            + config.max_current_section_tokens
            + config.max_pending_asr_tokens
            + config.safety_margin_tokens
        )
        self.assertEqual(config.max_input_tokens, config.max_context_tokens - config.output_reserve_tokens)
        self.assertLessEqual(parts, config.max_input_tokens)

    def test_an_over_subscribed_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            MeetingSummaryConfig(max_pending_asr_tokens=4_000).validate()


class ContextBuilderTest(unittest.TestCase):
    def setUp(self):
        self.config = MeetingSummaryConfig()
        self.builder = ContextBuilder(self.config, HeuristicTokenCounter())

    def test_a_prompt_stays_inside_the_input_budget(self):
        state = MeetingState(
            meeting_id="m1",
            current_section=section(),
            pending_segments=[
                ASRSegment(f"s{index}", "這是一段逐字稿內容，需要被整理。" * 3, index, index + 5)
                for index in range(40)
            ],
        )
        context = self.builder.build(state)
        self.assertLessEqual(context.tokens["total_input"], self.config.max_input_tokens)
        self.assertLessEqual(context.tokens["pending_asr"], self.config.max_pending_asr_tokens)

    def test_segments_that_do_not_fit_stay_queued_instead_of_being_dropped(self):
        segments = [
            ASRSegment(f"s{index}", "這是一段逐字稿內容，需要被整理。" * 3, index, index + 5)
            for index in range(60)
        ]
        context = self.builder.build(MeetingState("m1", pending_segments=segments))
        self.assertTrue(context.deferred_segment_ids)
        self.assertEqual(
            context.included_segment_ids + context.deferred_segment_ids,
            [segment.segment_id for segment in segments],
        )
        self.assertIn(f"deferred_{len(context.deferred_segment_ids)}_segments", context.compaction_applied)

    def test_a_long_index_is_compacted_instead_of_growing_with_the_meeting(self):
        entries = [
            SectionIndexEntry(f"sec{index}", f"主題{index}", "這是一段偏長的主題描述，用來測試索引壓縮行為")
            for index in range(60)
        ]
        context = self.builder.build(MeetingState("m1", section_index=entries))
        self.assertLessEqual(context.tokens["index"], self.config.max_index_tokens)
        self.assertTrue(context.compaction_applied)

    def test_an_oversized_current_section_is_compacted_explicitly(self):
        state = MeetingState("m1", current_section=section(points=60))
        context = self.builder.build(state)
        self.assertIn("current_section_compacted", context.compaction_applied)
        self.assertIsNotNone(context.compacted_section)
        self.assertLessEqual(context.tokens["current_section"], self.config.max_current_section_tokens)
        # Decisions are never dropped by compaction.
        self.assertEqual(context.compacted_section.decisions, ["由 BMS 保留最終控制權"])

    def test_instructions_larger_than_their_budget_raise_instead_of_truncating(self):
        config = MeetingSummaryConfig(max_instruction_tokens=10)
        builder = ContextBuilder(config, HeuristicTokenCounter())
        with self.assertRaises(ContextBudgetExceeded):
            builder.build(MeetingState("m1"))


if __name__ == "__main__":
    unittest.main()
