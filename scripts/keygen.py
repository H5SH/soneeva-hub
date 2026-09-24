"""Generate an Ed25519 keypair for a calling application.

    uv run python scripts/keygen.py softbox

Writes the public half to applications/{id}.keys[] in Firestore and prints the private half
once. The private key is never stored here — that is the whole point of the scheme.
"""

import asyncio
import sys
from datetime import UTC, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv

# Explicit: the app loads .env as a side effect of importing app.config, which this script has
# no reason to touch. Firestore needs GOOGLE_APPLICATION_CREDENTIALS before the first import.
load_dotenv()

from app.models.auth import Application, ApplicationStatus, KeyStatus, PublicKey  # noqa: E402
from app.models.db import Collection  # noqa: E402
from app.services.db import DB  # noqa: E402


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


async def main(application_id: str) -> None:
    db = DB()
    private_pem, public_pem = generate()
    kid = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    row = await db.get_row(Collection.APPLICATIONS, application_id)
    application = (
        Application(**(row | {"id": application_id}))
        if row
        else Application(id=application_id, name=application_id, status=ApplicationStatus.ACTIVE)
    )

    # Existing keys are retired rather than dropped, so tokens already in flight keep verifying
    # until they expire. Remove them on a later run once nothing is signing with them.
    for key in application.keys:
        key.status = KeyStatus.RETIRING
    application.keys.append(PublicKey(kid=kid, public_key=public_pem))

    await db.set_row(
        Collection.APPLICATIONS,
        application_id,
        application.model_dump(mode="json", exclude={"id"}),
    )

    print(f"\napplication : {application_id}")
    print(f"kid         : {kid}")
    print(f"public key  : stored in applications/{application_id}")
    print("\nPut these in the calling app's env. The private key is not stored and cannot be")
    print("recovered — rerun this script to issue a new one.\n")
    print(f"PAYMENT_APPLICATION_ID={application_id}")
    print(f"PAYMENT_KEY_ID={kid}")
    print(f'PAYMENT_PRIVATE_KEY="{private_pem.replace(chr(10), chr(92) + "n")}"')


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/keygen.py <application_id>")
    asyncio.run(main(sys.argv[1]))
