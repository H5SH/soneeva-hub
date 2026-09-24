import pytest

from app.models.app import AppConfig, PlanConfig

PLANS = [PlanConfig(code="free", name="Free")]


def app(**kwargs) -> AppConfig:
    return AppConfig(id="softbox", name="Softbox", plans=PLANS, **kwargs)


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/steal",
        "http://evil.example",
        "//evil.example/path",
        "javascript:alert(1)",
        "https://allowed.example.evil.com",
    ],
)
def test_redirects_outside_the_allow_list_are_discarded(candidate):
    """`/return` and `/cancel` are unauthenticated, so an unchecked redirect is an open redirector."""
    config = app(return_url="https://allowed.example/app", allowed_return_hosts=["allowed.example"])
    assert config.resolve_return_url(candidate) == "https://allowed.example/app"


def test_allow_listed_host_passes_through():
    config = app(return_url="https://allowed.example/", allowed_return_hosts=["allowed.example"])
    assert (
        config.resolve_return_url("https://allowed.example/settings")
        == "https://allowed.example/settings"
    )


def test_relative_path_is_kept():
    config = app(return_url="https://allowed.example/")
    assert config.resolve_return_url("/settings/billing") == "/settings/billing"


def test_missing_candidate_falls_back():
    config = app(return_url="https://allowed.example/app")
    assert config.resolve_return_url(None) == "https://allowed.example/app"


def test_no_configuration_at_all_is_still_safe():
    assert app().resolve_return_url("https://evil.example") == "/"
