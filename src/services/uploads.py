"""An uploaded file: its type by header and first bytes, within the settings' limits."""

from fastapi import Request

from src.services.errors import PlatformError
from src.tunables import tunable

SIGNATURES = {
    "image/png": lambda b: b.startswith(b"\x89PNG"),
    "image/jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
    "image/webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    "application/pdf": lambda b: b.startswith(b"%PDF"),
}


async def read_file(request: Request, limits: str = "media") -> tuple[bytes, str]:
    """The raw body: one of `{limits}.types` by header and first bytes, at most `{limits}.maxBytes`."""
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    kinds = tunable(f"{limits}.types")
    if kind not in kinds or kind not in SIGNATURES:
        raise PlatformError(f"Send {', '.join(kinds)}", 415)
    limit = tunable(f"{limits}.maxBytes")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise PlatformError(f"Files are at most {limit} bytes", 413)
    if not data or not SIGNATURES[kind](bytes(data)):
        raise PlatformError(f"Not a {kind} file", 415)
    return bytes(data), kind
