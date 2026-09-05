from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tg_username: Mapped[str | None] = mapped_column(String, nullable=True)
    invited_by_tg_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="SET NULL"), nullable=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_connection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    connections: Mapped[list[Connection]] = relationship(
        back_populates="owner",
        foreign_keys="Connection.owner_tg_id",
        cascade="all, delete-orphan",
    )


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("owner_tg_id", "base_url", "scope_path", name="uq_connection_scope"),
        CheckConstraint("provider = 'gitlab'", name="ck_connection_provider"),
        CheckConstraint("scope_kind IN ('group', 'project')", name="ck_connection_scope_kind"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    owner_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String, nullable=False, default="gitlab")
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    scope_path: Mapped[str] = mapped_column(String, nullable=False)
    scope_kind: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_pat: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    gitlab_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gitlab_username: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="connections", foreign_keys=[owner_tg_id])


class UsedInviteCode(Base):
    __tablename__ = "used_invite_codes"
    __table_args__ = (
        Index("ix_used_invite_codes_code", "code"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    inviter_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), nullable=False
    )
    consumer_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalEvent(Base):
    __tablename__ = "approval_events"
    __table_args__ = (
        CheckConstraint("action IN ('approved', 'unapproved')", name="ck_approval_action"),
        Index("ix_approval_events_mr", "connection_id", "project_id", "mr_iid"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    actor_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="RESTRICT"), nullable=False
    )
    on_behalf_of_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="RESTRICT"), nullable=False
    )
    connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("connections.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mr_iid: Mapped[int] = mapped_column(Integer, nullable=False)
    sha: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEvent(Base):
    """Кто что сделал: одна строка на каждое мутирующее действие в боте."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_project", "base_url", "project_path"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    actor_tg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_user_id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False, default="")
    # scope события: инстанс + path_with_namespace проекта (или scope-путь для
    # действий над подключением). NULL — действие вне проекта (invite, register)
    # либо строка, записанная до появления колонок.
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    project_path: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
