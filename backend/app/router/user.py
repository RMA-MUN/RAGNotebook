from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, or_

from app.core.success_response import success_response
from app.db.db_config import AsyncSessionLocal
from app.db.redis_config import connect_redis
from app.models.user_model import User
from app.schemas.user_schemas import (
    PasswordChangeRequest,
    TokenRefreshRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.utils.auth_utils import (
    blacklist_token,
    create_access_token,
    decode_jwt,
    get_current_user,
    get_current_user_id,
    get_user_info_from_redis,
    hash_password,
    security,
    verify_password,
)

user_router = APIRouter(tags=["user"], prefix="/user")


def _user_to_response(user: User) -> dict:
    return UserResponse.model_validate(user).model_dump()


# ─── 注册 ────────────────────────────────────────────────────────────


@user_router.post("/register/")
async def register(request: UserRegisterRequest):
    """用户注册"""
    # 校验邮箱唯一性
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == request.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail={"email": ["该邮箱已被注册"]})

        # 校验电话唯一性
        if request.telephone:
            existing_tel = await db.execute(select(User).where(User.telephone == request.telephone))
            if existing_tel.scalar_one_or_none():
                raise HTTPException(status_code=400, detail={"telephone": ["该电话号码已被注册"]})

        user = User(
            uuid=User.generate_uuid(),
            username=request.username,
            email=request.email,
            telephone=request.telephone,
            hashed_password=hash_password(request.password),
            status=1,  # ACTIVE
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token, _ = create_access_token(user)
    return {
        "status": 201,
        "message": f"{user.username} 注册成功",
        "user": _user_to_response(user),
        "token": token,
    }


# ─── 登录 ────────────────────────────────────────────────────────────


@user_router.post("/login/")
async def login(request: UserLoginRequest):
    """用户登录"""
    if not request.username and not request.email:
        raise HTTPException(status_code=400, detail="用户名或邮箱至少提供一个")

    async with AsyncSessionLocal() as db:
        if request.username and request.email:
            stmt = select(User).where(or_(User.username == request.username, User.email == request.email))
        elif request.username:
            stmt = select(User).where(User.username == request.username)
        else:
            stmt = select(User).where(User.email == request.email)

        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=400, detail={"non_field_errors": ["用户名或邮箱不存在"]})

    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=400, detail={"non_field_errors": ["密码错误"]})

    if user.status != 1:
        raise HTTPException(status_code=400, detail={"non_field_errors": ["用户状态异常，请检查是否激活或已被锁定"]})

    # 更新最后登录时间
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.uuid == user.uuid))
        u = result.scalar_one_or_none()
        if u:
            u.last_login = datetime.now()
            await db.commit()

    token, _ = create_access_token(user)
    return {
        "message": f"{user.username} 登录成功",
        "user": _user_to_response(user),
        "token": token,
    }


# ─── 获取用户详情 ─────────────────────────────────────────────────────


@user_router.get("/detail/")
async def get_user_detail(
    user_id: str = Depends(get_current_user_id),
):
    """获取用户详情"""
    user_info = await get_user_info_from_redis(user_id)
    if user_info is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return success_response(message="获取用户详情成功", data=user_info)


# ─── 更新用户信息 ────────────────────────────────────────────────────


@user_router.put("/update/")
async def update_user(
    request: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """更新用户信息"""
    # 黑名单旧 token
    await blacklist_token(credentials.credentials)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.uuid == current_user.uuid))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")

        if request.username is not None:
            user.username = request.username
        if request.telephone is not None:
            # 校验电话唯一性
            existing = await db.execute(select(User).where(User.telephone == request.telephone, User.uuid != user.uuid))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=400, detail={"telephone": ["该电话号码已被注册"]})
            user.telephone = request.telephone
        if request.avatar is not None:
            user.avatar = request.avatar
        if request.gender is not None:
            user.gender = request.gender
        if request.bio is not None:
            user.bio = request.bio

        await db.commit()
        await db.refresh(user)

        # 清除 Redis 缓存
        redis_client = await connect_redis()
        await redis_client.delete(f"user:{user.uuid}")

    # 签发新 token
    new_token, _ = create_access_token(user)
    return {
        "message": "用户信息更新成功",
        "user": _user_to_response(user),
        "token": new_token,
    }


# ─── 修改密码 ──────────────────────────────────────────────────────


async def _reset_password_handler(
    old_password: str,
    new_password: str,
    current_user: User,
    credentials: HTTPAuthorizationCredentials,
):
    """修改密码的内部实现"""
    if not verify_password(old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="请检查旧密码是否正确")

    if new_password == old_password:
        raise HTTPException(status_code=400, detail="新密码不能和旧密码相同")

    # 黑名单旧 token
    await blacklist_token(credentials.credentials)

    # 更新密码
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.uuid == current_user.uuid))
        user = result.scalar_one_or_none()
        if user:
            user.hashed_password = hash_password(new_password)
            await db.commit()

    # 签发新 token
    new_token, _ = create_access_token(current_user)
    return {
        "message": "密码重置成功",
        "token": new_token,
    }


@user_router.post("/reset-password/")
async def reset_password(
    request: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """修改密码"""
    return await _reset_password_handler(request.old_password, request.new_password, current_user, credentials)


# 前端使用 /user/change_password/ 路径（历史遗留，与 Django 路由不一致）
@user_router.post("/change_password/")
async def change_password(
    request: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """修改密码（前端别名）"""
    return await _reset_password_handler(request.old_password, request.new_password, current_user, credentials)


# ─── 刷新 Token ──────────────────────────────────────────────────────


@user_router.post("/refresh-token/")
async def refresh_token(request: TokenRefreshRequest):
    """刷新 Token"""
    payload = decode_jwt(request.token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token刷新失败")

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token无效")

    # 黑名单旧 token
    await blacklist_token(request.token)

    # 查用户签发新 token
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.uuid == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    new_token, expire_time = create_access_token(user)
    return {
        "message": "Token刷新成功",
        "token": new_token,
        "expire_time": expire_time,
    }


# ─── 注销 ──────────────────────────────────────────────────────────


@user_router.post("/logout/")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """用户注销 — 将当前 token 加入黑名单"""
    await blacklist_token(credentials.credentials)
    return {"message": "用户注销成功"}
