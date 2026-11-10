"""管理端鉴权原语（纯函数，无 DB）：pbkdf2 密码哈希 + HMAC 签名 token。

token 是「签名不加密」的 JSON（base64url），仅防篡改；过期与签名校验都在 verify_token。
"""

import base64
import hashlib
import hmac
import json
import os
import time

_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def sign_token(claims: dict, secret: str, ttl_seconds: int) -> str:
    payload = {**claims, "exp": time.time() + ttl_seconds}
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode())
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{body.decode()}.{sig}"


def verify_token(token: str, secret: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
        expect = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, TypeError):
        return None
