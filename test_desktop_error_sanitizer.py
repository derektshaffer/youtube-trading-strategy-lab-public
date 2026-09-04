from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

from desktop.trading_intelligence.error_sanitizer import clean_error


def _http_error(url: str, code: int, body: str) -> HTTPError:
    return HTTPError(url, code, "Unauthorized", {}, BytesIO(body.encode("utf-8")))


def test_clean_error_401_uses_sanitized_target_and_not_query() -> None:
    error = clean_error(
        _http_error(
            "https://api.tradier.com/v1/markets/quotes?token=hidden-token",
            401,
            "",
        )
    )
    assert "Tradier authentication failed." in error
    assert "token=" not in error.lower()


def test_clean_error_401_strips_html_and_keeps_auth_message() -> None:
    error = clean_error(
        _http_error(
            "https://api.tradier.com/v1/markets/quotes",
            401,
            "<html><body><h1>401 Unauthorized</h1><p>Missing API key</p></body></html>",
        )
    )
    assert "Tradier authentication failed." in error
    assert "Credentials are missing or not configured." in error
    assert "<html>" not in error.lower()
    assert "401 unauthorized" not in error.lower()
    assert "Check the connection credentials in Data Connections." in error


def test_clean_error_401_rejected_credentials_are_distinct() -> None:
    error = clean_error(
        _http_error(
            "https://paper-api.alpaca.markets/v2/positions",
            401,
            '<html><body>{"message":"Your key is invalid"}</body></html>',
        )
    )
    assert "Alpaca authentication failed." in error
    assert "Credentials were rejected." in error
    assert "<body>" not in error.lower()


def test_clean_error_403_local_service_keeps_debuggable_text() -> None:
    error = clean_error(_http_error("https://127.0.0.1:54321/health", 403, "<html>Oops</html>"))
    assert "Local service authentication failed." in error
    assert "<html>" not in error.lower()


def test_clean_error_non_auth_http_error_keeps_reason_without_html() -> None:
    error = clean_error(
        _http_error(
            "https://example.invalid",
            500,
            "<html><h1>Server Error</h1><p>Database offline</p></html>",
        )
    )
    assert error == "Server Error Database offline"
    assert "Server Error Database offline" in error
    assert "<html>" not in error.lower()
    assert "<p>" not in error.lower()


def test_clean_error_handles_non_http_html_fragments() -> None:
    error = clean_error(
        RuntimeError("<html><body><h1>Bad</h1> token=abc</body></html>")
    )
    assert "Bad token=[redacted]" in error
    assert "<html>" not in error.lower()
    assert "<h1>" not in error.lower()


def test_clean_error_401_message_without_http_error_maps_to_authentication_notice() -> None:
    error = clean_error(RuntimeError("401 Unauthorized from https://paper-api.alpaca.markets/v2/account"))
    assert error == "Alpaca authentication failed. Credentials were rejected. Check the connection credentials in Data Connections."


def test_clean_error_redacts_secret_like_fields() -> None:
    error = clean_error(
        _http_error("https://api.example.com/v1/test", 401, "token=super-secret-token")
    )
    assert "super-secret-token" not in error
    assert "Authentication failed." in error
