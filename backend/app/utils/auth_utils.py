import os
import json
from typing import Optional, Dict, Any
import requests
from dotenv import load_dotenv
from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.failed_response import logger
from app.db.redis_config import connect_redis, set_redis_cache

load_dotenv()

# Django JWT配置
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

# 创建Bearer认证方案
security = HTTPBearer()


def decode_django_jwt(token: str) -> Optional[Dict[str, Any]]:
    """解析Django生成的JWT token
    
    Args:
        token: JWT token字符串
        
    Returns:
        解析后的payload，如果解析失败返回None
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """从Django JWT中获取当前用户UUID
    
    Args:
        credentials: HTTP认证凭据
        
    Returns:
        用户的UUID
        
    Raises:
        HTTPException: 认证失败时抛出
    """
    token = credentials.credentials
    payload = decode_django_jwt(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查JWT是否在黑名单中
    jti = payload.get("jti")
    if jti:
        redis_client = await connect_redis()
        # 使用通配符查询所有可能的黑名单键格式
        # 匹配任何前缀的blacklist键，如:1:blacklist:{jti}、blacklist:{jti}等
        wildcard_pattern = f"*blacklist:{jti}"
        
        # 获取所有匹配的键
        matching_keys = await redis_client.keys(wildcard_pattern)
        
        # 如果有匹配的键，说明JWT在黑名单中
        if matching_keys:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 从Django JWT中提取user_id（uuid）
    user_id: str = payload.get("user_id")
    
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user ID in token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user_id


async def fetch_user_info_from_django_api(token: str, url: str) -> Optional[Dict[str, Any]]:
    """从Django API获取用户信息
    
    Args:
        token: JWT token字符串
        
    Returns:
        用户信息字典，如果获取失败返回None
    """

    try:
        # 构建请求头
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        # 调用Django API
        response = requests.get(
            url=url,
            headers=headers
        )
        
        if response.status_code == 200:
            user_data = response.json()
            logger.info(f"【debug】 从Django API获取用户信息成功", extra={"path": "auth_utils.fetch_user_info_from_django_api"})
            return user_data
        else:
            logger.error(f"【debug】 从Django API获取用户信息失败，status_code: {response.status_code}", extra={"path": "auth_utils.fetch_user_info_from_django_api"})
            return None
    except Exception as e:
        logger.error(f"【debug】 调用Django API时出错: {str(e)}", extra={"path": "auth_utils.fetch_user_info_from_django_api"})
        return None


async def get_user_info_from_redis(user_id: str, credentials: HTTPAuthorizationCredentials):
    """从Redis中获取用户信息
    
    Args:
        user_id: 用户ID
        credentials: HTTP认证凭据
        
    Returns:
        用户信息
    """
    redis_client = await connect_redis()
    key = f":1:user:{user_id}"
    
    try:
        # 从Redis中获取用户信息
        user_info = await redis_client.get(key)
        if user_info is None:
            # 降级调用django查询用户信息
            user_data = await fetch_user_info_from_django_api(credentials.credentials, os.getenv("DJANGO_API_URL") + "/user/detail/")
            if user_data:
                # 将用户信息存入Redis，设置过期时间为1小时
                await set_redis_cache(
                    key,
                    user_data,
                    expire=3600
                )
                user_info = user_data
        else:
            # 如果从Redis中获取到数据，尝试将其解析为字典
            try:
                
                user_info = json.loads(user_info)
            except json.JSONDecodeError:
                # 如果解析失败，删除旧数据并重新获取
                await redis_client.delete(key)
                user_data = await fetch_user_info_from_django_api(credentials.credentials, os.getenv("DJANGO_API_URL") + "/user/detail/")
                if user_data:
                    await set_redis_cache(
                        key,
                        user_data,
                        expire=3600
                    )
                    user_info = user_data
                else:
                    user_info = None
    except UnicodeDecodeError:
        # 处理解码错误，删除旧数据并重新获取
        await redis_client.delete(key)
        user_data = await fetch_user_info_from_django_api(credentials.credentials, os.getenv("DJANGO_API_URL") + "/user/detail/")
        if user_data:
            await set_redis_cache(
                key,
                user_data,
                expire=3600
            )
            user_info = user_data
        else:
            user_info = None

    return user_info