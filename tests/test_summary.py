import json
import threading
import unittest

from backend.summary import MLXSummaryBackend



class SummaryPromptTest(unittest.TestCase):
    def test_incremental_prompt_requires_standalone_deduplicated_notes(self):
        class FakeTokenizer:
            def apply_chat_template(self, messages, **kwargs):
                self.messages = messages
                return "prompt"

            def encode(self, prompt):
                return [1, 2, 3]

        backend = MLXSummaryBackend.__new__(MLXSummaryBackend)
        backend.model = object()
        backend.tokenizer = FakeTokenizer()
        backend._lock = threading.Lock()
        backend._generate = lambda *args, **kwargs: json.dumps(
            {
                "summary": "整合後的摘要",
                "key_points": ["合併後的重點"],
                "decisions": [],
                "action_items": [],
            },
            ensure_ascii=False,
        )

        result = backend.summarize(
            "[1.0s–2.0s] 後續討論",
            {"summary": "先前摘要", "key_points": [], "decisions": [], "action_items": []},
        )

        system_prompt = backend.tokenizer.messages[0]["content"]
        task_prompt = backend.tokenizer.messages[1]["content"]
        self.assertIn("可獨立閱讀", system_prompt)
        self.assertIn("合併語意相同", system_prompt)
        self.assertIn("不得在結果中提及", system_prompt)
        self.assertIn("重寫整份紀要", task_prompt)
        self.assertEqual(result["context_tokens"], 3)


if __name__ == "__main__":
    unittest.main()
