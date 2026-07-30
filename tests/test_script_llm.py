from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

import llm
import script


class FakeSettings:
    enabled = True
    model = "qwen3.5:4b"
    host = "http://localhost:11434"


class ScriptKeywordFallbackTests(unittest.TestCase):
    @patch("script.llm.OllamaClient")
    def test_disabled_model_is_exact_heuristic_path(self, client_type) -> None:
        client = client_type.return_value
        client.settings = FakeSettings()
        client.settings.enabled = False
        paragraphs = ["Energy bills rose.", "The bank changed its fees."]

        result = script.extract_keywords_llm(
            paragraphs,
            {"llm": {"enabled": False, "keywords_per_chunk": 3}},
            logging.getLogger("test"),
        )

        self.assertEqual(
            [script.heuristic_keywords(p, 3, i) for i, p in enumerate(paragraphs)],
            result,
        )
        client.check_status.assert_not_called()
        client.keywords_for_chunk.assert_not_called()

    @patch("script.llm.OllamaClient")
    def test_unreachable_server_falls_back_for_whole_script(
        self, client_type
    ) -> None:
        client = client_type.return_value
        client.settings = FakeSettings()
        client.check_status.return_value = llm.OllamaStatus(
            False, False, "Ollama unavailable"
        )
        paragraphs = ["Energy bills rose.", "The bank changed its fees."]

        result = script.extract_keywords_llm(
            paragraphs,
            {"llm": {"enabled": True, "keywords_per_chunk": 3}},
            logging.getLogger("test"),
        )

        self.assertEqual(
            [script.heuristic_keywords(p, 3, i) for i, p in enumerate(paragraphs)],
            result,
        )
        client.keywords_for_chunk.assert_not_called()

    @patch("script.llm.OllamaClient")
    def test_one_bad_chunk_uses_only_one_heuristic_fallback(
        self, client_type
    ) -> None:
        client = client_type.return_value
        client.settings = FakeSettings()
        client.check_status.return_value = llm.OllamaStatus(True, True, "ready")
        client.keywords_for_chunk.side_effect = [
            ["electric meter", "paper bill", "cash counter"],
            llm.LLMResponseError("bad JSON"),
        ]
        paragraphs = ["Energy bills rose.", "The bank changed its fees."]

        result = script.extract_keywords_llm(
            paragraphs,
            {"llm": {"enabled": True, "keywords_per_chunk": 3}},
            logging.getLogger("test"),
        )

        self.assertEqual(
            ["electric meter", "paper bill", "cash counter"],
            result[0],
        )
        self.assertEqual(script.heuristic_keywords(paragraphs[1], 3, 1), result[1])
        second_recent = client.keywords_for_chunk.call_args_list[1].args[1]
        self.assertEqual(result[0], second_recent)


if __name__ == "__main__":
    unittest.main()
