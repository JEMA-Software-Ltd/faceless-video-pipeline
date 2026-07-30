from __future__ import annotations

import json
import logging
import unittest
from unittest.mock import Mock, patch

import doctor
import llm


def config(*, enabled: bool = True, retries: int = 2) -> dict:
    return {
        "llm": {
            "enabled": enabled,
            "backend": "ollama",
            "host": "http://localhost:11434/",
            "model": "qwen3.5:4b",
            "timeout_seconds": 120,
            "context": 4096,
            "max_retries": retries,
            "keywords_per_chunk": 3,
        }
    }


def fake_response(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class OllamaStatusTests(unittest.TestCase):
    @patch("llm.requests.get")
    def test_status_confirms_configured_model(self, get: Mock) -> None:
        get.return_value = fake_response(
            {"models": [{"name": "qwen3.5:4b"}, {"name": "other:latest"}]}
        )

        status = llm.OllamaClient(config()).check_status()

        self.assertTrue(status.ready)
        self.assertIn("is installed", status.detail)
        get.assert_called_once_with(
            "http://localhost:11434/api/tags",
            timeout=5.0,
        )

    @patch("llm.requests.get")
    def test_status_reports_missing_model(self, get: Mock) -> None:
        get.return_value = fake_response({"models": [{"name": "other:latest"}]})

        status = llm.OllamaClient(config()).check_status()

        self.assertTrue(status.reachable)
        self.assertFalse(status.model_present)
        self.assertIn("ollama pull qwen3.5:4b", status.detail)

    @patch("doctor.llm.OllamaClient")
    def test_doctor_warns_when_disabled_even_if_unreachable(
        self, client_type: Mock
    ) -> None:
        client = client_type.return_value
        client.settings.enabled = False
        client.check_status.return_value = llm.OllamaStatus(
            False, False, "Ollama unavailable"
        )

        status, detail = doctor.check_ollama(config(enabled=False), logging.getLogger())

        self.assertEqual(doctor.WARN, status)
        self.assertIn("disabled by config", detail)

    @patch("doctor.llm.OllamaClient")
    def test_doctor_fails_when_enabled_and_model_missing(
        self, client_type: Mock
    ) -> None:
        client = client_type.return_value
        client.settings.enabled = True
        client.check_status.return_value = llm.OllamaStatus(
            True, False, "model missing"
        )

        status, _ = doctor.check_ollama(config(), logging.getLogger())

        self.assertEqual(doctor.FAIL, status)


class StructuredGenerationTests(unittest.TestCase):
    def client(self, *, retries: int = 2) -> llm.OllamaClient:
        client = llm.OllamaClient(config(retries=retries))
        client._status = llm.OllamaStatus(True, True, "ready")
        return client

    @patch("llm.requests.post")
    def test_malformed_output_is_retried_then_validated(self, post: Mock) -> None:
        post.side_effect = [
            fake_response({"response": "not json"}),
            fake_response({"response": json.dumps({"wrong": []})}),
            fake_response(
                {
                    "response": json.dumps(
                        {
                            "keywords": [
                                "prepayment meter",
                                "supermarket checkout",
                                "terraced houses",
                            ]
                        }
                    )
                }
            ),
        ]

        result = self.client().keywords_for_chunk(
            "A paragraph about household bills.",
            ["energy bill", "cash payment"],
        )

        self.assertEqual(
            ["prepayment meter", "supermarket checkout", "terraced houses"],
            result,
        )
        self.assertEqual(3, post.call_count)
        payload = post.call_args.kwargs["json"]
        self.assertFalse(payload["stream"])
        self.assertEqual(4096, payload["options"]["num_ctx"])
        self.assertEqual("object", payload["format"]["type"])
        self.assertIn("energy bill", payload["prompt"])

    @patch("llm.requests.post")
    def test_recent_exact_repeat_is_rejected(self, post: Mock) -> None:
        post.side_effect = [
            fake_response(
                {
                    "response": json.dumps(
                        {
                            "keywords": [
                                "energy bill",
                                "payment counter",
                                "electric meter",
                            ]
                        }
                    )
                }
            ),
            fake_response(
                {
                    "response": json.dumps(
                        {
                            "keywords": [
                                "prepayment meter",
                                "payment counter",
                                "terraced houses",
                            ]
                        }
                    )
                }
            ),
        ]

        result = self.client().keywords_for_chunk(
            "A paragraph about household bills.",
            ["energy bill"],
        )

        self.assertEqual("prepayment meter", result[0])
        self.assertEqual(2, post.call_count)

    @patch("llm.requests.post")
    def test_exhausted_retries_raise_recoverable_error(self, post: Mock) -> None:
        post.return_value = fake_response({"response": "{}"})

        with self.assertRaises(llm.LLMResponseError):
            self.client(retries=1).keywords_for_chunk("Narration.", [])

        self.assertEqual(2, post.call_count)

    @patch("llm.requests.post")
    def test_disabled_client_does_not_call_server(self, post: Mock) -> None:
        client = llm.OllamaClient(config(enabled=False))

        with self.assertRaises(llm.LLMUnavailable):
            client.keywords_for_chunk("Narration.", [])

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
