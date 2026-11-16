import unittest

from app.pipeline.asr import RawSegment, Word
from app.pipeline.diarization import Turn
from app.pipeline.transcript import (
    FinalSegment,
    build_segments,
    clean_text,
    format_timestamp,
    render_context,
    select_relevant,
    to_srt,
)


class AlignmentTests(unittest.TestCase):
    def test_words_are_split_on_speaker_change(self):
        raw = [
            RawSegment(
                start=0.0,
                end=4.0,
                text="你好世界今天天气不错",
                words=[
                    Word(0.0, 1.0, "你好"),
                    Word(1.0, 2.0, "世界"),
                    Word(2.0, 3.0, "今天"),
                    Word(3.0, 4.0, "天气不错"),
                ],
            )
        ]
        turns = [Turn(0.0, 2.0, 0), Turn(2.0, 4.0, 1)]
        segments = build_segments(raw, turns)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].speaker, "说话人 1")
        self.assertEqual(segments[1].speaker, "说话人 2")
        self.assertEqual(segments[0].text, "你好世界")
        self.assertEqual(segments[1].text, "今天天气不错")

    def test_without_diarization_keeps_text(self):
        raw = [RawSegment(0, 2, "第一句"), RawSegment(2, 3, "第二句")]
        segments = build_segments(raw, [])
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "")
        self.assertEqual(segments[0].text, "第一句第二句")

    def test_clean_text_removes_cjk_spaces(self):
        self.assertEqual(clean_text("你 好 世界 ， 再见"), "你好世界，再见")
        self.assertEqual(clean_text("hello  world"), "hello world")

    def test_timestamp(self):
        self.assertEqual(format_timestamp(65), "01:05")
        self.assertEqual(format_timestamp(3665), "01:01:05")

    def test_srt(self):
        srt = to_srt([FinalSegment(1.5, 3.25, "说话人 1", "你好")])
        self.assertIn("00:00:01,500 --> 00:00:03,250", srt)
        self.assertIn("说话人 1：你好", srt)

    def test_select_relevant_keeps_budget(self):
        segments = [FinalSegment(i, i + 1, "说话人 1", f"这是第 {i} 段内容，讨论人工智能。" * 3) for i in range(200)]
        chosen = select_relevant(segments, "人工智能", budget_chars=3000, max_segments=20)
        self.assertLess(len(chosen), len(segments))
        self.assertTrue(all(s in segments for s in chosen))
        self.assertLessEqual(len(render_context(chosen)), 3000)


if __name__ == "__main__":
    unittest.main()
