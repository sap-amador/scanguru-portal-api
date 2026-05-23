"""PHI encryption / decryption via Fernet (symmetric, AES-128-CBC + HMAC-SHA256)."""
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.phi_encryption_key:
            raise RuntimeError(
                "PHI_ENCRYPTION_KEY not set. Generate one with:\n"
                "  python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        _fernet = Fernet(settings.phi_encryption_key.encode())
    return _fernet


def encrypt(value: str | None) -> bytes | None:
    """Encrypt a string for storage. None / empty returns None."""
    if value is None or value == "":
        return None
    return _get_fernet().encrypt(value.encode("utf-8"))


def decrypt(value: bytes | memoryview | None) -> str | None:
    """Decrypt bytes back to string. Returns None on missing or invalid input."""
    if not value:
        return None
    try:
        return _get_fernet().decrypt(bytes(value)).decode("utf-8")
    except InvalidToken:
        return None
