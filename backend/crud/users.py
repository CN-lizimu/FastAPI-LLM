import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.users import User, UserAuthState, UserRefreshToken, UserToken, UserTokenBlacklist
from schemas.users import UserChangePasswordRequest, UserRequest, UserUpdateRequest
from utils import security
from utils.jwt_tokens import create_jwt, decode_jwt, from_timestamp, hash_jti, utc_now_naive, JWTError


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int


# 根据用户名查询数据库
async def get_user_by_username(db: AsyncSession, username: str):
    query = select(User).where(User.username == username)
    result = await db.execute(query)
    return result.scalar_one_or_none()#返回orm对象


# 创建用户
async def create_user(db: AsyncSession, username: str, password: str):#更推荐将crud与pydantic模型解耦，crud只处理数据库操作，pydantic模型负责数据验证和转换，这样可以提高代码的可维护性和灵活性。后面也有没解耦的例子
    # 先密码加密处理 → add
    hashed_password = security.get_hash_password(password)#加密后的密码
    user = User(username=username, password=hashed_password)#为什么不验证是否重名？因为在数据库层面设置了 username 唯一索引，重复会抛出异常
    db.add(user)
    await db.commit()
    await db.refresh(user)  # 从数据库读回最新的 user
    return user#返回orm对象，路由层再转换成pydantic对象响应给前端


# 生成 Token
async def create_token(db: AsyncSession, user_id: int):
    # 生成 Token + 设置过期时间 → 查询数据库当前用户是否有 Token → 有：更新；没有：添加
    token = str(uuid.uuid4())#生成一个随机的uuid字符串作为token
    # timedelta(days=7, hours=2, minutes=30, seconds=10)
    expires_at = datetime.now() + timedelta(days=7)#设置过期时间为7天后
    query = select(UserToken).where(UserToken.user_id == user_id)
    result = await db.execute(query)
    user_token = result.scalar_one_or_none()

    if user_token:
        user_token.token = token
        user_token.expires_at = expires_at
    else:
        user_token = UserToken(user_id=user_id, token=token, expires_at=expires_at)
        db.add(user_token)
        await db.commit()

    return token


async def _get_auth_state(db: AsyncSession, user_id: int) -> UserAuthState:
    result = await db.execute(select(UserAuthState).where(UserAuthState.user_id == user_id))
    state = result.scalar_one_or_none()
    if state:
        return state

    state = UserAuthState(user_id=user_id, token_version=1)
    db.add(state)
    await db.flush()
    return state


async def issue_token_pair(db: AsyncSession, user: User) -> TokenPair:
    settings = get_settings()
    state = await _get_auth_state(db, user.id)

    access_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)
    refresh_delta = timedelta(days=settings.jwt_refresh_token_expire_days)

    access_token, access_claims = create_jwt(
        user_id=user.id,
        username=user.username,
        token_type="access",
        token_version=state.token_version,
        expires_delta=access_delta,
    )
    refresh_token, refresh_claims = create_jwt(
        user_id=user.id,
        username=user.username,
        token_type="refresh",
        token_version=state.token_version,
        expires_delta=refresh_delta,
    )

    refresh_row = UserRefreshToken(
        user_id=user.id,
        jti_hash=hash_jti(refresh_claims["jti"]),
        token_version=state.token_version,
        expires_at=from_timestamp(refresh_claims["exp"]).replace(tzinfo=None),
    )
    db.add(refresh_row)
    await db.commit()

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_in=int(access_delta.total_seconds()),
        refresh_expires_in=int(refresh_delta.total_seconds()),
    )

# 验证用户名和密码
async def authenticate_user(db: AsyncSession, username: str, password: str):
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not security.verify_password(password, user.password):
        return None

    return user


# 根据 Token 查询用户：验证 Token → 查询用户
async def get_user_by_token(db: AsyncSession, token: str):
    try:
        claims = decode_jwt(token, expected_type="access")
    except JWTError:
        return None

    user_id = int(claims["sub"])
    jti_hash = hash_jti(claims["jti"])

    blacklist_result = await db.execute(
        select(UserTokenBlacklist).where(UserTokenBlacklist.jti_hash == jti_hash)
    )
    if blacklist_result.scalar_one_or_none():
        return None

    state = await _get_auth_state(db, user_id)
    if state.token_version != int(claims.get("ver", 0)):
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_access_claims(db: AsyncSession, token: str) -> dict | None:
    try:
        claims = decode_jwt(token, expected_type="access")
    except JWTError:
        return None

    user_id = int(claims["sub"])
    jti_hash = hash_jti(claims["jti"])
    blacklist_result = await db.execute(
        select(UserTokenBlacklist).where(UserTokenBlacklist.jti_hash == jti_hash)
    )
    if blacklist_result.scalar_one_or_none():
        return None

    state = await _get_auth_state(db, user_id)
    if state.token_version != int(claims.get("ver", 0)):
        return None
    return claims


async def refresh_token_pair(db: AsyncSession, refresh_token: str) -> TokenPair | None:
    try:
        claims = decode_jwt(refresh_token, expected_type="refresh")
    except JWTError:
        return None

    user_id = int(claims["sub"])
    jti_hash = hash_jti(claims["jti"])
    result = await db.execute(
        select(UserRefreshToken).where(UserRefreshToken.jti_hash == jti_hash)
    )
    refresh_row = result.scalar_one_or_none()
    if (
        not refresh_row
        or refresh_row.revoked_at is not None
        or refresh_row.expires_at <= utc_now_naive()
    ):
        return None

    state = await _get_auth_state(db, user_id)
    if state.token_version != int(claims.get("ver", 0)) or refresh_row.token_version != state.token_version:
        return None

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        return None

    refresh_row.revoked_at = utc_now_naive()
    pair = await issue_token_pair(db, user)
    new_refresh_claims = decode_jwt(pair.refresh_token, expected_type="refresh")
    refresh_row.replaced_by_jti_hash = hash_jti(new_refresh_claims["jti"])
    await db.commit()
    return pair


async def blacklist_access_token(db: AsyncSession, token: str, reason: str = "logout") -> bool:
    try:
        claims = decode_jwt(token, expected_type="access")
    except JWTError:
        return False

    jti_hash = hash_jti(claims["jti"])
    existing = await db.execute(select(UserTokenBlacklist).where(UserTokenBlacklist.jti_hash == jti_hash))
    if existing.scalar_one_or_none():
        return True

    db.add(
        UserTokenBlacklist(
            user_id=int(claims["sub"]),
            jti_hash=jti_hash,
            expires_at=from_timestamp(claims["exp"]).replace(tzinfo=None),
            reason=reason,
        )
    )
    await db.commit()
    return True


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> bool:
    try:
        claims = decode_jwt(refresh_token, expected_type="refresh")
    except JWTError:
        return False

    result = await db.execute(
        select(UserRefreshToken).where(UserRefreshToken.jti_hash == hash_jti(claims["jti"]))
    )
    refresh_row = result.scalar_one_or_none()
    if not refresh_row:
        return False
    if refresh_row.revoked_at is None:
        refresh_row.revoked_at = utc_now_naive()
        await db.commit()
    return True


async def revoke_all_user_tokens(db: AsyncSession, user_id: int) -> None:
    state = await _get_auth_state(db, user_id)
    state.token_version += 1
    state.updated_at = utc_now_naive()

    await db.execute(
        update(UserRefreshToken)
        .where(UserRefreshToken.user_id == user_id, UserRefreshToken.revoked_at.is_(None))
        .values(revoked_at=utc_now_naive())
    )
    await db.commit()


# 更新用户信息: update更新 → 检查是否命中 → 获取更新后的用户返回
async def update_user(db: AsyncSession, username: str, user_data: UserUpdateRequest):
    # update(User).where(User.username == username).values(字段=值, 字段=值)
    # user_data 是一个Pydantic类型，得到字典 → ** 解包
    # 没有设置值的不更新
    query = update(User).where(User.username == username).values(**user_data.model_dump(#model_dump 将 Pydantic 模型转换为字典
        exclude_unset=True,
        exclude_none=True
    ))
    result = await db.execute(query)
    await db.commit()

    # 检查更新
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 获取一下更新后的用户
    updated_user = await get_user_by_username(db, username)
    return updated_user


# 修改密码: 验证旧密码 → 新密码加密 → 修改密码
# async def change_password(db: AsyncSession, user: User, old_password: str, new_password: str):
async def change_password(db: AsyncSession, user: User, password_data: UserChangePasswordRequest):
    if not security.verify_password(password_data.old_password, user.password):
        return False

    hashed_new_pwd = security.get_hash_password(password_data.new_password)
    user.password = hashed_new_pwd
    # 更新: 由SQLAlchemy真正接管这个 User 对象，确保可以 commit
    # 规避 session 过期或关闭导致的不能提交的问题
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await revoke_all_user_tokens(db, user.id)
    return True
