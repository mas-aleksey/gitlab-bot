from cryptography.fernet import Fernet


class PatCrypto:
    """Fernet-based PAT encryption. One instance per app, initialized from FERNET_KEY."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, pat: str) -> bytes:
        return self._fernet.encrypt(pat.encode())

    def decrypt(self, blob: bytes) -> str:
        return self._fernet.decrypt(blob).decode()
