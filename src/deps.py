"""Composition root — wires the shared dependencies for the bot process.

Everything with a lifecycle (httpx pool, DB engine) is created once here
and passed into aiogram via ``dp.workflow_data`` so handlers receive it
by parameter name.
"""

from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import errors
from clients.gitlab import GitLabClient
from config import Settings
from crypto import PatCrypto
from db.session import make_engine, make_sessionmaker
from features.audit.router import router as audit_router
from features.audit.service import AuditService
from features.connections.router import router as connections_router
from features.connections.service import ConnectionsService
from features.help.router import router as help_router
from features.mrs.router import router as mrs_router
from features.mrs.service import MrsService, ProxyApprovalService
from features.onboarding.invites import InviteSigner
from features.onboarding.router import router as onboarding_router
from features.onboarding.service import OnboardingService
from features.pipelines.router import router as pipelines_router
from features.pipelines.service import PipelinesService
from features.releases.router import router as releases_router
from features.releases.service import ReleasesService
from features.tags.router import router as tags_router
from features.tags.service import TagsService
from shared.middlewares import UserMiddleware


@dataclass(slots=True)
class Deps:
    bot: Bot
    dp: Dispatcher
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    gitlab: GitLabClient
    crypto: PatCrypto


def build(settings: Settings) -> Deps:
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)

    gitlab = GitLabClient()
    crypto = PatCrypto(settings.fernet_key)
    invite_signer = InviteSigner(settings.invite_salt)
    onboarding = OnboardingService(sessionmaker=sessionmaker, signer=invite_signer)
    connections = ConnectionsService(
        sessionmaker=sessionmaker, gitlab=gitlab, crypto=crypto
    )
    pipelines = PipelinesService(gitlab=gitlab)
    mrs = MrsService(gitlab=gitlab)
    proxy = ProxyApprovalService(
        gitlab=gitlab, sessionmaker=sessionmaker, crypto=crypto
    )
    tags = TagsService(gitlab=gitlab)
    releases = ReleasesService(gitlab=gitlab)
    audit = AuditService(sessionmaker=sessionmaker)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data.update(
        {
            "sessionmaker": sessionmaker,
            "gitlab": gitlab,
            "crypto": crypto,
            "onboarding": onboarding,
            "connections": connections,
            "pipelines": pipelines,
            "mrs": mrs,
            "proxy": proxy,
            "tags": tags,
            "releases": releases,
            "audit": audit,
        }
    )
    dp.update.outer_middleware(UserMiddleware())
    dp.include_router(onboarding_router)
    dp.include_router(connections_router)
    dp.include_router(pipelines_router)
    dp.include_router(mrs_router)
    dp.include_router(tags_router)
    dp.include_router(releases_router)
    dp.include_router(audit_router)
    dp.include_router(help_router)
    errors.register(dp)
    return Deps(
        bot=bot,
        dp=dp,
        engine=engine,
        sessionmaker=sessionmaker,
        gitlab=gitlab,
        crypto=crypto,
    )


async def shutdown(deps: Deps) -> None:
    await deps.bot.session.close()
    await deps.gitlab.stop()
    await deps.engine.dispose()
