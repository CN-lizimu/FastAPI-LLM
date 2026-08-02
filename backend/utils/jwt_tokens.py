from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from config.settings import get_settings


class JWTError(ValueError):
    pass


TokenType = Literal["access", "refresh"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    return utc_now().replace(tzinfo=None)


def to_timestamp(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def from_timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _json_dumps(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sign(signing_input: str) -> str:
    settings = get_settings()
    if settings.jwt_algorithm != "HS256":
        raise JWTError("当前仅支持 HS256 JWT 签名算法")
    signature = hmac.new(
        settings.jwt_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(signature)


def create_jwt(
    *,
    user_id: int,
    username: str,
    token_type: TokenType,
    token_version: int,
    expires_delta: timedelta,
) -> tuple[str, dict[str, Any]]:
    settings = get_settings()
    now = utc_now()
    expires_at = now + expires_delta
    jti = secrets.token_urlsafe(24)

    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "username": username,
        "typ": token_type,
        "jti": jti,
        "ver": token_version,
        "iat": to_timestamp(now),
        "nbf": to_timestamp(now),
        "exp": to_timestamp(expires_at),
    }

    encoded_header = _b64url_encode(_json_dumps(header))
    encoded_payload = _b64url_encode(_json_dumps(payload))
    signing_input = f"{encoded_header}.{encoded_payload}"
    token = f"{signing_input}.{_sign(signing_input)}"
    return token, payload


def decode_jwt(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, signature = token.split(".", 2)
    except ValueError as exc:
        raise JWTError("JWT 格式错误") from exc

    signing_input = f"{encoded_header}.{encoded_payload}"
    expected_signature = _sign(signing_input)
    if not hmac.compare_digest(signature, expected_signature):
        raise JWTError("JWT 签名无效")

    try:
        header = json.loads(_b64url_decode(encoded_header))
        payload = json.loads(_b64url_decode(encoded_payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise JWTError("JWT 内容解析失败") from exc

    settings = get_settings()
    if header.get("alg") != settings.jwt_algorithm:
        raise JWTError("JWT 签名算法不匹配")
    if payload.get("iss") != settings.jwt_issuer:
        raise JWTError("JWT issuer 不匹配")
    if payload.get("aud") != settings.jwt_audience:
        raise JWTError("JWT audience 不匹配")
    if expected_type and payload.get("typ") != expected_type:
        raise JWTError("JWT 类型不匹配")

    now_ts = to_timestamp(utc_now())
    if int(payload.get("nbf", 0)) > now_ts:
        raise JWTError("JWT 尚未生效")
    if int(payload.get("exp", 0)) <= now_ts:
        raise JWTError("JWT 已过期")
    if not payload.get("sub") or not payload.get("jti"):
        raise JWTError("JWT 缺少必要 Claims")

    return payload

