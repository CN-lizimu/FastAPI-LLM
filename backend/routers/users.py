from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from models.users import User
from schemas.users import (
    LogoutRequest,
    RefreshTokenRequest,
    UserRequest,
    UserAuthResponse,
    UserInfoResponse,
    UserUpdateRequest,
    UserChangePasswordRequest,
)

from config.db_conf import get_db
from crud import users
from utils.response import success_response
from utils.auth import get_current_token, get_current_user

router = APIRouter(prefix="/api/user", tags=["users"])


def _build_auth_response(token_pair: users.TokenPair, user: User) -> UserAuthResponse:
    return UserAuthResponse(
        token=token_pair.access_token,
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        token_type=token_pair.token_type,
        expires_in=token_pair.expires_in,
        refresh_expires_in=token_pair.refresh_expires_in,
        user_info=UserInfoResponse.model_validate(user),
    )


@router.post("/register")
async def register(user_data: UserRequest, db: AsyncSession = Depends(get_db)):  # 用户信息 和 db
    # 注册逻辑：验证用户是否存在 -> 创建用户 → 生成 Token  → 响应结果
    existing_user = await users.get_user_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户已存在")
    user = await users.create_user(db, user_data.username, user_data.password)
    token_pair = await users.issue_token_pair(db, user)
    # 使用 Pydantic 模型类进行数据转换和验证，确保响应数据符合预期的格式和要求，user原来是orm对象，现在是pydantic对象
    response_data = _build_auth_response(token_pair, user)
    return success_response(message="注册成功", data=response_data)#本质就是把orm对象转换成pydantic对象，最终响应给前端的还是json字符串，前端拿到后再转换成js对象使用


@router.post("/login")
async def login(user_data: UserRequest, db: AsyncSession = Depends(get_db)):
    # 登录逻辑：验证用户是否存在 -> 验证密码 -> 生成 Token  → 响应结果
    user = await users.authenticate_user(db, user_data.username, user_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token_pair = await users.issue_token_pair(db, user)
    response_data = _build_auth_response(token_pair, user)
    return success_response(message="登录成功", data=response_data)


@router.post("/refresh")
async def refresh_token(payload: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    token_pair = await users.refresh_token_pair(db, payload.refresh_token)
    if not token_pair:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh Token 无效或已过期")

    user = await users.get_user_by_token(db, token_pair.access_token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或令牌无效")
    return success_response(message="刷新令牌成功", data=_build_auth_response(token_pair, user))


@router.post("/logout")
async def logout(
        payload: LogoutRequest | None = None,
        token: str = Depends(get_current_token),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    await users.blacklist_access_token(db, token, reason="logout")
    if payload and payload.refresh_token:
        await users.revoke_refresh_token(db, payload.refresh_token)
    return success_response(message="退出登录成功")


# 查Token查用户 → 封装crud → 功能整合成一个工具函数 → 路由导入使用: 依赖注入
@router.get("/info")
async def get_user_info(user: User = Depends(get_current_user)):
    return success_response(message="获取用户信息成功", data=UserInfoResponse.model_validate(user))


# 修改用户信息：验证Token → 更新（用户输入数据 put 提交 → 请求体参数 → 定义Pydantic模型类） → 响应结果
# 参数：用户输入的 + 验证Token的 + db（调用更新的方法）
@router.put("/update")
async def update_user_info(user_data: UserUpdateRequest, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    user = await users.update_user(db, user.username, user_data)
    return success_response(message="更新用户信息成功", data=UserInfoResponse.model_validate(user))


@router.put("/password")
async def update_password(
        password_data: UserChangePasswordRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)):
    res_change_pwd = await users.change_password(db, user, password_data)
    if not res_change_pwd:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="修改密码失败，请稍后再试")
    return success_response(message="修改密码成功")
