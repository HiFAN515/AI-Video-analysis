import unittest

from app.pipeline.downloader import DownloadError, detect_platform, normalize_url


class UrlTests(unittest.TestCase):
    def test_bare_bv(self):
        self.assertEqual(normalize_url("BV1xx411c7mD"), "https://www.bilibili.com/video/BV1xx411c7mD")

    def test_share_text(self):
        text = "【标题】 https://b23.tv/abcdefg 复制打开"
        self.assertEqual(normalize_url(text), "https://b23.tv/abcdefg")

    def test_platform(self):
        self.assertEqual(detect_platform("https://www.bilibili.com/video/BV1xx411c7mD"), "bilibili")
        self.assertEqual(detect_platform("https://youtu.be/xyz"), "youtube")

    def test_invalid(self):
        with self.assertRaises(DownloadError):
            normalize_url("not a link")


if __name__ == "__main__":
    unittest.main()
