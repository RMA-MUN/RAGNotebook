"""密钥加密：用 settings.SECRET_KEY 派生 Fernet 密钥做字段级对称加密。

约定：
- 空输入返回空串（不对空值加密，读取时空值直接落地为 NULL/空）。
- SECRET_KEY 为空时停止加密（明确报错，避免明文落库）。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import settings


def _fernet() -> Fernet:
    if not settings.SECRET_KEY:
        raise RuntimeError("SECRET_KEY 未配置，无法加密 AI 配置中的密钥，请先在 .env 配置 SECRET_KEY")
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # 旧数据/明文残留：返回空串交由调用方判断（已配置但解不开 → 视为退回 .env）
        return ""
