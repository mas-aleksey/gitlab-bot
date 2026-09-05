"""Stateless HMAC-signed invite codes.

Format: ``{inviter_tg_id}-{sig12}`` where ``sig12`` is
``HMAC-SHA256(salt, "{inviter_tg_id}:{today_iso}")`` truncated to 9 bytes
and url-safe-base64 encoded without padding (12 chars).

The code is valid only on the calendar day (UTC) it was issued. Anti-replay
within that day is enforced by inserting into ``used_invite_codes`` in the
same transaction as the user, so the same code can be redeemed at most
once — see :meth:`OnboardingService.register`.
"""

import base64
import hashlib
import hmac
from datetime import date

_SIG_BYTES = 9


class InviteSigner:
    __slots__ = ("_salt",)

    def __init__(self, salt: str) -> None:
        if not salt:
            raise ValueError("invite salt must be non-empty")
        self._salt = salt.encode()

    def sign(self, inviter_tg_id: int, today: date) -> str:
        return f"{inviter_tg_id}-{self._sig(inviter_tg_id, today)}"

    def verify(self, code: str, today: date) -> int | None:
        """Return inviter tg_user_id if the signature matches today, else ``None``."""
        prefix, sep, sig = code.partition("-")
        if not sep or not prefix or not sig:
            return None
        try:
            inviter_tg_id = int(prefix)
        except ValueError:
            return None
        expected = self._sig(inviter_tg_id, today)
        if hmac.compare_digest(sig, expected):
            return inviter_tg_id
        return None

    def _sig(self, inviter_tg_id: int, today: date) -> str:
        msg = f"{inviter_tg_id}:{today.isoformat()}".encode()
        digest = hmac.new(self._salt, msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest[:_SIG_BYTES]).rstrip(b"=").decode()
