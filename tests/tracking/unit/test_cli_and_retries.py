from data_sources.tracking.cli import main
from data_sources.tracking.config import RetryConfig
from data_sources.tracking.exceptions import AuthenticationError, RateLimitError
from data_sources.tracking.retries import classify_http_error, retry_delay


def test_cli_help_and_doctor(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "doctor" in out
    assert "daily" in out
    assert "backfill" in out

    code = main(["doctor", "--json"])
    assert code == 0
    payload = capsys.readouterr().out
    assert "config_fingerprint" in payload
    assert "serper_api_key" not in payload


def test_retry_does_not_retry_auth():
    err = classify_http_error(401, "invalid api key")
    assert isinstance(err, AuthenticationError)
    assert err.retryable is False
    rate = classify_http_error(429, "slow down", retry_after=7)
    assert isinstance(rate, RateLimitError)
    assert rate.retryable is True
    delay = retry_delay(1, RetryConfig(jitter=False), retry_after=7)
    assert delay == 7
