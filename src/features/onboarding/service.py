"""Onboarding service — pool bootstrap + invite redemption.

Registration is split from connection setup: ``/start`` (with or without
an invite code) only puts the caller into ``users``. Setting up the first
GitLab connection lives in ``/add_connection`` — see the connections
feature.

The seed-admin path fires when ``users`` is empty. Anyone hitting
``/start`` at that moment becomes admin with ``invited_by=None``. Every
subsequent user must arrive via ``/start <code>``.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db import repos
from db.models import User
from db.session import transaction
from features.onboarding.invites import InviteSigner


class OnboardingError(Exception):
    """Base for onboarding failures."""


class InviteInvalid(OnboardingError):
    """HMAC signature doesn't match today's expected value."""


class InviteAlreadyUsed(OnboardingError):
    """Code passed HMAC but has already been consumed."""


class AlreadyRegistered(OnboardingError):
    """Caller is already in the pool."""


class NotInPool(OnboardingError):
    """Caller is not in the pool (issuing invites / listing requires it)."""


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    was_seed_admin: bool


class OnboardingService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        signer: InviteSigner,
    ) -> None:
        self._sm = sessionmaker
        self._signer = signer

    async def register(
        self,
        *,
        tg_user_id: int,
        tg_username: str | None,
        invite_code: str | None,
        today: date,
    ) -> RegistrationResult:
        """Register the caller.

        - ``invite_code=None`` and empty pool → seed admin.
        - ``invite_code=None`` and non-empty pool → ``NotInPool`` (guest hint).
        - ``invite_code`` set → verify, then consume atomically.
        """
        async with transaction(self._sm) as session:
            if await repos.get_user(session, tg_user_id) is not None:
                raise AlreadyRegistered()

            if invite_code is None:
                if await repos.users_count(session) > 0:
                    raise NotInPool()
                user = await repos.add_user(
                    session,
                    tg_user_id=tg_user_id,
                    tg_username=tg_username,
                    is_admin=True,
                    invited_by_tg_id=None,
                )
                await repos.add_audit_event(
                    session,
                    actor_tg_id=tg_user_id,
                    action="user.register",
                    target="seed admin",
                )
                return RegistrationResult(user=user, was_seed_admin=True)

            inviter_tg_id = self._signer.verify(invite_code, today)
            if inviter_tg_id is None:
                raise InviteInvalid()
            if await repos.is_code_used(session, invite_code):
                raise InviteAlreadyUsed()

            user = await repos.add_user(
                session,
                tg_user_id=tg_user_id,
                tg_username=tg_username,
                is_admin=False,
                invited_by_tg_id=inviter_tg_id,
            )
            await repos.mark_code_used(
                session,
                code=invite_code,
                inviter_tg_id=inviter_tg_id,
                consumer_tg_id=tg_user_id,
            )
            await repos.add_audit_event(
                session,
                actor_tg_id=tg_user_id,
                action="user.register",
                target=f"по инвайту от {inviter_tg_id}",
            )
            return RegistrationResult(user=user, was_seed_admin=False)

    async def issue_invite(self, *, issuer_tg_id: int, today: date) -> str:
        async with transaction(self._sm) as session:
            if await repos.get_user(session, issuer_tg_id) is None:
                raise NotInPool()
            await repos.add_audit_event(
                session, actor_tg_id=issuer_tg_id, action="invite.issue", target=""
            )
        return self._signer.sign(issuer_tg_id, today)

    async def list_pool(self) -> list[User]:
        async with self._sm() as session:
            return await repos.list_pool(session)

    async def list_pool_in_scope(
        self, *, base_url: str, scope_path: str
    ) -> list[tuple[User, str]]:
        """Участники, подключённые к тому же (или вложенному) scope."""
        async with self._sm() as session:
            return await repos.list_pool_in_scope(
                session, base_url=base_url, scope_path=scope_path
            )
