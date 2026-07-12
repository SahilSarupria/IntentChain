"""
IntentChain — Native Token Price Feed

Converts gas quotes (gwei) into an approximate USD cost so a "cheapest" vs
"fastest" choice is meaningful to a human, not just abstract gwei numbers.

Uses CoinGecko's public simple-price endpoint (no API key required). Fails
soft: if the network call errors or the process is offline, callers just get
`None` back and omit the USD figure rather than crash.
"""
import time
import requests

_SYMBOL_TO_ID = {
    "ETH": "ethereum",
    "MATIC": "matic-network",
    "BNB": "binancecoin",
}

_CACHE_TTL_SECONDS = 45
_cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, price_usd)


def get_native_usd_price(symbol: str) -> float | None:
    symbol = (symbol or "ETH").upper()
    now = time.time()

    cached = _cache.get(symbol)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    coingecko_id = _SYMBOL_TO_ID.get(symbol)
    if not coingecko_id:
        return None

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coingecko_id, "vs_currencies": "usd"},
            timeout=4,
        )
        resp.raise_for_status()
        price = resp.json()[coingecko_id]["usd"]
        _cache[symbol] = (now, price)
        return price
    except Exception:
        # stale cache is better than nothing if we have one, else give up quietly
        return cached[1] if cached else None


def estimate_usd_cost(gas_limit: int, max_fee_gwei: float, native_symbol: str) -> float | None:
    price = get_native_usd_price(native_symbol)
    if price is None:
        return None
    native_amount = (gas_limit * max_fee_gwei) / 1e9
    return round(native_amount * price, 4)
