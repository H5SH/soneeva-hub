"""Ed25519 keypairs for calling applications.

The private half is returned to the caller once and never stored. Rotation retires existing
keys rather than deleting them, so tokens already in flight keep verifying until they expire.
"""

from datetime import UTC, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.models.auth import Application, KeyStatus, PublicKey


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


def rotate(application: Application) -> tuple[Application, str, str]:
    """Add a fresh key and retire the rest. Returns (application, kid, private PEM)."""
    private_pem, public_pem = generate()
    kid = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    for key in application.keys:
        key.status = KeyStatus.RETIRING
    application.keys.append(PublicKey(kid=kid, public_key=public_pem))
    return application, kid, private_pem


def env_block(application_id: str, kid: str, private_pem: str) -> str:
    """Exactly what goes in the calling app's .env, ready to copy."""
    escaped = private_pem.replace("\n", "\\n")
    return (
        f"PAYMENT_APPLICATION_ID={application_id}\n"
        f"PAYMENT_KEY_ID={kid}\n"
        f'PAYMENT_PRIVATE_KEY="{escaped}"'
    )
