from tgcli.errors import (
    ConfigError,
    NotFoundError,
    PolicyError,
    RateLimitError,
    TgcliError,
)


def test_exit_codes_match_contract():
    assert TgcliError("x").exit_code == 1
    assert PolicyError("x").exit_code == 2
    assert ConfigError("x").exit_code == 3
    assert NotFoundError("x").exit_code == 4
    assert RateLimitError("x").exit_code == 5


def test_error_carries_code_and_details():
    err = RateLimitError("flood", retry_after=42)
    assert err.code == "FLOOD_WAIT"
    assert err.details == {"retry_after": 42}
    assert str(err) == "flood"
