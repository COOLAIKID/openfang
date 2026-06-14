"""Finance toolkit — prices, FX, and lightweight market math.

Free, key-less sources: CoinGecko for crypto, exchangerate.host for fiat FX.
Includes a few pure-Python indicators (SMA, RSI, percentage change) the market
team can use to reason about trends without external TA libraries.
"""
from __future__ import annotations

import json

import requests

UA = {"User-Agent": "AutoEarn/1.0"}


def crypto_prices(symbols: str = "bitcoin,ethereum") -> str:
    """CoinGecko spot prices + 24h change for comma-separated coin ids."""
    ids = ",".join(s.strip().lower() for s in symbols.split(","))
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
        headers=UA,
        timeout=30,
    )
    resp.raise_for_status()
    return json.dumps(resp.json())


def crypto_market_chart(coin: str = "bitcoin", days: int = 7) -> list[float]:
    """Daily closing prices for a coin over N days."""
    resp = requests.get(
        f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
        params={"vs_currency": "usd", "days": days, "interval": "daily"},
        headers=UA,
        timeout=30,
    )
    resp.raise_for_status()
    return [p[1] for p in resp.json().get("prices", [])]


def fx_rate(base: str = "USD", quote: str = "EUR") -> str:
    """Current foreign-exchange rate between two fiat currencies."""
    resp = requests.get(
        "https://api.exchangerate.host/latest",
        params={"base": base.upper(), "symbols": quote.upper()},
        timeout=30,
    )
    resp.raise_for_status()
    rate = resp.json().get("rates", {}).get(quote.upper())
    return f"1 {base.upper()} = {rate} {quote.upper()}"


# --- pure-python indicators ------------------------------------------------
def pct_change(series: list[float]) -> float:
    if len(series) < 2 or series[0] == 0:
        return 0.0
    return round((series[-1] - series[0]) / series[0] * 100.0, 2)


def sma(series: list[float], window: int) -> float:
    if len(series) < window or window <= 0:
        return 0.0
    return round(sum(series[-window:]) / window, 4)


def rsi(series: list[float], period: int = 14) -> float:
    """Classic Relative Strength Index over a price series."""
    if len(series) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(series)):
        delta = series[i] - series[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def trend_signal(coin: str = "bitcoin", days: int = 14) -> str:
    """Combine SMA crossover + RSI into a simple buy/sell/hold signal."""
    series = crypto_market_chart(coin, days)
    if len(series) < 5:
        return json.dumps({"coin": coin, "signal": "hold", "reason": "insufficient data"})
    short = sma(series, max(3, days // 4))
    long = sma(series, max(5, days // 2))
    momentum = rsi(series)
    change = pct_change(series)
    if short > long and momentum < 70:
        signal = "buy"
    elif short < long and momentum > 30:
        signal = "sell"
    else:
        signal = "hold"
    return json.dumps(
        {
            "coin": coin,
            "signal": signal,
            "sma_short": short,
            "sma_long": long,
            "rsi": momentum,
            "change_pct": change,
        }
    )
