"""initial schema

Revision ID: e37a50adb5dd
Revises:
Create Date: 2026-07-22 08:49:01.736831

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e37a50adb5dd'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('tg_user_id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('tg_username', sa.String(), nullable=True),
        sa.Column('invited_by_tg_id', sa.BigInteger(), nullable=True),
        sa.Column('is_admin', sa.Boolean(), nullable=False),
        sa.Column('default_connection_id', sa.BigInteger(), nullable=True),
        sa.Column('registered_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['invited_by_tg_id'], ['users.tg_user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('tg_user_id'),
    )

    op.create_table(
        'connections',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('owner_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('base_url', sa.String(), nullable=False),
        sa.Column('scope_path', sa.String(), nullable=False),
        sa.Column('scope_kind', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('encrypted_pat', sa.LargeBinary(), nullable=False),
        sa.Column('gitlab_user_id', sa.BigInteger(), nullable=False),
        sa.Column('gitlab_username', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint("provider = 'gitlab'", name='ck_connection_provider'),
        sa.CheckConstraint("scope_kind IN ('group', 'project')", name='ck_connection_scope_kind'),
        sa.ForeignKeyConstraint(['owner_tg_id'], ['users.tg_user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_tg_id', 'base_url', 'scope_path', name='uq_connection_scope'),
    )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_foreign_key(
            'fk_users_default_connection_id',
            'connections',
            ['default_connection_id'], ['id'],
            ondelete='SET NULL',
        )

    op.create_table(
        'approval_events',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('actor_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('on_behalf_of_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('connection_id', sa.BigInteger(), nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('mr_iid', sa.Integer(), nullable=False),
        sa.Column('sha', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint("action IN ('approved', 'unapproved')", name='ck_approval_action'),
        sa.ForeignKeyConstraint(['actor_tg_id'], ['users.tg_user_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['connection_id'], ['connections.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['on_behalf_of_tg_id'], ['users.tg_user_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_approval_events_mr', 'approval_events',
        ['connection_id', 'project_id', 'mr_iid'], unique=False,
    )

    op.create_table(
        'used_invite_codes',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('inviter_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('consumer_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['consumer_tg_id'], ['users.tg_user_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inviter_tg_id'], ['users.tg_user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_used_invite_codes_code', 'used_invite_codes', ['code'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_used_invite_codes_code', table_name='used_invite_codes')
    op.drop_table('used_invite_codes')
    op.drop_index('ix_approval_events_mr', table_name='approval_events')
    op.drop_table('approval_events')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_default_connection_id', type_='foreignkey')
    op.drop_table('connections')
    op.drop_table('users')
