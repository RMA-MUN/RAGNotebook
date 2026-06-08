import asyncio
import json
import os
from typing import Any

import redis.asyncio as redis
from redis.asyncio import ConnectionPool

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "3"))

# 全局连接池
_pool: ConnectionPool | None = None
_pool_loop_id: int | None = None


async def _get_pool() -> ConnectionPool:
    """获取或创建Redis连接池，如果event loop发生变化则重建"""
    global _pool, _pool_loop_id
    current_loop_id = id(asyncio.get_running_loop())

    if _pool is not None and _pool_loop_id != current_loop_id:
        try:
            await _pool.disconnect()
        except Exception:
            pass
        _pool = None

    if _pool is None:
        _pool = ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True,
        )
        _pool_loop_id = current_loop_id

    return _pool


async def connect_redis():
    """从连接池获取Redis客户端"""
    pool = await _get_pool()
    return redis.Redis(connection_pool=pool)


async def close_redis():
    """关闭Redis连接池"""
    global _pool, _pool_loop_id
    if _pool:
        try:
            await _pool.disconnect()
        except Exception:
            pass
        _pool = None
        _pool_loop_id = None

async def check_redis_connection() -> bool:
    """检查Redis连接"""
    try:
        redis_client = await connect_redis()
        await redis_client.ping()
        return True
    except Exception as e:
        print(f"Redis连接失败: {e}")
        return False

# 设置和读取redis
async def get_redis_cache_str(key: str) -> str | None:
    """根据key获取redis缓存 (字符串类型)"""
    try:
        redis_client = await connect_redis()
        return await redis_client.get(key)
    except Exception as e:
        print(f"获取redis缓存失败: {e}")
        return None

async def get_redis_cache_json(key: str) -> dict | None:
    """根据key获取redis缓存 (字典或列表类型)"""
    try:
        redis_client = await connect_redis()
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        print(f"获取redis的JSON缓存失败: {e}")
        return None

async def set_redis_cache(key: str, value: Any, expire: int = 3600) -> bool:
    """
    根据key设置redis缓存

    :param key: 缓存键
    :param value: 缓存值
    :param expire: 过期时间(秒)
    :return: None
    """
    try:
        redis_client = await connect_redis()
        if isinstance(value, str):
            # 如果是字符串，直接设置缓存
            await redis_client.set(key, value, ex=expire)
        elif isinstance(value, (dict, list)):
            # 如果是字典或列表，转为json字符串在设置缓存
            await redis_client.set(key, json.dumps(value, ensure_ascii=False), ex=expire)
        else:
            # 其他类型，尝试转换为字符串
            await redis_client.set(key, str(value), ex=expire)
        return True

    except Exception as e:
        print(f"设置redis缓存失败: {e}")
        return False
