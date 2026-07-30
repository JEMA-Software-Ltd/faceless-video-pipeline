import unittest

from charts import detect_candidate


class ChartCandidateTests(unittest.TestCase):
    def test_accepts_verified_from_to_comparison(self):
        chunk = {
            "index": 3,
            "text": "The loss fell from nine million pounds to three million pounds.",
        }
        candidate, reason = detect_candidate(chunk, 1.5)
        self.assertEqual(reason, "accepted")
        self.assertEqual(candidate.labels, ["From", "To"])
        self.assertEqual([f.value for f in candidate.figures], [9_000_000, 3_000_000])

    def test_rejects_ratio_below_threshold(self):
        chunk = {
            "index": 4,
            "text": "The loss rose from nine million pounds to ten million pounds.",
        }
        candidate, reason = detect_candidate(chunk, 1.5)
        self.assertIsNone(candidate)
        self.assertIn("below", reason)

    def test_rejects_numbers_without_comparison_cue(self):
        chunk = {
            "index": 5,
            "text": "It held nine million pounds and three million pounds.",
        }
        candidate, reason = detect_candidate(chunk, 1.5)
        self.assertIsNone(candidate)
        self.assertIn("no explicit", reason)

    def test_accepts_now_then_prior_year_in_source_order(self):
        chunk = {
            "index": 6,
            "text": (
                "The cost now is two hundred and sixty one pounds. "
                "In twenty twenty two, it was eighty four pounds."
            ),
        }
        candidate, reason = detect_candidate(chunk, 1.5)
        self.assertEqual(reason, "accepted")
        self.assertEqual(candidate.labels, ["Now", "2022"])


if __name__ == "__main__":
    unittest.main()
