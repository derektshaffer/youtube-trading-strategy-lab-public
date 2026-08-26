"""Paper-only Alpaca execution helpers for YouTube Trading Strategy Lab.

The base URL is intentionally hard-coded to Alpaca's paper environment.
This module cannot submit live brokerage orders.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"


class PaperTradeError(RuntimeError):
    """Actionable paper-trading error suitable for displaying in Streamlit."""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


class AlpacaPaperTrader:
    """Small dependency-free client for Alpaca's paper Trading API."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        self.api_key = str(api_key or "").strip()
        self.secret_key = str(secret_key or "").strip()
        if not self.api_key or not self.secret_key:
            raise PaperTradeError("Alpaca paper-trading API credentials are missing.")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> Any:
        query_string = ""
        if params:
            cleaned = {key: value for key, value in params.items() if value is not None}
            if cleaned:
                query_string = "?" + urlencode(cleaned)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            PAPER_TRADING_URL + path + query_string,
            data=body,
            headers=self.headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace").strip()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                decoded = json.loads(raw)
                message = decoded.get("message") or decoded.get("error") or raw[:350]
            except (json.JSONDecodeError, AttributeError):
                message = raw[:350]
            if exc.code in {401, 403}:
                raise PaperTradeError(
                    f"Alpaca paper trading denied the request ({exc.code}). "
                    "Make sure the credentials belong to your Alpaca paper account."
                ) from exc
            if exc.code == 429:
                raise PaperTradeError("Alpaca paper trading rate limit reached. Try again shortly.") from exc
            raise PaperTradeError(f"Alpaca paper trading request failed ({exc.code}): {message}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PaperTradeError(f"Could not reach Alpaca paper trading: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise PaperTradeError("Alpaca paper trading returned unreadable data.") from exc

    def account(self) -> dict[str, Any]:
        result = self._request("/v2/account")
        return result if isinstance(result, dict) else {}

    def clock(self) -> dict[str, Any]:
        result = self._request("/v2/clock")
        return result if isinstance(result, dict) else {}

    def positions(self) -> list[dict[str, Any]]:
        result = self._request("/v2/positions")
        return result if isinstance(result, list) else []

    def orders(
        self,
        *,
        status: str = "all",
        after: str | None = None,
        limit: int = 100,
        nested: bool = True,
    ) -> list[dict[str, Any]]:
        result = self._request(
            "/v2/orders",
            params={
                "status": status,
                "after": after,
                "limit": max(1, min(500, int(limit))),
                "direction": "desc",
                "nested": "true" if nested else "false",
            },
        )
        return result if isinstance(result, list) else []

    def submit_bracket_market_order(
        self,
        *,
        symbol: str,
        qty: int,
        stop_price: float,
        target_price: float,
        client_order_id: str,
    ) -> dict[str, Any]:
        clean_symbol = str(symbol or "").strip().upper()
        clean_qty = int(qty)
        stop = round(float(stop_price), 4)
        target = round(float(target_price), 4)
        if not clean_symbol:
            raise PaperTradeError("A ticker is required.")
        if clean_qty < 1:
            raise PaperTradeError("Paper order size must be at least one share.")
        if stop <= 0 or target <= 0 or target <= stop:
            raise PaperTradeError("The bracket order needs a valid stop below a valid profit target.")
        payload = {
            "symbol": clean_symbol,
            "qty": str(clean_qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{target:.4f}"},
            "stop_loss": {"stop_price": f"{stop:.4f}"},
            "client_order_id": str(client_order_id)[:128],
        }
        result = self._request("/v2/orders", method="POST", payload=payload)
        if not isinstance(result, dict) or not result.get("id"):
            raise PaperTradeError("Alpaca did not return a valid paper order.")
        return result

    def cancel_all_orders(self) -> Any:
        return self._request("/v2/orders", method="DELETE")

    def close_position(self, symbol: str) -> dict[str, Any]:
        clean_symbol = str(symbol or "").strip().upper()
        if not clean_symbol:
            raise PaperTradeError("Choose a symbol to close.")
        result = self._request(f"/v2/positions/{quote(clean_symbol, safe='')}", method="DELETE")
        return result if isinstance(result, dict) else {}


def daily_account_pnl(account: dict[str, Any]) -> float:
    equity = _as_float(account.get("equity"))
    last_equity = _as_float(account.get("last_equity"))
    return equity - last_equity


def position_size_from_risk(
    *,
    equity: float,
    price: float,
    stop_price: float,
    risk_per_trade_pct: float,
    max_position_pct: float,
    max_position_dollars: float | None = None,
) -> int:
    """Return whole-share size respecting both loss-at-stop and position-value caps."""
    equity_value = max(0.0, float(equity))
    entry = max(0.0, float(price))
    stop = max(0.0, float(stop_price))
    risk_pct = max(0.0, float(risk_per_trade_pct))
    position_pct = max(0.0, float(max_position_pct))
    if equity_value <= 0 or entry <= 0 or stop <= 0 or stop >= entry:
        return 0

    risk_per_share = entry - stop
    risk_budget = equity_value * risk_pct / 100.0
    position_budget = equity_value * position_pct / 100.0
    if max_position_dollars is not None and float(max_position_dollars) > 0:
        position_budget = min(position_budget, float(max_position_dollars))

    by_risk = math.floor(risk_budget / risk_per_share) if risk_budget > 0 else 0
    by_value = math.floor(position_budget / entry) if position_budget > 0 else 0
    return max(0, min(by_risk, by_value))
