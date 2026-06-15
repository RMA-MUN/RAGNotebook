import json
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import bcrypt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select

from app.db.db_config import AsyncSessionLocal
from app.db.redis_config import connect_redis, set_redis_cache
from app.models.user_model import User

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

security = HTTPBearer()


def hash_password(password: str) -> str:
    """使用 bcrypt 对密码进行哈希"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user: User) -> Tuple[str, int]:
    """为用户签发 JWT token（payload 结构与 Django 保持一致）"""
    expire_time = int(time.time()) + 60 * 60 * 24  # 24小时过期
    payload = {
        "user_id": user.uuid,
        "username": user.username,
        "email": user.email,
        "exp": expire_time,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, expire_time


def decode_jwt(token: str) -> Optional[Dict[str, Any]]:
    """解析 JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


# 向后兼容别名
decode_django_jwt = decode_jwt


async def blacklist_token(token: str):
    """将 token 加入 Redis 黑名单"""
    payload = decode_jwt(token)
    if payload is None:
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        current_time = int(time.time())
        ttl = exp - current_time if exp > current_time else 0
        redis_client = await connect_redis()
        await redis_client.set(f"blacklist:{jti}", "1", ex=ttl if ttl > 0 else 60 * 60 * 24)


async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """从 JWT 中获取当前用户 UUID"""
    token = credentials.credentials
    payload = decode_jwt(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查 JWT 是否在黑名单中
    jti = payload.get("jti")
    if jti:
        redis_client = await connect_redis()
        is_blacklisted = await redis_client.get(f"blacklist:{jti}")
        if is_blacklisted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

    user_id: str = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user ID in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """从 JWT 获取当前用户完整对象"""
    user_id = await get_current_user_id(credentials)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.uuid == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        if user.status != 1:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is disabled or locked",
            )
        return user


async def get_user_from_db(user_id: str) -> Optional[User]:
    """根据 uuid 查询用户"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.uuid == user_id))
        return result.scalar_one_or_none()


async def get_user_info_from_redis(user_id: str) -> Optional[Dict[str, Any]]:
    """从 Redis 读取用户信息，miss 则从数据库查询并回填缓存"""
    redis_client = await connect_redis()
    key = f"user:{user_id}"

    try:
        cached = await redis_client.get(key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                await redis_client.delete(key)
    except Exception:
        pass

    # Redis miss → 查数据库
    user = await get_user_from_db(user_id)
    if user is None:
        return None

    user_info = {
        "id": user.uuid,
        "username": user.username,
        "email": user.email,
        "avatar": user.avatar,
        "telephone": user.telephone,
        "gender": user.gender,
        "bio": user.bio,
        "status": user.status,
        "create_time": user.date_joined.isoformat() if user.date_joined else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }

    # 回填 Redis 缓存 1 小时
    await set_redis_cache(key, user_info, expire=3600)
    return user_info
