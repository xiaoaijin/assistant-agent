"""管理端鉴权原语：pbkdf2 密码哈希 + HMAC 签名 token（纯函数）。"""

import time

from app.admin_auth import hash_password, sign_token, verify_password, verify_token


def test_password_hash_roundtrip():
    stored = hash_password("s3cret")
    assert stored != "s3cret"
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_token_roundtrip():
    token = sign_token({"sub": "alice", "shop_id": None}, secret="k", ttl_seconds=60)
    claims = verify_token(token, secret="k")
    assert claims is not None
    assert claims["sub"] == "alice"


def test_tampered_token_rejected():
    token = sign_token({"sub": "alice"}, secret="k", ttl_seconds=60)
    assert verify_token(token[:-2] + "xx", secret="k") is None


def test_wrong_secret_rejected():
    token = sign_token({"sub": "alice"}, secret="k", ttl_seconds=60)
    assert verify_token(token, secret="other") is None


def test_expired_token_rejected():
    token = sign_token({"sub": "alice"}, secret="k", ttl_seconds=-1)
    assert verify_token(token, secret="k") is None


def test_token_payload_is_signed_not_encrypted():
    import base64

    token = sign_token({"sub": "alice"}, secret="k", ttl_seconds=60)
    body = token.rsplit(".", 1)[0]  # 不需要 secret 即可解码查看，签名仅防篡改
    assert "alice" in base64.urlsafe_b64decode(body).decode()
