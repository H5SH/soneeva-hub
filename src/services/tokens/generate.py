"""Ed25519 keypairs for calling applications.

The private half is returned to the caller once and never stored. Rotation retires existing
keys rather than deleting them, so tokens already in flight keep verifying until they expire.
"""

from datetime import UTC, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.models.auth import KeyStatus, PublicKey


def generate() -> tuple[str, str]:
    """Returns (private PKCS8 PEM, public SPKI PEM)."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def rotate(keys: list[PublicKey]) -> tuple[list[PublicKey], str, str]:
    """A fresh key, the rest retired. Returns (keys, kid, private PEM)."""
    private_pem, public_pem = generate()
    kid = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    retired = [k.model_copy(update={"status": KeyStatus.RETIRING}) for k in keys]
    return [*retired, PublicKey(kid=kid, public_key=public_pem)], kid, private_pem


def subject_key(keys: list[PublicKey], subject: str) -> tuple[list[PublicKey], str, str]:
    """A key that only signs for `subject`, replacing that subject's previous one; others untouched."""
    private_pem, public_pem = generate()
    kid = f"sub-{subject}"
    kept = [k for k in keys if k.kid != kid]
    return [*kept, PublicKey(kid=kid, public_key=public_pem, subject=subject)], kid, private_pem


def env_block(application_id: str, kid: str, private_pem: str) -> str:
    """Exactly what goes in the calling app's .env, ready to copy."""
    escaped = private_pem.replace("\n", "\\n")
    return (
        f"PAYMENT_APPLICATION_ID={application_id}\n"
        f"PAYMENT_KEY_ID={kid}\n"
        f'PAYMENT_PRIVATE_KEY="{escaped}"'
    )


def hub_env_block(application_id: str, kid: str, private_pem: str) -> str:
    """The same key under the names our Next apps (console, admin) read."""
    escaped = private_pem.replace("\n", "\\n")
    return f'HUB_APP_ID={application_id}\nHUB_KEY_ID={kid}\nHUB_PRIVATE_KEY="{escaped}"'
