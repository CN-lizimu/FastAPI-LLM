from datetime import datetime
from typing import Optional

from sqlalchemy import Index, Integer, String, Enum, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """
    用户信息表ORM模型
    """
    __tablename__ = 'user'

    # 创建索引，为了保证用户名和手机号的唯一性
    __table_args__ = (
        Index('username_UNIQUE', 'username'),
        Index('phone_UNIQUE', 'phone'),
    )
    #optional意思是可选的，不是必填的字段
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="用户ID")
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="用户名")
    password: Mapped[str] = mapped_column(String(255), nullable=False, comment="密码（加密存储）")
    nickname: Mapped[Optional[str]] = mapped_column(String(50), comment="昵称")
    avatar: Mapped[Optional[str]] = mapped_column(String(255), comment="头像URL",
                                                  default='https://fastly.jsdelivr.net/npm/@vant/assets/cat.jpeg')
    gender: Mapped[Optional[str]] = mapped_column(Enum('male', 'female', 'unknown'), comment="性别", default='unknown')
    bio: Mapped[Optional[str]] = mapped_column(String(500), comment="个人简介", default='这个人很懒，什么都没留下')
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, comment="手机号")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(), onupdate=datetime.now(),
                                                 comment="更新时间")


    def __repr__(self):#这是为了在打印User对象时，能够以更友好的格式显示用户的ID、用户名和昵称等关键信息，方便调试和日志记录。
        return f"<User(id={self.id}, username='{self.username}', nickname='{self.nickname}')>"


class UserToken(Base):
    """
    用户令牌表ORM模型
    """
    __tablename__ = 'user_token'

    # 创建索引
    __table_args__ = (
        Index('token_UNIQUE', 'token'),
        Index('fk_user_token_user_idx', 'user_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="令牌ID")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey(User.id), nullable=False, comment="用户ID")
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, comment="令牌值")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="过期时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(), comment="创建时间")


    def __repr__(self):
        return f"<UserToken(id={self.id}, user_id={self.user_id}, token='{self.token}')>"


class UserAuthState(Base):
    """
    用户认证状态表。

    token_version 用于全局失效某个用户的全部 JWT，例如改密、后台踢下线。
    """
    __tablename__ = "user_auth_state"

    __table_args__ = (
        Index("idx_user_auth_state_user_id", "user_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="认证状态ID")
    user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, comment="用户ID")
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="令牌版本号")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")


class UserRefreshToken(Base):
    """
    Refresh Token 服务端状态表。

    只保存 refresh token 的 jti 哈希，不保存原始 token。
    """
    __tablename__ = "user_refresh_token"

    __table_args__ = (
        Index("idx_user_refresh_token_user_id", "user_id"),
        Index("idx_user_refresh_token_jti_hash", "jti_hash", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="Refresh Token ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="用户ID")
    jti_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="Refresh Token JTI 哈希")
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, comment="签发时的令牌版本号")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="过期时间")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="撤销时间")
    replaced_by_jti_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="轮换后的 Refresh Token JTI 哈希")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class UserTokenBlacklist(Base):
    """
    Access Token 黑名单表。

    JWT 本身无状态，登出时需要把未过期 access token 的 jti 放入黑名单。
    """
    __tablename__ = "user_token_blacklist"

    __table_args__ = (
        Index("idx_user_token_blacklist_jti_hash", "jti_hash", unique=True),
        Index("idx_user_token_blacklist_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="黑名单ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="用户ID")
    jti_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="Access Token JTI 哈希")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="原令牌过期时间")
    reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="加入黑名单原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
