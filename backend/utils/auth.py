from fastapi import Header, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from config.db_conf import get_db
from crud import users


def extract_bearer_token(authorization: str) -> str:
    authorization = authorization.strip()
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return authorization


# 整合 根据 Token 查询用户，返回用户
async def get_current_user(
        authorization: str = Header(..., alias="Authorization"),# 获取请求头中的 Authorization 字段，并将其作为参数传递给 get_current_user 函数
        db: AsyncSession = Depends(get_db)
):
    # Bearer xxxxx
    token = extract_bearer_token(authorization)
    user = await users.get_user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌或已经过期的令牌")

    return user


async def get_current_token(
        authorization: str = Header(..., alias="Authorization"),
) -> str:
    return extract_bearer_token(authorization)
