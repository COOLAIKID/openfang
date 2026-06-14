from __future__ import annotations

import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
DEFILLAMA_BASE = "https://api.llama.fi"
ALT_ME_BASE = "https://api.alternative.me"
WHALE_ALERT_BASE = "https://api.whale-alert.io/v1"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "autoearn-crypto-toolkit/1.0"})

# Read optional API keys from environment so callers can inject them at import time.
import os as _os
_WHALE_ALERT_KEY: str = _os.environ.get("WHALE_ALERT_API_KEY", "")
_GLASSNODE_KEY: str = _os.environ.get("GLASSNODE_API_KEY", "")


def _get(url: str, params: dict | None = None, timeout: int = 15) -> dict | list:
    """GET with basic error handling. Returns parsed JSON."""
    resp = _SESSION.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert value to float, returning default on failure."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _pct(numerator: float, denominator: float) -> float:
    """Return percentage with zero-division guard."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 4)


# ---------------------------------------------------------------------------
# 1. top_coins
# ---------------------------------------------------------------------------

def top_coins(limit: int = 50) -> list[dict]:
    """Return top *limit* coins by market cap from CoinGecko with full stats.

    Each entry includes: id, symbol, name, current_price, market_cap,
    market_cap_rank, total_volume, price_change_24h, price_change_percentage_24h,
    price_change_percentage_7d_in_currency, circulating_supply, total_supply,
    max_supply, ath, ath_change_percentage, last_updated.
    """
    limit = max(1, min(limit, 250))
    pages_needed = math.ceil(limit / 100)
    results: list[dict] = []

    for page in range(1, pages_needed + 1):
        per_page = min(100, limit - len(results))
        data = _get(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "1h,24h,7d",
            },
        )
        if not isinstance(data, list):
            break
        results.extend(data)
        if len(data) < per_page:
            break

    cleaned: list[dict] = []
    for coin in results[:limit]:
        cleaned.append(
            {
                "id": coin.get("id", ""),
                "symbol": coin.get("symbol", "").upper(),
                "name": coin.get("name", ""),
                "current_price_usd": _safe_float(coin.get("current_price")),
                "market_cap_usd": _safe_float(coin.get("market_cap")),
                "market_cap_rank": coin.get("market_cap_rank"),
                "total_volume_usd": _safe_float(coin.get("total_volume")),
                "price_change_24h_usd": _safe_float(coin.get("price_change_24h")),
                "price_change_pct_1h": _safe_float(
                    coin.get("price_change_percentage_1h_in_currency")
                ),
                "price_change_pct_24h": _safe_float(
                    coin.get("price_change_percentage_24h")
                ),
                "price_change_pct_7d": _safe_float(
                    coin.get("price_change_percentage_7d_in_currency")
                ),
                "circulating_supply": _safe_float(coin.get("circulating_supply")),
                "total_supply": _safe_float(coin.get("total_supply")),
                "max_supply": coin.get("max_supply"),
                "ath_usd": _safe_float(coin.get("ath")),
                "ath_change_pct": _safe_float(coin.get("ath_change_percentage")),
                "ath_date": coin.get("ath_date", ""),
                "image": coin.get("image", ""),
                "last_updated": coin.get("last_updated", ""),
            }
        )
    return cleaned


# ---------------------------------------------------------------------------
# 2. coin_detail
# ---------------------------------------------------------------------------

def coin_detail(coin_id: str) -> dict:
    """Return comprehensive data for a single coin from CoinGecko.

    Includes price, volume, supply, ATH, ATL, sentiment votes, description
    snippet, developer stats, community stats, and category list.
    """
    data = _get(
        f"{COINGECKO_BASE}/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "true",
            "sparkline": "false",
        },
    )

    md = data.get("market_data", {})
    usd = lambda field: _safe_float((md.get(field) or {}).get("usd"))  # noqa: E731

    desc_raw: str = (data.get("description") or {}).get("en", "")
    desc_clean = re.sub(r"<[^>]+>", "", desc_raw)[:500].strip()

    return {
        "id": data.get("id", ""),
        "symbol": (data.get("symbol") or "").upper(),
        "name": data.get("name", ""),
        "description": desc_clean,
        "categories": data.get("categories", []),
        "homepage": (data.get("links") or {}).get("homepage", [""])[0],
        "current_price_usd": usd("current_price"),
        "market_cap_usd": usd("market_cap"),
        "market_cap_rank": data.get("market_cap_rank"),
        "fully_diluted_valuation_usd": usd("fully_diluted_valuation"),
        "total_volume_usd": usd("total_volume"),
        "high_24h_usd": usd("high_24h"),
        "low_24h_usd": usd("low_24h"),
        "price_change_pct_24h": _safe_float(md.get("price_change_percentage_24h")),
        "price_change_pct_7d": _safe_float(md.get("price_change_percentage_7d")),
        "price_change_pct_30d": _safe_float(md.get("price_change_percentage_30d")),
        "price_change_pct_1y": _safe_float(md.get("price_change_percentage_1y")),
        "ath_usd": usd("ath"),
        "ath_change_pct": _safe_float((md.get("ath_change_percentage") or {}).get("usd")),
        "ath_date": (md.get("ath_date") or {}).get("usd", ""),
        "atl_usd": usd("atl"),
        "atl_date": (md.get("atl_date") or {}).get("usd", ""),
        "circulating_supply": _safe_float(md.get("circulating_supply")),
        "total_supply": _safe_float(md.get("total_supply")),
        "max_supply": md.get("max_supply"),
        "sentiment_votes_up_pct": _safe_float(data.get("sentiment_votes_up_percentage")),
        "sentiment_votes_down_pct": _safe_float(
            data.get("sentiment_votes_down_percentage")
        ),
        "coingecko_score": _safe_float(data.get("coingecko_score")),
        "developer_score": _safe_float(data.get("developer_score")),
        "community_score": _safe_float(data.get("community_score")),
        "liquidity_score": _safe_float(data.get("liquidity_score")),
        "public_interest_score": _safe_float(data.get("public_interest_score")),
        "github_stars": (data.get("developer_data") or {}).get("stars", 0),
        "github_forks": (data.get("developer_data") or {}).get("forks", 0),
        "twitter_followers": (data.get("community_data") or {}).get(
            "twitter_followers", 0
        ),
        "reddit_subscribers": (data.get("community_data") or {}).get(
            "reddit_subscribers", 0
        ),
        "last_updated": data.get("last_updated", ""),
    }


# ---------------------------------------------------------------------------
# 3. fear_greed_index
# ---------------------------------------------------------------------------

def fear_greed_index() -> dict:
    """Fetch Crypto Fear & Greed Index from alternative.me.

    Returns today's value, classification, timestamp, and a 7-day history list.
    """
    data = _get(f"{ALT_ME_BASE}/fng/", params={"limit": 7, "format": "json"})
    entries = (data.get("data") or []) if isinstance(data, dict) else []

    result: dict = {"current": {}, "history": []}
    for i, entry in enumerate(entries):
        parsed = {
            "value": int(entry.get("value", 0)),
            "classification": entry.get("value_classification", ""),
            "timestamp": datetime.fromtimestamp(
                int(entry.get("timestamp", 0)), tz=timezone.utc
            ).isoformat(),
        }
        if i == 0:
            result["current"] = parsed
        result["history"].append(parsed)

    if result["current"]:
        v = result["current"]["value"]
        result["current"]["interpretation"] = (
            "Extreme Fear" if v <= 25
            else "Fear" if v <= 45
            else "Neutral" if v <= 55
            else "Greed" if v <= 75
            else "Extreme Greed"
        )
    return result


# ---------------------------------------------------------------------------
# 4. defi_protocols
# ---------------------------------------------------------------------------

def defi_protocols(limit: int = 30) -> list[dict]:
    """Return top DeFi protocols by TVL from DeFiLlama."""
    data = _get(f"{DEFILLAMA_BASE}/protocols")
    if not isinstance(data, list):
        return []

    sorted_protocols = sorted(
        data, key=lambda x: _safe_float(x.get("tvl")), reverse=True
    )

    results: list[dict] = []
    for p in sorted_protocols[:limit]:
        results.append(
            {
                "name": p.get("name", ""),
                "slug": p.get("slug", ""),
                "symbol": (p.get("symbol") or "").upper(),
                "chain": p.get("chain", ""),
                "chains": p.get("chains", []),
                "category": p.get("category", ""),
                "tvl_usd": _safe_float(p.get("tvl")),
                "change_1h_pct": _safe_float(p.get("change_1h")),
                "change_24h_pct": _safe_float(p.get("change_24h")),
                "change_7d_pct": _safe_float(p.get("change_7d")),
                "mcap_tvl_ratio": _safe_float(p.get("mcap")) / _safe_float(p.get("tvl"))
                if _safe_float(p.get("tvl")) > 0
                else None,
                "token_price_usd": _safe_float(p.get("tokenBreakdowns", {}).get("usd")),
                "logo": p.get("logo", ""),
                "url": p.get("url", ""),
            }
        )
    return results


# ---------------------------------------------------------------------------
# 5. yield_opportunities
# ---------------------------------------------------------------------------

def yield_opportunities(min_apy: float = 10.0, chain: str = "") -> list[dict]:
    """Return yield farming opportunities from DeFiLlama yields endpoint.

    Filters by minimum APY and optionally by blockchain name.
    """
    data = _get("https://yields.llama.fi/pools")
    pools = (data.get("data") or []) if isinstance(data, dict) else []

    results: list[dict] = []
    for pool in pools:
        apy = _safe_float(pool.get("apy"))
        if apy < min_apy:
            continue
        pool_chain = (pool.get("chain") or "").lower()
        if chain and pool_chain != chain.lower():
            continue
        results.append(
            {
                "pool_id": pool.get("pool", ""),
                "chain": pool.get("chain", ""),
                "project": pool.get("project", ""),
                "symbol": pool.get("symbol", ""),
                "tvl_usd": _safe_float(pool.get("tvlUsd")),
                "apy_pct": apy,
                "apy_base_pct": _safe_float(pool.get("apyBase")),
                "apy_reward_pct": _safe_float(pool.get("apyReward")),
                "il_risk": pool.get("ilRisk", ""),
                "exposure": pool.get("exposure", ""),
                "stable_coin": bool(pool.get("stablecoin", False)),
                "underlying_tokens": pool.get("underlyingTokens") or [],
                "reward_tokens": pool.get("rewardTokens") or [],
                "url": pool.get("url", ""),
            }
        )

    # Sort by APY descending, cap at 200 to avoid noise from tiny pools
    results.sort(key=lambda x: x["apy_pct"], reverse=True)
    return results[:200]


# ---------------------------------------------------------------------------
# 6. nft_collection_stats
# ---------------------------------------------------------------------------

def nft_collection_stats(collection_slug: str) -> dict:
    """Fetch NFT collection stats from OpenSea public API.

    Returns floor price, total volume, owners, total supply, and 30d stats.
    Does not require an API key for basic stats endpoint.
    """
    url = f"https://api.opensea.io/api/v2/collections/{collection_slug}/stats"
    headers = {"accept": "application/json"}
    try:
        resp = _SESSION.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as exc:
        return {"error": str(exc), "collection_slug": collection_slug}

    total = data.get("total", {})
    intervals = data.get("intervals", [])

    stats: dict = {
        "collection_slug": collection_slug,
        "floor_price_eth": _safe_float(total.get("floor_price")),
        "floor_price_symbol": total.get("floor_price_symbol", "ETH"),
        "total_volume_eth": _safe_float(total.get("volume")),
        "total_sales": int(_safe_float(total.get("sales"))),
        "total_supply": int(_safe_float(total.get("num_owners", 0))),
        "market_cap": _safe_float(total.get("market_cap")),
        "avg_price_eth": _safe_float(total.get("average_price")),
        "intervals": [],
    }

    for interval in intervals:
        stats["intervals"].append(
            {
                "period": interval.get("interval", ""),
                "volume": _safe_float(interval.get("volume")),
                "volume_change_pct": _safe_float(interval.get("volume_change")),
                "sales": int(_safe_float(interval.get("sales"))),
                "sales_change_pct": _safe_float(interval.get("sales_diff")),
                "avg_price": _safe_float(interval.get("average_price")),
            }
        )

    return stats


# ---------------------------------------------------------------------------
# 7. whale_alerts
# ---------------------------------------------------------------------------

def whale_alerts(min_usd: float = 1_000_000) -> list[dict]:
    """Return large crypto transaction alerts.

    Uses the Whale Alert API if WHALE_ALERT_API_KEY env var is set,
    otherwise scrapes public whale data from WhaleStats HTML as fallback.
    """
    if _WHALE_ALERT_KEY:
        since = int((datetime.now(tz=timezone.utc) - timedelta(hours=1)).timestamp())
        data = _get(
            f"{WHALE_ALERT_BASE}/transactions",
            params={
                "api_key": _WHALE_ALERT_KEY,
                "min_value": int(min_usd),
                "start": since,
            },
        )
        txs = data.get("transactions", []) if isinstance(data, dict) else []
        results: list[dict] = []
        for tx in txs:
            results.append(
                {
                    "id": tx.get("id", ""),
                    "blockchain": tx.get("blockchain", ""),
                    "symbol": (tx.get("symbol") or "").upper(),
                    "amount": _safe_float(tx.get("amount")),
                    "amount_usd": _safe_float(tx.get("amount_usd")),
                    "from_address": (tx.get("from") or {}).get("address", "unknown"),
                    "from_owner": (tx.get("from") or {}).get("owner", "unknown"),
                    "to_address": (tx.get("to") or {}).get("address", "unknown"),
                    "to_owner": (tx.get("to") or {}).get("owner", "unknown"),
                    "transaction_type": tx.get("transaction_type", ""),
                    "hash": tx.get("hash", ""),
                    "timestamp": datetime.fromtimestamp(
                        int(tx.get("timestamp", 0)), tz=timezone.utc
                    ).isoformat(),
                }
            )
        return results

    # Fallback: scrape recent whale transactions from whalestats
    try:
        resp = _SESSION.get(
            "https://www.whalestats.com/analysis-of-top-100-eth-wallets",
            timeout=15,
        )
        # Extract token transfer data embedded in page script blocks
        matches = re.findall(
            r'"amount_usd"\s*:\s*([0-9.]+).*?"symbol"\s*:\s*"([^"]+)"', resp.text
        )
        results = []
        for amount_str, symbol in matches[:50]:
            amount_usd = float(amount_str)
            if amount_usd >= min_usd:
                results.append(
                    {
                        "symbol": symbol.upper(),
                        "amount_usd": amount_usd,
                        "source": "whalestats_scrape",
                        "blockchain": "ethereum",
                        "note": "scraped_data_no_api_key",
                    }
                )
        return results
    except Exception:
        return [{"error": "No API key and scraping failed", "min_usd": min_usd}]


# ---------------------------------------------------------------------------
# 8. crypto_calendar
# ---------------------------------------------------------------------------

def crypto_calendar() -> list[dict]:
    """Return upcoming crypto events from CoinMarketCal public API.

    Falls back to CoinGecko events endpoint if primary source fails.
    """
    events: list[dict] = []

    # CoinMarketCal public API (no key required for basic access)
    try:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        data = _get(
            "https://developers.coinmarketcal.com/v1/events",
            params={
                "dateRangeStart": today,
                "dateRangeEnd": future,
                "sortBy": "trending",
                "max": 50,
            },
        )
        body = data.get("body", []) if isinstance(data, dict) else []
        for ev in body:
            coins = ev.get("coins", [])
            events.append(
                {
                    "id": ev.get("id", ""),
                    "title": ev.get("title", {}).get("en", ""),
                    "coins": [c.get("symbol", "").upper() for c in coins],
                    "date_event": ev.get("date_event", ""),
                    "created_date": ev.get("created_date", ""),
                    "categories": [
                        cat.get("name", "") for cat in ev.get("categories", [])
                    ],
                    "percentage": _safe_float(ev.get("percentage")),
                    "vote_count": int(_safe_float(ev.get("vote_count", 0))),
                    "source": "coinmarketcal",
                }
            )
        if events:
            return events
    except Exception:
        pass

    # CoinGecko events fallback
    try:
        data = _get(f"{COINGECKO_BASE}/events")
        for ev in (data.get("data") or []):
            events.append(
                {
                    "type": ev.get("type", ""),
                    "title": ev.get("title", ""),
                    "description": (ev.get("description") or "")[:200],
                    "organizer": ev.get("organizer", ""),
                    "start_date": ev.get("start_date", ""),
                    "end_date": ev.get("end_date", ""),
                    "website": ev.get("website", ""),
                    "source": "coingecko",
                }
            )
    except Exception:
        pass

    return events


# ---------------------------------------------------------------------------
# 9. gas_prices
# ---------------------------------------------------------------------------

def gas_prices() -> dict:
    """Return Ethereum gas prices (fast / standard / slow) in Gwei.

    Primary source: Etherscan gas oracle. Fallback: owlracle.info.
    """
    # Etherscan gas oracle (no key required for basic tier)
    try:
        data = _get(
            "https://api.etherscan.io/api",
            params={"module": "gastracker", "action": "gasoracle"},
        )
        result = data.get("result", {})
        if isinstance(result, dict):
            return {
                "slow_gwei": _safe_float(result.get("SafeGasPrice")),
                "standard_gwei": _safe_float(result.get("ProposeGasPrice")),
                "fast_gwei": _safe_float(result.get("FastGasPrice")),
                "base_fee_gwei": _safe_float(result.get("suggestBaseFee")),
                "gas_used_ratio": result.get("gasUsedRatio", ""),
                "source": "etherscan",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
    except Exception:
        pass

    # Fallback: owlracle
    try:
        data = _get("https://api.owlracle.info/v4/eth/gas")
        speeds = data.get("speeds", [])
        price_map: dict[str, float] = {}
        labels = ["slow", "standard", "fast", "instant"]
        for i, speed in enumerate(speeds[:4]):
            price_map[labels[i]] = _safe_float(
                speed.get("maxFeePerGas") or speed.get("gasPrice")
            )
        return {
            "slow_gwei": price_map.get("slow", 0),
            "standard_gwei": price_map.get("standard", 0),
            "fast_gwei": price_map.get("fast", 0),
            "instant_gwei": price_map.get("instant", 0),
            "base_fee_gwei": _safe_float(data.get("baseFee")),
            "source": "owlracle",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
    except Exception:
        pass

    return {"error": "Gas price sources unavailable", "timestamp": datetime.now(tz=timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# 10. token_unlock_schedule
# ---------------------------------------------------------------------------

def token_unlock_schedule(coin: str) -> dict:
    """Return token vesting/unlock event schedule from TokenUnlocks.app.

    coin: coin symbol or name (e.g. 'aptos', 'sui', 'arbitrum').
    """
    # TokenUnlocks public API
    try:
        data = _get(
            f"https://token.unlocks.app/api/projects/{coin.lower()}",
        )
        if isinstance(data, dict) and "error" not in data:
            unlocks = data.get("unlocks", [])
            schedule: list[dict] = []
            for event in unlocks:
                schedule.append(
                    {
                        "date": event.get("date", ""),
                        "tokens": _safe_float(event.get("tokens")),
                        "usd_value": _safe_float(event.get("usd")),
                        "unlock_type": event.get("type", ""),
                        "cliff": bool(event.get("cliff", False)),
                    }
                )
            return {
                "coin": coin,
                "total_locked_usd": _safe_float(data.get("totalLockedUsd")),
                "total_locked_tokens": _safe_float(data.get("totalLocked")),
                "circulating_pct": _safe_float(data.get("circulatingPct")),
                "next_unlock_date": data.get("nextUnlockDate", ""),
                "schedule": schedule,
                "source": "tokenunlocks",
            }
    except Exception:
        pass

    # Fallback: CoinGecko market_chart doesn't have unlock data; return a note
    return {
        "coin": coin,
        "note": "Token unlock data unavailable. Consider checking vesting.io or token.unlocks.app manually.",
        "schedule": [],
        "source": "unavailable",
    }


# ---------------------------------------------------------------------------
# 11. dex_volume
# ---------------------------------------------------------------------------

def dex_volume(chain: str = "ethereum") -> dict:
    """Return DEX trading volume statistics from DeFiLlama for a given chain."""
    try:
        data = _get(f"{DEFILLAMA_BASE}/overview/dexs/{chain}", params={"excludeTotalDataChart": "false"})
    except Exception:
        data = {}

    protocols = data.get("protocols", []) if isinstance(data, dict) else []
    top_dexs: list[dict] = []
    for dex in sorted(protocols, key=lambda x: _safe_float(x.get("totalVolume24h")), reverse=True)[:20]:
        top_dexs.append(
            {
                "name": dex.get("name", ""),
                "volume_24h_usd": _safe_float(dex.get("totalVolume24h")),
                "volume_7d_usd": _safe_float(dex.get("totalVolume7d")),
                "change_24h_pct": _safe_float(dex.get("change_24h")),
                "change_7d_pct": _safe_float(dex.get("change_7d")),
                "chains": dex.get("chains", []),
            }
        )

    return {
        "chain": chain,
        "total_volume_24h_usd": _safe_float(data.get("total24h")),
        "total_volume_7d_usd": _safe_float(data.get("total7d")),
        "change_24h_pct": _safe_float(data.get("change_24h")),
        "top_dexs": top_dexs,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 12. crypto_correlation_matrix
# ---------------------------------------------------------------------------

def crypto_correlation_matrix(coins: list[str]) -> dict:
    """Return a price correlation matrix for the given coin IDs over 30 days.

    coins: list of CoinGecko coin IDs, e.g. ['bitcoin', 'ethereum', 'solana'].
    Returns {coins, matrix (list of lists), pearson_correlations}.
    """
    if not coins:
        return {"error": "No coins provided"}

    prices: dict[str, list[float]] = {}

    for coin_id in coins:
        try:
            data = _get(
                f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": "30", "interval": "daily"},
            )
            prices[coin_id] = [p[1] for p in data.get("prices", [])]
        except Exception:
            prices[coin_id] = []

    # Align lengths to the shortest series
    min_len = min((len(v) for v in prices.values() if v), default=0)
    if min_len < 2:
        return {"error": "Insufficient price data", "coins": coins}

    aligned = {k: v[-min_len:] for k, v in prices.items() if len(v) >= min_len}
    coin_ids = list(aligned.keys())

    def _pearson(a: list[float], b: list[float]) -> float:
        if len(a) != len(b) or len(a) < 2:
            return float("nan")
        try:
            mean_a, mean_b = statistics.mean(a), statistics.mean(b)
            cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
            std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
            std_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
            if std_a == 0 or std_b == 0:
                return float("nan")
            return round(cov / (std_a * std_b), 4)
        except Exception:
            return float("nan")

    matrix: list[list[float]] = []
    for coin_a in coin_ids:
        row: list[float] = []
        for coin_b in coin_ids:
            row.append(_pearson(aligned[coin_a], aligned[coin_b]))
        matrix.append(row)

    flat_pairs: list[dict] = []
    for i, ca in enumerate(coin_ids):
        for j, cb in enumerate(coin_ids):
            if j > i:
                flat_pairs.append({"pair": f"{ca}/{cb}", "correlation": matrix[i][j]})

    flat_pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    return {
        "coins": coin_ids,
        "matrix": matrix,
        "top_correlations": flat_pairs[:10],
        "period_days": 30,
        "data_points": min_len,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 13. liquidation_data
# ---------------------------------------------------------------------------

def liquidation_data() -> dict:
    """Return recent crypto liquidation data from Coinglass.

    Returns total liquidations, long/short split, and per-coin breakdown.
    """
    headers = {"accept": "application/json"}
    try:
        resp = _SESSION.get(
            "https://open-api.coinglass.com/public/v2/liquidation_history",
            params={"time_type": "h24", "symbol": "BTC"},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        data = {}

    # Also fetch aggregate stats
    coins_data: list[dict] = []
    try:
        resp2 = _SESSION.get(
            "https://open-api.coinglass.com/public/v2/liquidation",
            headers=headers,
            timeout=15,
        )
        resp2.raise_for_status()
        agg = resp2.json().get("data", [])
        for item in agg[:20]:
            coins_data.append(
                {
                    "symbol": item.get("symbol", ""),
                    "long_liq_usd_24h": _safe_float(item.get("longLiquidationUsd24h")),
                    "short_liq_usd_24h": _safe_float(item.get("shortLiquidationUsd24h")),
                    "total_liq_usd_24h": _safe_float(item.get("liquidationUsd24h")),
                    "long_liq_count": int(_safe_float(item.get("longCount24h", 0))),
                    "short_liq_count": int(_safe_float(item.get("shortCount24h", 0))),
                }
            )
    except Exception:
        pass

    total_long = sum(c["long_liq_usd_24h"] for c in coins_data)
    total_short = sum(c["short_liq_usd_24h"] for c in coins_data)

    return {
        "total_liquidations_24h_usd": total_long + total_short,
        "long_liquidations_24h_usd": total_long,
        "short_liquidations_24h_usd": total_short,
        "long_short_ratio": round(total_long / total_short, 4) if total_short else None,
        "coins": sorted(coins_data, key=lambda x: x["total_liq_usd_24h"], reverse=True),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "source": "coinglass",
    }


# ---------------------------------------------------------------------------
# 14. on_chain_metrics
# ---------------------------------------------------------------------------

def on_chain_metrics(coin: str) -> dict:
    """Return on-chain metrics: NVT ratio, active addresses, transaction volume.

    Uses Glassnode if GLASSNODE_API_KEY env var is set, otherwise pulls
    approximate data from CoinGecko developer stats and blockchain explorers.
    """
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    since_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=30)).timestamp())

    if _GLASSNODE_KEY:
        def _gn(metric: str, asset: str) -> list[dict]:
            try:
                data = _get(
                    f"https://api.glassnode.com/v1/metrics/{metric}",
                    params={
                        "a": asset.upper(),
                        "api_key": _GLASSNODE_KEY,
                        "s": since_ts,
                        "u": now_ts,
                        "i": "24h",
                    },
                )
                return data if isinstance(data, list) else []
            except Exception:
                return []

        symbol = coin.upper()
        active_addresses_raw = _gn("addresses/active_count", symbol)
        tx_volume_raw = _gn("transactions/transfers_volume_sum", symbol)
        nvt_raw = _gn("indicators/nvt", symbol)

        avg_active = (
            statistics.mean([d.get("v", 0) for d in active_addresses_raw[-7:]])
            if active_addresses_raw else 0
        )
        avg_tx_vol = (
            statistics.mean([d.get("v", 0) for d in tx_volume_raw[-7:]])
            if tx_volume_raw else 0
        )
        latest_nvt = nvt_raw[-1].get("v", 0) if nvt_raw else 0

        return {
            "coin": coin,
            "nvt_ratio": round(latest_nvt, 4),
            "avg_active_addresses_7d": round(avg_active),
            "avg_tx_volume_7d_usd": round(avg_tx_vol, 2),
            "data_points": len(active_addresses_raw),
            "source": "glassnode",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }

    # Fallback: use CoinGecko to approximate metrics
    try:
        detail = coin_detail(coin)
        price = detail.get("current_price_usd", 0)
        volume = detail.get("total_volume_usd", 0)
        market_cap = detail.get("market_cap_usd", 0)
        # NVT approximation: market_cap / daily_tx_volume (using trading volume as proxy)
        nvt_approx = round(market_cap / volume, 4) if volume > 0 else None
        return {
            "coin": coin,
            "nvt_ratio_approx": nvt_approx,
            "nvt_note": "Approximated using market_cap/trading_volume; real NVT uses on-chain tx volume",
            "current_price_usd": price,
            "market_cap_usd": market_cap,
            "trading_volume_24h_usd": volume,
            "sentiment_up_pct": detail.get("sentiment_votes_up_pct", 0),
            "github_stars": detail.get("github_stars", 0),
            "source": "coingecko_approx",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"coin": coin, "error": str(exc)}


# ---------------------------------------------------------------------------
# 15. arbitrage_opportunities
# ---------------------------------------------------------------------------

def arbitrage_opportunities(
    base_asset: str = "bitcoin",
    exchanges: list[str] | None = None,
) -> list[dict]:
    """Detect price differences for base_asset across multiple exchanges.

    Uses CoinGecko tickers endpoint to fetch per-exchange prices.
    Returns list of opportunities sorted by spread percentage descending.
    """
    if exchanges is None:
        exchanges = ["binance", "coinbase", "kraken"]

    # Fetch tickers from CoinGecko (returns exchange-level price data)
    all_tickers: list[dict] = []
    page = 1
    while page <= 5:
        try:
            data = _get(
                f"{COINGECKO_BASE}/coins/{base_asset}/tickers",
                params={"page": page, "include_exchange_logo": "false", "depth": "false"},
            )
        except Exception:
            break

        tickers = data.get("tickers", []) if isinstance(data, dict) else []
        if not tickers:
            break
        all_tickers.extend(tickers)
        page += 1

    # Collect USD-equivalent prices by exchange
    exchange_prices: dict[str, list[float]] = {}
    target_symbols = {"USD", "USDT", "USDC", "BUSD", "DAI"}

    for ticker in all_tickers:
        ex_id = (ticker.get("market") or {}).get("identifier", "").lower()
        if exchanges and ex_id not in [e.lower() for e in exchanges]:
            continue
        target = (ticker.get("target") or "").upper()
        if target not in target_symbols:
            continue
        price = _safe_float(ticker.get("last"))
        if price <= 0:
            continue
        exchange_prices.setdefault(ex_id, []).append(price)

    # Average prices per exchange
    avg_prices: dict[str, float] = {
        ex: statistics.mean(prices) for ex, prices in exchange_prices.items() if prices
    }

    if len(avg_prices) < 2:
        return [
            {
                "note": "Insufficient exchange data for arbitrage analysis",
                "asset": base_asset,
                "exchanges_found": list(avg_prices.keys()),
            }
        ]

    # Find all pairs
    opportunities: list[dict] = []
    ex_list = list(avg_prices.items())
    for i in range(len(ex_list)):
        for j in range(i + 1, len(ex_list)):
            ex_a, price_a = ex_list[i]
            ex_b, price_b = ex_list[j]
            spread = abs(price_a - price_b)
            spread_pct = _pct(spread, min(price_a, price_b))
            buy_on = ex_a if price_a < price_b else ex_b
            sell_on = ex_b if price_a < price_b else ex_a
            buy_price = min(price_a, price_b)
            sell_price = max(price_a, price_b)

            opportunities.append(
                {
                    "asset": base_asset.upper(),
                    "buy_exchange": buy_on,
                    "sell_exchange": sell_on,
                    "buy_price_usd": round(buy_price, 6),
                    "sell_price_usd": round(sell_price, 6),
                    "spread_usd": round(spread, 6),
                    "spread_pct": spread_pct,
                    "estimated_profit_per_1k_usd": round(
                        (spread_pct / 100) * 1000, 4
                    ),
                    "viable": spread_pct > 0.5,  # >0.5% is typically above fees
                }
            )

    opportunities.sort(key=lambda x: x["spread_pct"], reverse=True)
    return opportunities
