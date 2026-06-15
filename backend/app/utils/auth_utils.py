import json
import os
import time
import uuid
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select

from app.core.failed_response import logger
from app.db.db_config import AsyncSessionLocal
from app.db.redis_config import connect_redis, set_redis_cache

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

security = HTTPBearer()

pwd_context = CryptContext(schemes=["bcrypt", "django_pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def generate_token(user_id: str, username: str, email: str) -> tuple[str, int]:
    expire_time = int(time.time()) + 60 * 60 * 24
    payload = {
        "user_id": user_id,
        "username": username,
        "email": email,
        "exp": expire_time,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, expire_time


def decode_django_jwt(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def blacklist_token(token: str):
    payload = decode_django_jwt(token)
    if not payload:
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        current_time = int(time.time())
        ttl = max(exp - current_time, 0)
        try:
            redis_client = await connect_redis()
            await redis_client.set(f"blacklist:{jti}", "1", ex=ttl)
        except Exception as e:
            logger.warning(f"黑名单Token失败: {e}")


async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    payload = decode_django_jwt(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = payload.get("jti")
    if jti:
        try:
            redis_client = await connect_redis()
            matching_keys = await redis_client.keys(f"*blacklist:{jti}")
            if matching_keys:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Redis黑名单检查失败，跳过: {e}")

    user_id: str = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user ID in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def get_user_info_from_db(user_id: str) -> dict[str, Any] | None:
    from app.models.user_model import User

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.uuid == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return None
        return {
            "uuid": user.uuid,
            "user_id": user.uuid,
            "id": user.uuid,
            "username": user.username,
            "email": user.email,
            "telephone": user.telephone,
            "gender": user.gender,
            "bio": user.bio,
            "avatar": user.avatar,
            "status": user.status,
            "date_joined": str(user.date_joined) if user.date_joined else None,
            "last_login": str(user.last_login) if user.last_login else None,
            "is_active": user.is_active,
        }


async def get_user_info_from_redis(user_id: str, credentials: HTTPAuthorizationCredentials | None = None):
    redis_client = await connect_redis()
    key = f"user:{user_id}"

    try:
        user_info = await redis_client.get(key)
        if user_info is not None:
            try:
                return json.loads(user_info)
            except json.JSONDecodeError:
                await redis_client.delete(key)

        user_data = await get_user_info_from_db(user_id)
        if user_data:
            await set_redis_cache(key, user_data, expire=3600)
            return user_data
        return None
    except UnicodeDecodeError:
        await redis_client.delete(key)
        user_data = await get_user_info_from_db(user_id)
        if user_data:
            await set_redis_cache(key, user_data, expire=3600)
            return user_data
        return None
