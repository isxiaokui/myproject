import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

TOKEN_LIFETIME_SECONDS = 60 * 60 * 24 * 7
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _secret() -> bytes:
    value = os.getenv("AUTH_SECRET_KEY", "").strip()
    if len(value) < 32 or value.startswith("replace_with_"):
        raise RuntimeError("请在 backend/.env 中设置至少 32 个字符的随机 AUTH_SECRET_KEY。")
    return value.encode("utf-8")


def validate_auth_secret() -> None:
    _secret()


def validate_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("用户名需为 3–32 位字母、数字、点、下划线或短横线。")
    return normalized


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=64
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64url(salt)}${_b64url(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    payload = _b64url(
        json.dumps({"sub": user_id, "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS}, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64url(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def decode_access_token(token: str) -> str | None:
    try:
        payload_text, signature_text = token.split(".", 1)
        expected = _b64url(hmac.new(_secret(), payload_text.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature_text, expected):
            return None
        payload_bytes = base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4))
        payload = json.loads(payload_bytes)
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        subject = payload.get("sub")
        return subject if isinstance(subject, str) and subject else None
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError):
        return None
