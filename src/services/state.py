"""`app.state` as what it is: main.py's `attach` puts each of the `Services` on it, under the same name."""

from typing import TYPE_CHECKING, cast

from fastapi import Request

if TYPE_CHECKING:
    from src.wiring import Services


def services_of(request: Request) -> "Services":
    return cast("Services", request.app.state)
