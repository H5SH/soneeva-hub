"""One instance of each Stripe-backed service per mode, so gateways and caches never mix live and test."""

from collections.abc import Callable

from src.models.mode import Mode
from src.services.errors import PlatformError


class ByMode[T]:
    """`test` is None until STRIPE_TEST_SECRET_KEY is set; asking for it then is a 503."""

    def __init__(self, live: T, test: T | None = None) -> None:
        self._live = live
        self._test = test

    def __getitem__(self, mode: Mode) -> T:
        if mode is Mode.LIVE:
            return self._live
        if self._test is None:
            raise PlatformError("Stripe test mode is off: set STRIPE_TEST_SECRET_KEY", 503)
        return self._test

    @property
    def live(self) -> T:
        return self._live

    def map[U](self, build: Callable[[T, Mode], U]) -> "ByMode[U]":
        test = None if self._test is None else build(self._test, Mode.TEST)
        return ByMode(build(self._live, Mode.LIVE), test)

    def modes(self) -> list[Mode]:
        return [Mode.LIVE] if self._test is None else [Mode.LIVE, Mode.TEST]
