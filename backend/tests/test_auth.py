import os
import unittest
from unittest.mock import patch

from app import auth


class AuthTests(unittest.TestCase):
    def test_username_is_trimmed_and_case_normalized(self):
        self.assertEqual(auth.validate_username("  Rose_7 "), "rose_7")

    def test_username_rejects_invalid_characters_and_lengths(self):
        for username in ("ab", "contains space", "花朵123", "a" * 33):
            with self.subTest(username=username), self.assertRaises(ValueError):
                auth.validate_username(username)

    def test_password_hash_verifies_only_matching_password(self):
        encoded = auth.hash_password("a-strong-password")
        self.assertTrue(auth.verify_password("a-strong-password", encoded))
        self.assertFalse(auth.verify_password("wrong-password", encoded))
        self.assertFalse(auth.verify_password("anything", "not-a-password-hash"))

    def test_access_token_requires_configured_secret_and_rejects_tampering(self):
        with patch.dict(os.environ, {"AUTH_SECRET_KEY": "t" * 48}):
            token = auth.create_access_token("user-123")
            self.assertEqual(auth.decode_access_token(token), "user-123")
            self.assertIsNone(auth.decode_access_token(token + "x"))

        with patch.dict(os.environ, {"AUTH_SECRET_KEY": "short"}):
            self.assertIsNone(auth.decode_access_token(token))


if __name__ == "__main__":
    unittest.main()
