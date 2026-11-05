"""The console's sealed store tokens (lib/stores/connection.ts): `iv.tag.ciphertext`, each base64url."""

import base64
import binascii

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.stores.base import StoreBillingError

KEY_BYTES = 32


def _bytes(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def check_key(key: str) -> None:
    """Raises unless `key` is what the console seals with, so a bad one stops a deploy, not every charge."""
    try:
        valid = len(base64.b64decode(key, validate=True)) == KEY_BYTES
    except binascii.Error:
        valid = False
    if not valid:
        raise ValueError(f"CONNECTIONS_KEY must be {KEY_BYTES} bytes, base64")


def unseal(sealed: str, key: str) -> str:
    try:
        iv, tag, text = (_bytes(part) for part in sealed.split("."))
        return AESGCM(base64.b64decode(key)).decrypt(iv, text + tag, None).decode()
    except (ValueError, InvalidTag) as error:
        raise StoreBillingError("The store's token can't be read with CONNECTIONS_KEY") from error
