import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import rate_limit


class ResourceRateLimitTests(unittest.TestCase):
    def setUp(self):
        with rate_limit._lock:
            rate_limit._windows.clear()

    def test_chat_rate_limit_is_scoped_to_user(self):
        request = SimpleNamespace(client=SimpleNamespace(host="198.51.100.10"))
        with patch.dict(
            "os.environ",
            {"CHAT_USER_RATE_LIMIT": "1", "CHAT_IP_RATE_LIMIT": "100"},
        ), patch("app.rate_limit.cache_increment", return_value=None):
            rate_limit.enforce_resource_rate_limit(request, "chat", "user-1")
            with self.assertRaises(HTTPException) as raised:
                rate_limit.enforce_resource_rate_limit(request, "chat", "user-1")

        self.assertEqual(raised.exception.status_code, 429)

    def test_image_rate_limit_is_scoped_to_ip(self):
        request = SimpleNamespace(client=SimpleNamespace(host="198.51.100.11"))
        with patch.dict(
            "os.environ",
            {"IMAGE_IP_RATE_LIMIT": "1", "IMAGE_USER_RATE_LIMIT": "100"},
        ), patch("app.rate_limit.cache_increment", return_value=None):
            rate_limit.enforce_resource_rate_limit(request, "image", "user-1")
            with self.assertRaises(HTTPException) as raised:
                rate_limit.enforce_resource_rate_limit(request, "image", "user-2")

        self.assertEqual(raised.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
