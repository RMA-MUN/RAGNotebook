from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.core.success_response import success_response
from app.utils.auth_utils import get_current_user_id, get_user_info_from_redis, security

user_router = APIRouter(tags=["user"], prefix="/user")

@user_router.get("/detail/")
async def get_user_info(user_id: str = Depends(get_current_user_id), credentials: HTTPAuthorizationCredentials = Depends(security)):
    """获取用户信息"""
    # 借助 uuid 去查询redis 中存储的用户信息
    user_info = await get_user_info_from_redis(user_id, credentials)
    return success_response(
        message="获取用户信息成功",
        data=user_info,
    )
