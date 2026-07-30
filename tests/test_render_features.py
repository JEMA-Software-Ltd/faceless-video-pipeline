import tempfile
import unittest
from pathlib import Path

import render


class RenderFeatureTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [{
            "index": 7,
            "text": "It rose to forty six per cent.",
            "duration": 30.0,
            "images": ["images/a.jpg", "images/b.jpg", "images/c.jpg"],
        }]

    def test_zero_pacing_variation_preserves_equal_split(self):
        segments = render.build_timeline(self.chunks, 25, pace_variation=0)
        self.assertEqual([s["frames"] for s in segments], [250, 250, 250])

    def test_pacing_is_deterministic_bounded_and_exact(self):
        first = render.build_timeline(self.chunks, 25, pace_variation=0.3)
        second = render.build_timeline(self.chunks, 25, pace_variation=0.3)
        frames = [s["frames"] for s in first]
        self.assertEqual(frames, [s["frames"] for s in second])
        self.assertEqual(sum(frames), 750)
        self.assertTrue(all(4 * 25 <= value <= 15 * 25 for value in frames))
        self.assertNotEqual(frames, [250, 250, 250])

    def test_short_chunk_borrows_boundary_without_changing_total(self):
        chunks = [
            {
                "index": 0, "text": "A short turn.", "duration": 3.116,
                "images": ["images/a.jpg"],
            },
            {
                "index": 1, "text": "The following point.", "duration": 7.58,
                "images": ["images/b.jpg"],
            },
        ]
        segments = render.build_timeline(chunks, 25, pace_variation=0.3)
        self.assertEqual(sum(s["frames"] for s in segments), round(10.696 * 25))
        self.assertTrue(all(s["frames"] >= 100 for s in segments))
        self.assertGreater(segments[1]["chunk_offset_start"], 0)

    def test_fingerprint_changes_with_relevant_config(self):
        base = {
            "video": {"width": 1920, "height": 1080, "fps": 25,
                      "crf": 20, "preset": "medium", "pace_variation": 0},
            "grade": {"enabled": True},
            "captions": {"enabled": False},
            "overlays": {"enabled": False},
        }
        changed = {**base, "grade": {"enabled": True, "gamma": 0.9}}
        self.assertNotEqual(
            render.render_fingerprint(base, self.chunks)["sha256"],
            render.render_fingerprint(changed, self.chunks)["sha256"],
        )

    def test_cache_invalidation_removes_only_numbered_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips_dir = Path(tmp)
            stale = clips_dir / "clip_0000.mp4"
            unrelated = clips_dir / "preview.mp4"
            stale.write_bytes(b"x")
            unrelated.write_bytes(b"x")
            fingerprint = render.render_fingerprint({}, self.chunks)
            render.prepare_clip_cache(
                clips_dir, fingerprint, False,
                render.common.get_logger("test-render-cache"),
            )
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())

    def test_overlay_is_late_biased_and_source_verified(self):
        cfg = {"overlays": {"enabled": True, "max_per_chunk": 1, "duration": 2.5}}
        segment = {
            "text": "It rose to forty six per cent after the review.",
            "chunk_duration": 10.0,
            "chunk_offset_start": 0.0,
            "chunk_offset_end": 10.0,
        }
        overlay = render.overlay_for_segment(segment, cfg)
        self.assertEqual(overlay["display"], "46%")
        self.assertGreater(overlay["start"], 2.0)
        self.assertEqual(
            segment["text"][overlay["source_start"]:overlay["source_end"]],
            "forty six per cent",
        )

    def test_audio_polish_is_padded_to_video_duration(self):
        cfg = {"audio": {"enabled": True, "normalise": True, "compress": True}}
        command, description = render.build_mux_command(
            "ffmpeg", Path("silent.mp4"), Path("full.wav"), Path("out.mp4"),
            123.456, cfg,
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("loudnorm=I=-16.0", graph)
        self.assertIn("apad=whole_dur=123.456000", graph)
        self.assertIn("atrim=duration=123.456000", graph)
        self.assertIn("narration polish", description)


if __name__ == "__main__":
    unittest.main()
