"""Alpaca REST client shared by the broker and market-data adapters (F051, OD-3, OD-4).

One key pair and one environment per process: paper or live, never both (F051 Security).
Failures are classified by whether the request can have taken effect, which is what the
broker port needs (FR-22): refused-before-sending, refused, or outcome unknown.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx

from app.instruments import Instrument, OptionContract, Stock

DATA_URL = "https://data.alpaca.markets"


class AlpacaError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AlpacaNotSent(AlpacaError):
    """The request cannot have taken effect: no connection, bad credentials, rate limited."""


class AlpacaRefused(AlpacaError):
    """Alpaca read the request and refused it (4xx): invalid, duplicate, insufficient funds."""


class AlpacaUnknown(AlpacaError):
    """Timeout or 5xx after the request left: it may or may not have taken effect."""


class AlpacaApi:
    def __init__(
        self,
        key: str,
        secret: str,
        endpoint: str = "https://paper-api.alpaca.markets",
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        host = urlparse(endpoint).netloc  # the usual ".../v2" suffix is not part of the host
        self.stream_url = f"wss://{host}/stream"
        self._credentials = {"key": key, "secret": secret}
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self._trading = httpx.AsyncClient(
            base_url=f"https://{host}", headers=headers, timeout=timeout_s, transport=transport
        )
        self._data = httpx.AsyncClient(
            base_url=DATA_URL, headers=headers, timeout=timeout_s, transport=transport
        )

    def stream_auth(self) -> dict[str, Any]:
        """First WebSocket message. The secret leaves this object only inside it."""
        return {"action": "auth", **self._credentials}

    async def get(self, path: str, *, data: bool = False, **params: Any) -> Any:
        return await self._call(self._data if data else self._trading, "GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        return await self._call(self._trading, "POST", path, json=body)

    async def delete(self, path: str) -> Any:
        return await self._call(self._trading, "DELETE", path)

    async def aclose(self) -> None:
        await self._trading.aclose()
        await self._data.aclose()

    async def _call(self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
        if "params" in kwargs:
            kwargs["params"] = {k: _text(v) for k, v in kwargs["params"].items() if v is not None}
        try:
            response = await client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise AlpacaNotSent(f"{type(exc).__name__}: {exc}") from exc
        except httpx.TransportError as exc:  # read/write timeout, reset: the request left
            raise AlpacaUnknown(f"{type(exc).__name__}: {exc}") from exc
        status = response.status_code
        reason = _reason(response)
        if status >= 500:
            raise AlpacaUnknown(f"HTTP {status}: {reason}", status)
        # 403 is both "bad credentials" and "insufficient buying power": only the second is
        # a refusal of an order; the first never reached the account.
        if status in (401, 429) or (status == 403 and "insufficient" not in reason.lower()):
            raise AlpacaNotSent(f"HTTP {status}: {reason}", status)
        if status >= 400:
            raise AlpacaRefused(reason, status)
        return response.json() if response.content else {}


def _text(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _reason(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or "no response body"
    return str(body.get("message", body)) if isinstance(body, dict) else str(body)[:200]


_OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def alpaca_symbol(instrument: Instrument) -> str:
    """Alpaca writes the OCC symbol without the root padding."""
    if isinstance(instrument, OptionContract):
        return instrument.occ_symbol().replace(" ", "")
    return instrument.symbol


def instrument_of(symbol: str) -> Instrument:
    match = _OCC.match(symbol)
    if match is None:
        return Stock(symbol=symbol)
    root, day, right, strike = match.groups()
    return OptionContract(
        underlying=root,
        expiry=datetime.strptime(day, "%y%m%d").date(),
        strike=Decimal(strike) / 1000,
        right="call" if right == "C" else "put",
    )


def dec(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))
