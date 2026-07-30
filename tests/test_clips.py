import unittest

from clips import caption_cues, select_chunks


class VerticalClipTests(unittest.TestCase):
    def test_caption_cues_follow_character_offsets(self):
        text = "Alpha beta gamma delta epsilon zeta eta theta."
        cues = caption_cues(text, 8.0, words_per_cue=4)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start"], 0.0)
        self.assertGreater(cues[1]["start"], cues[0]["end"])
        self.assertEqual(cues[-1]["end"], 8.0)

    def test_captions_do_not_cross_sentences_or_split_number_phrases(self):
        text = (
            "The debt was roughly seven point nine million pounds. "
            "It later fell."
        )
        cues = caption_cues(text, 8.0, words_per_cue=6)
        bodies = [cue["text"] for cue in cues]
        amount_cue = next(body for body in bodies if "seven" in body)
        self.assertIn("seven point nine million pounds.", amount_cue)
        self.assertNotIn("It", amount_cue)

    def test_selection_is_spread_and_deterministic(self):
        chunks = []
        for idx in range(18):
            chunks.append({
                "index": idx,
                "duration": 20.0,
                "text": f"Chunk {idx} lost nine hundred thousand pounds.",
            })
        cfg = {"count": 6, "min_seconds": 15, "max_seconds": 55}
        first = [c["index"] for c in select_chunks(chunks, cfg)]
        second = [c["index"] for c in select_chunks(chunks, cfg)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 6)
        self.assertTrue(all(b - a >= 2 for a, b in zip(first, first[1:])))

    def test_duration_window_is_hard_filter(self):
        chunks = [
            {"index": 0, "duration": 10, "text": "Nine million pounds."},
            {"index": 1, "duration": 20, "text": "One million pounds."},
        ]
        cfg = {"count": 1, "min_seconds": 15, "max_seconds": 55}
        self.assertEqual(select_chunks(chunks, cfg)[0]["index"], 1)


if __name__ == "__main__":
    unittest.main()
