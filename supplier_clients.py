"""Safe, server-side clients for CouponShops, Acczone and EliteToolz.

Setup
-----
1. Install the dependency:  python -m pip install requests
2. Set these environment variables on the bot/server (never in Telegram messages,
   source code, browser code, or Git):
       COUPONSHOPS_API_KEY=...
       ACCZONE_API_KEY=...
       ELITE_API_KEY=...
3. Import this module only from trusted server-side code.

Purchase safety
---------------
* Read-only calls can be made normally.
* Every purchase method requires confirm_purchase=True as an explicit guard.
* No request is automatically retried. This is important because a timeout does
  not prove a purchase failed, and EliteToolz has no documented idempotency key.
* CouponShops requires a unique client_order_id. Save it with your local order.
* Acczone documents purchase as GET /buyCpn; treat it as a money-moving action.
* Never log request URLs, headers, API keys, raw responses, or delivery values.

This module does not set prices or profit. Calculate/store your customer price,
supplier charge, and profit in your own order/database layer.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

import requests


class SupplierAPIError(RuntimeError):
    """Sanitized provider error; deliberately excludes URLs, keys and response bodies."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{provider}: {message}{suffix}")


class _BaseClient:
    PROVIDER = "Supplier"
    BASE_URL = ""
    API_KEY_ENV = ""
    TIMEOUT = (5, 25)  # connect timeout, read timeout

    def __init__(self, api_key: str | None = None, *, timeout: tuple[float, float] | None = None):
        key = api_key if api_key is not None else os.getenv(self.API_KEY_ENV)
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Set {self.API_KEY_ENV} in the server environment first")
        self._api_key = key.strip()
        self.timeout = timeout or self.TIMEOUT
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json", "User-Agent": "supplier-client/1.0"})

    def close(self) -> None:
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        # Provider paths are constants in this module, not caller-supplied URLs.
        url = self.BASE_URL.rstrip("/") + "/" + path.lstrip("/")
        try:
            response = self._session.request(
                method=method,
                url=url,
                headers=dict(headers or {}),
                params=dict(params or {}),
                json=json,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            # Suppress the original exception: it can contain a URL/query string.
            raise SupplierAPIError(self.PROVIDER, "network/timeout error; check safely, then reconcile before retrying") from None

        if not 200 <= response.status_code < 300:
            # Do not include response text; providers may echo sensitive request data.
            raise SupplierAPIError(self.PROVIDER, "request rejected; inspect provider dashboard/support", response.status_code)
        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, ValueError):
            raise SupplierAPIError(self.PROVIDER, "response was not valid JSON", response.status_code) from None


class CouponShopsClient(_BaseClient):
    """CouponShops API: X-API-Key, /services, /balance, /orders."""

    PROVIDER = "CouponShops"
    BASE_URL = "https://api.couponshops.org/api/v1"
    API_KEY_ENV = "COUPONSHOPS_API_KEY"

    def _call(self, method: str, path: str, *, json: Mapping[str, Any] | None = None) -> Any:
        return self._request(method, path, headers={"X-API-Key": self._api_key}, json=json)

    def services(self) -> Any:
        return self._call("GET", "/services")

    def balance(self) -> Any:
        return self._call("GET", "/balance")

    def create_order(
        self,
        service_id: str,
        quantity: int,
        client_order_id: str,
        *,
        confirm_purchase: bool = False,
    ) -> Any:
        if confirm_purchase is not True:
            raise ValueError("Set confirm_purchase=True only after validating payment, price and stock")
        if not isinstance(service_id, str) or not service_id.strip():
            raise ValueError("service_id must be a non-empty string")
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise ValueError("quantity must be an integer from 1 to 100")
        if not isinstance(client_order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", client_order_id):
            raise ValueError("client_order_id must be 1-80 letters, digits, underscores or hyphens")
        return self._call("POST", "/orders", json={
            "service_id": service_id.strip(),
            "quantity": quantity,
            "client_order_id": client_order_id,
        })

    def get_order(self, order_id: str) -> Any:
        if not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", order_id):
            raise ValueError("order_id format is invalid")
        return self._call("GET", f"/orders/{order_id}")


class AcczoneClient(_BaseClient):
    """Acczone Activation API. Its documented auth uses an apikey query parameter."""

    PROVIDER = "Acczone"
    BASE_URL = "https://api.acczone.xyz"
    API_KEY_ENV = "ACCZONE_API_KEY"

    def _call(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        query = {"apikey": self._api_key}
        query.update(params or {})
        return self._request("GET", path, params=query)

    def services(self) -> Any:
        return self._call("/getServices")

    def balance(self) -> Any:
        return self._call("/getBalance")

    def history(self, *, page: int = 1, limit: int = 20) -> Any:
        if type(page) is not int or page < 1:
            raise ValueError("page must be an integer >= 1")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        return self._call("/getHistory", params={"page": page, "limit": limit})

    def buy_activation(
        self,
        service_key: str,
        quantity: int = 1,
        *,
        confirm_purchase: bool = False,
    ) -> Any:
        if confirm_purchase is not True:
            raise ValueError("Set confirm_purchase=True only after validating payment, price and stock")
        if not isinstance(service_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", service_key):
            raise ValueError("service_key format is invalid")
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise ValueError("quantity must be an integer from 1 to 100")
        # Docs recommend quantity=1 because stock can sell out. No automatic retry.
        return self._call("/buyCpn", params={"service_key": service_key, "quantity": quantity})


class EliteToolzClient(_BaseClient):
    """EliteToolz API: X-API-Key, /api/products, /api/balance, /api/order."""

    PROVIDER = "EliteToolz"
    BASE_URL = "https://api.elitetoolz.store/api"
    API_KEY_ENV = "ELITE_API_KEY"

    def _call(self, method: str, path: str, *, json: Mapping[str, Any] | None = None) -> Any:
        return self._request(
            method,
            path,
            headers={"X-API-Key": self._api_key},
            json=json,
        )

    def products(self) -> Any:
        return self._call("GET", "/products")

    def balance(self) -> Any:
        return self._call("GET", "/balance")

    def create_order(
        self,
        product_id: str,
        quantity: int,
        *,
        confirm_purchase: bool = False,
    ) -> Any:
        if confirm_purchase is not True:
            raise ValueError("Set confirm_purchase=True only after validating payment, price and stock")
        if not isinstance(product_id, str) or not product_id.strip():
            raise ValueError("product_id must be a non-empty string")
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise ValueError("quantity must be an integer from 1 to 100")
        # No idempotency key is supported by the docs; this POST is never retried.
        return self._call("POST", "/order", json={"productId": product_id.strip(), "quantity": quantity})

# ══════════════════════════════════════════════════
#  Async helpers used by the Telegram bot (main.py)
# ══════════════════════════════════════════════════
import asyncio

PROVIDERS = {
    "couponshops": CouponShopsClient,
    "acczone": AcczoneClient,
    "elitetoolz": EliteToolzClient,
}
PROVIDER_LABELS = {
    "couponshops": "CouponShops",
    "acczone": "Acczone",
    "elitetoolz": "EliteToolz",
}


def key_configured(provider: str) -> bool:
    cls = PROVIDERS[provider]
    return bool((os.getenv(cls.API_KEY_ENV) or "").strip())


def _client(provider: str):
    if provider not in PROVIDERS:
        raise ValueError("unknown provider")
    return PROVIDERS[provider]()


def _balance_sync(provider: str):
    with _client(provider) as c:
        return c.balance()


def _catalog_sync(provider: str):
    with _client(provider) as c:
        return c.products() if provider == "elitetoolz" else c.services()


def _order_sync(provider: str, product_id: str, quantity: int, client_order_id: str):
    with _client(provider) as c:
        if provider == "couponshops":
            return c.create_order(product_id, quantity, client_order_id, confirm_purchase=True)
        if provider == "acczone":
            return c.buy_activation(product_id, quantity, confirm_purchase=True)
        return c.create_order(product_id, quantity, confirm_purchase=True)


async def fetch_balance(provider: str):
    return await asyncio.to_thread(_balance_sync, provider)


async def fetch_catalog(provider: str):
    return await asyncio.to_thread(_catalog_sync, provider)


async def place_order(provider: str, product_id: str, quantity: int, client_order_id: str):
    """Money-moving call. Never retried automatically."""
    return await asyncio.to_thread(_order_sync, provider, product_id, quantity, client_order_id)
