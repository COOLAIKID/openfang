from __future__ import annotations

import json
import math
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_YF_BASE = "https://query1.finance.yahoo.com/v8/finance"
_YF_V10  = "https://query2.finance.yahoo.com/v10/finance"

_SECTOR_ETFS = {
    "Technology":        "XLK",
    "Healthcare":        "XLV",
    "Financials":        "XLF",
    "Consumer Disc.":    "XLY",
    "Communication":     "XLC",
    "Industrials":       "XLI",
    "Consumer Staples":  "XLP",
    "Energy":            "XLE",
    "Utilities":         "XLU",
    "Real Estate":       "XLRE",
    "Materials":         "XLB",
}


def _yf_get(path: str, params: dict | None = None) -> dict[str, Any]:
    """GET from Yahoo Finance v8 API with cookie/crumb handling."""
    url = f"{_YF_BASE}/{path}"
    try:
        resp = requests.get(url, headers=_HEADERS, params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def _yf_v10_get(path: str, params: dict | None = None) -> dict[str, Any]:
    url = f"{_YF_V10}/{path}"
    try:
        resp = requests.get(url, headers=_HEADERS, params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def _safe_float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> int | None:
    try:
        if isinstance(val, dict):
            val = val.get("raw", val.get("fmt", None))
        return int(val)
    except (TypeError, ValueError):
        return None


def _raw_val(obj: Any) -> Any:
    """Extract raw numeric value from Yahoo Finance dict objects."""
    if isinstance(obj, dict):
        return obj.get("raw", obj.get("fmt", None))
    return obj


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def stock_price(symbol: str) -> dict[str, Any]:
    """
    Fetch current price, change %, volume, and market cap via Yahoo Finance.

    Returns {symbol, price, change, change_pct, volume, market_cap, currency}.
    """
    symbol = symbol.upper().strip()
    data = _yf_get(f"chart/{symbol}", {"interval": "1d", "range": "1d"})

    if "error" in data and "chart" not in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("previousClose") or meta.get("chartPreviousClose", price)
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        volume = meta.get("regularMarketVolume", 0)
        currency = meta.get("currency", "USD")

        # Market cap from v10 summary
        summary = _yf_v10_get(f"quoteSummary/{symbol}", {"modules": "summaryDetail"})
        market_cap = None
        if "quoteSummary" in summary:
            sd = (summary["quoteSummary"].get("result") or [{}])[0]
            market_cap = _raw_val(sd.get("summaryDetail", {}).get("marketCap"))

        return {
            "symbol": symbol,
            "price": round(price, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "market_cap": market_cap,
            "currency": currency,
        }
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def stock_history(symbol: str, days: int = 30) -> dict[str, Any]:
    """
    Fetch historical OHLCV data for the past N days.

    Returns {symbol, days, history: [{date, open, high, low, close, volume}]}.
    """
    symbol = symbol.upper().strip()
    range_str = "1mo" if days <= 30 else ("3mo" if days <= 90 else ("6mo" if days <= 180 else "1y"))

    data = _yf_get(f"chart/{symbol}", {"interval": "1d", "range": range_str})

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        ohlcv = result["indicators"]["quote"][0]
        opens   = ohlcv.get("open",   [])
        highs   = ohlcv.get("high",   [])
        lows    = ohlcv.get("low",    [])
        closes  = ohlcv.get("close",  [])
        volumes = ohlcv.get("volume", [])

        history = []
        for i, ts in enumerate(timestamps):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            history.append({
                "date":   dt,
                "open":   round(opens[i],  4) if i < len(opens)   and opens[i]   is not None else None,
                "high":   round(highs[i],  4) if i < len(highs)   and highs[i]   is not None else None,
                "low":    round(lows[i],   4) if i < len(lows)    and lows[i]    is not None else None,
                "close":  round(closes[i], 4) if i < len(closes)  and closes[i]  is not None else None,
                "volume": volumes[i]           if i < len(volumes) else None,
            })

        return {"symbol": symbol, "days": days, "history": history}
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def fundamental_data(symbol: str) -> dict[str, Any]:
    """
    Retrieve fundamental metrics: P/E, EPS, revenue, profit margin, etc.

    Returns {symbol, pe_ratio, eps, revenue, profit_margin, debt_to_equity, ...}.
    """
    symbol = symbol.upper().strip()
    modules = "defaultKeyStatistics,financialData,summaryDetail"
    data = _yf_v10_get(f"quoteSummary/{symbol}", {"modules": modules})

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = (data.get("quoteSummary", {}).get("result") or [{}])[0]
        ks = result.get("defaultKeyStatistics", {})
        fd = result.get("financialData", {})
        sd = result.get("summaryDetail", {})

        return {
            "symbol": symbol,
            "pe_ratio":             _raw_val(sd.get("trailingPE")),
            "forward_pe":           _raw_val(sd.get("forwardPE")),
            "eps":                  _raw_val(ks.get("trailingEps")),
            "forward_eps":          _raw_val(ks.get("forwardEps")),
            "revenue":              _raw_val(fd.get("totalRevenue")),
            "revenue_growth":       _raw_val(fd.get("revenueGrowth")),
            "gross_margin":         _raw_val(fd.get("grossMargins")),
            "profit_margin":        _raw_val(fd.get("profitMargins")),
            "operating_margin":     _raw_val(fd.get("operatingMargins")),
            "return_on_equity":     _raw_val(fd.get("returnOnEquity")),
            "return_on_assets":     _raw_val(fd.get("returnOnAssets")),
            "debt_to_equity":       _raw_val(fd.get("debtToEquity")),
            "current_ratio":        _raw_val(fd.get("currentRatio")),
            "book_value":           _raw_val(ks.get("bookValue")),
            "price_to_book":        _raw_val(ks.get("priceToBook")),
            "enterprise_value":     _raw_val(ks.get("enterpriseValue")),
            "52w_high":             _raw_val(sd.get("fiftyTwoWeekHigh")),
            "52w_low":              _raw_val(sd.get("fiftyTwoWeekLow")),
            "shares_outstanding":   _raw_val(ks.get("sharesOutstanding")),
            "float_shares":         _raw_val(ks.get("floatShares")),
        }
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def earnings_calendar(days_ahead: int = 14) -> dict[str, Any]:
    """
    Fetch upcoming earnings announcements from Yahoo Finance screener.

    Returns {as_of, days_ahead, announcements: [{symbol, name, date, eps_estimate}]}.
    """
    today = datetime.now(timezone.utc)
    end   = today + timedelta(days=days_ahead)
    start_str = today.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")

    url = "https://finance.yahoo.com/calendar/earnings"
    try:
        resp = requests.get(
            url,
            headers=_HEADERS,
            params={"from": start_str, "to": end_str},
            timeout=15,
        )
        html = resp.text
        # Extract JSON embedded in the page
        m = re.search(r'"earnings":\{"rows":(\[.*?\])', html)
        if m:
            rows = json.loads(m.group(1))
        else:
            rows = []
    except Exception as exc:
        return {"error": str(exc), "announcements": []}

    announcements = []
    for row in rows:
        announcements.append({
            "symbol":       row.get("ticker", ""),
            "name":         row.get("companyshortname", ""),
            "date":         row.get("startdatetimetype", ""),
            "eps_estimate": row.get("epsestimate", None),
        })

    return {
        "as_of": start_str,
        "days_ahead": days_ahead,
        "announcements": announcements,
    }


def insider_trading(symbol: str) -> dict[str, Any]:
    """
    Fetch recent insider buy/sell transactions from SEC EDGAR.

    Returns {symbol, transactions: [{insider, title, type, shares, value, date}]}.
    """
    symbol = symbol.upper().strip()
    url = (
        f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22"
        f"&dateRange=custom&startdt={datetime.now().year - 1}-01-01"
        f"&forms=4"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "autoearn bot@example.com"}, timeout=15)
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc), "transactions": []}

    transactions = []
    for hit in hits[:20]:
        src = hit.get("_source", {})
        transactions.append({
            "insider":  src.get("entity_name", ""),
            "title":    src.get("file_num", ""),
            "type":     src.get("form_type", "4"),
            "date":     src.get("period_of_report", ""),
            "filing":   src.get("file_date", ""),
            "url":      f"https://www.sec.gov/Archives/{src.get('file_path', '')}",
        })

    return {"symbol": symbol, "transactions": transactions}


def short_interest(symbol: str) -> dict[str, Any]:
    """
    Retrieve short interest data (short float %, days to cover).

    Returns {symbol, short_float_pct, days_to_cover, short_shares, source}.
    """
    symbol = symbol.upper().strip()

    # Yahoo Finance key statistics has short interest data
    data = _yf_v10_get(f"quoteSummary/{symbol}", {"modules": "defaultKeyStatistics"})

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = (data.get("quoteSummary", {}).get("result") or [{}])[0]
        ks = result.get("defaultKeyStatistics", {})
        short_pct   = _raw_val(ks.get("shortPercentOfFloat"))
        days_cover  = _raw_val(ks.get("shortRatio"))
        short_shares= _raw_val(ks.get("sharesShort"))
        short_prior = _raw_val(ks.get("sharesShortPriorMonth"))

        return {
            "symbol": symbol,
            "short_float_pct": round(float(short_pct) * 100, 2) if short_pct else None,
            "days_to_cover": days_cover,
            "short_shares": short_shares,
            "short_shares_prior_month": short_prior,
            "source": "yahoo_finance",
        }
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def sector_performance() -> dict[str, Any]:
    """
    Fetch 1-day and YTD performance of S&P 500 sector ETFs.

    Returns {as_of, sectors: [{sector, etf, price, change_pct, ytd_pct}]}.
    """
    results = []
    for sector, etf in _SECTOR_ETFS.items():
        data = _yf_get(f"chart/{etf}", {"interval": "1d", "range": "ytd"})
        try:
            meta = data["chart"]["result"][0]["meta"]
            price    = meta.get("regularMarketPrice", 0)
            prev     = meta.get("previousClose") or meta.get("chartPreviousClose", price)
            change_1d = round((price - prev) / prev * 100, 2) if prev else 0

            ts  = data["chart"]["result"][0].get("timestamp", [])
            q   = data["chart"]["result"][0]["indicators"]["quote"][0]
            closes = [c for c in q.get("close", []) if c is not None]
            ytd_pct = round((price - closes[0]) / closes[0] * 100, 2) if closes else 0

            results.append({
                "sector":     sector,
                "etf":        etf,
                "price":      round(price, 2),
                "change_pct": change_1d,
                "ytd_pct":    ytd_pct,
            })
        except (KeyError, IndexError, TypeError):
            results.append({"sector": sector, "etf": etf, "error": "unavailable"})

    return {"as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "sectors": results}


def stock_screener(criteria: dict[str, Any]) -> dict[str, Any]:
    """
    Screen stocks using Yahoo Finance screener API.

    criteria keys: min_pe, max_pe, min_market_cap (USD), sector, min_dividend_yield.
    Returns {criteria, tickers: [{symbol, name, price, pe, market_cap, dividend_yield}]}.
    """
    filters = []

    if "min_pe" in criteria:
        filters.append({"operator": "GT", "operands": ["trailingpe", criteria["min_pe"]]})
    if "max_pe" in criteria:
        filters.append({"operator": "LT", "operands": ["trailingpe", criteria["max_pe"]]})
    if "min_market_cap" in criteria:
        filters.append({"operator": "GT", "operands": ["intradaymarketcap", criteria["min_market_cap"]]})
    if "min_dividend_yield" in criteria:
        filters.append({"operator": "GT", "operands": ["trailingannualdividendyield", criteria["min_dividend_yield"]]})
    if "sector" in criteria:
        filters.append({"operator": "EQ", "operands": ["sector", criteria["sector"]]})

    payload = {
        "size": 25,
        "offset": 0,
        "sortField": "intradaymarketcap",
        "sortType": "DESC",
        "quoteType": "EQUITY",
        "query": {
            "operator": "AND",
            "operands": filters if filters else [{"operator": "GT", "operands": ["intradaymarketcap", 1_000_000_000]}],
        },
        "userId": "",
        "userIdType": "guid",
    }

    try:
        resp = requests.post(
            "https://query1.finance.yahoo.com/v1/finance/screener",
            headers={**_HEADERS, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        data = resp.json()
        quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
    except Exception as exc:
        return {"criteria": criteria, "error": str(exc), "tickers": []}

    tickers = []
    for q in quotes:
        tickers.append({
            "symbol":         q.get("symbol", ""),
            "name":           q.get("longName") or q.get("shortName", ""),
            "price":          q.get("regularMarketPrice"),
            "pe":             q.get("trailingPE"),
            "market_cap":     q.get("marketCap"),
            "dividend_yield": q.get("trailingAnnualDividendYield"),
            "sector":         q.get("sector", ""),
        })

    return {"criteria": criteria, "count": len(tickers), "tickers": tickers}


def options_chain(symbol: str, expiry: str = "") -> dict[str, Any]:
    """
    Fetch options chain data for a symbol.

    expiry: YYYY-MM-DD string or empty for nearest expiration.
    Returns {symbol, expiry, calls: [...], puts: [...]}.
    """
    symbol = symbol.upper().strip()
    params: dict[str, Any] = {}
    if expiry:
        try:
            dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params["date"] = int(dt.timestamp())
        except ValueError:
            pass

    data = _yf_v10_get(f"options/{symbol}", params)

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = data.get("optionChain", {}).get("result", [{}])[0]
        meta = result.get("quote", {})
        opts = (result.get("options") or [{}])[0]

        def _fmt_options(lst: list[dict]) -> list[dict]:
            out = []
            for o in lst:
                out.append({
                    "strike":          o.get("strike"),
                    "last_price":      o.get("lastPrice"),
                    "bid":             o.get("bid"),
                    "ask":             o.get("ask"),
                    "volume":          o.get("volume"),
                    "open_interest":   o.get("openInterest"),
                    "implied_vol":     round(o.get("impliedVolatility", 0) * 100, 2),
                    "in_the_money":    o.get("inTheMoney", False),
                    "expiry":          datetime.fromtimestamp(
                                           o.get("expiration", 0), tz=timezone.utc
                                       ).strftime("%Y-%m-%d"),
                })
            return out

        expirations = result.get("expirationDates", [])
        chosen_exp  = datetime.fromtimestamp(expirations[0], tz=timezone.utc).strftime("%Y-%m-%d") \
                      if expirations else expiry

        return {
            "symbol":      symbol,
            "expiry":      chosen_exp,
            "stock_price": meta.get("regularMarketPrice"),
            "calls":       _fmt_options(opts.get("calls", [])),
            "puts":        _fmt_options(opts.get("puts",  [])),
        }
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def compute_technical_indicators(
    prices: list[float],
    volumes: list[float] | None = None,
) -> dict[str, Any]:
    """
    Compute technical indicators from a list of closing prices.

    Indicators: SMA(20), EMA(12/26), RSI(14), MACD, Bollinger Bands(20,2), OBV.

    Returns {sma_20, ema_12, ema_26, rsi_14, macd, macd_signal, macd_hist,
             bb_upper, bb_middle, bb_lower, obv}.
    """
    n = len(prices)
    if n < 2:
        return {"error": "Need at least 2 data points"}

    def ema(data: list[float], period: int) -> list[float]:
        k = 2.0 / (period + 1)
        result = [sum(data[:period]) / period]
        for p in data[period:]:
            result.append(p * k + result[-1] * (1 - k))
        return result

    # SMA(20)
    sma_20 = round(statistics.mean(prices[-20:]), 4) if n >= 20 else round(statistics.mean(prices), 4)

    # EMA 12 and 26
    ema12_series = ema(prices, 12) if n >= 12 else []
    ema26_series = ema(prices, 26) if n >= 26 else []
    ema12 = round(ema12_series[-1], 4) if ema12_series else None
    ema26 = round(ema26_series[-1], 4) if ema26_series else None

    # MACD line = EMA12 - EMA26
    macd_val = None
    macd_signal_val = None
    macd_hist_val = None
    if ema12_series and ema26_series:
        offset = len(ema12_series) - len(ema26_series)
        macd_line = [e12 - e26 for e12, e26 in zip(ema12_series[offset:], ema26_series)]
        macd_val = round(macd_line[-1], 4) if macd_line else None
        if len(macd_line) >= 9:
            signal_series = ema(macd_line, 9)
            macd_signal_val = round(signal_series[-1], 4)
            macd_hist_val   = round(macd_line[-1] - signal_series[-1], 4)

    # RSI(14)
    rsi_val = None
    if n >= 15:
        deltas = [prices[i] - prices[i - 1] for i in range(1, n)]
        gains  = [max(d, 0) for d in deltas]
        losses = [max(-d, 0) for d in deltas]
        avg_gain = statistics.mean(gains[-14:])
        avg_loss = statistics.mean(losses[-14:])
        if avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = round(100 - 100 / (1 + rs), 2)

    # Bollinger Bands(20, 2)
    bb_upper = bb_middle = bb_lower = None
    if n >= 20:
        window = prices[-20:]
        mean   = statistics.mean(window)
        stdev  = statistics.stdev(window)
        bb_middle = round(mean, 4)
        bb_upper  = round(mean + 2 * stdev, 4)
        bb_lower  = round(mean - 2 * stdev, 4)

    # OBV
    obv = 0.0
    obv_series = [0.0]
    if volumes and len(volumes) >= n:
        for i in range(1, n):
            if prices[i] > prices[i - 1]:
                obv += volumes[i]
            elif prices[i] < prices[i - 1]:
                obv -= volumes[i]
            obv_series.append(obv)

    return {
        "sma_20":       sma_20,
        "ema_12":       ema12,
        "ema_26":       ema26,
        "rsi_14":       rsi_val,
        "macd":         macd_val,
        "macd_signal":  macd_signal_val,
        "macd_hist":    macd_hist_val,
        "bb_upper":     bb_upper,
        "bb_middle":    bb_middle,
        "bb_lower":     bb_lower,
        "obv":          round(obv, 2),
        "data_points":  n,
    }


def earnings_surprise_history(symbol: str) -> dict[str, Any]:
    """
    Fetch last 8 quarters of EPS estimate vs actual (earnings surprise).

    Returns {symbol, quarters: [{period, eps_estimate, eps_actual, surprise_pct}]}.
    """
    symbol = symbol.upper().strip()
    data = _yf_v10_get(f"quoteSummary/{symbol}", {"modules": "earningsHistory"})

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = (data.get("quoteSummary", {}).get("result") or [{}])[0]
        history = result.get("earningsHistory", {}).get("history", [])

        quarters = []
        for q in history:
            actual   = _raw_val(q.get("epsActual"))
            estimate = _raw_val(q.get("epsEstimate"))
            surprise = _raw_val(q.get("epsDifference"))
            surprise_pct = _raw_val(q.get("surprisePercent"))
            period   = q.get("period", "")
            quarters.append({
                "period":       period,
                "eps_estimate": estimate,
                "eps_actual":   actual,
                "surprise":     surprise,
                "surprise_pct": round(float(surprise_pct) * 100, 2) if surprise_pct is not None else None,
                "quarter":      q.get("quarter", {}).get("fmt", period) if isinstance(q.get("quarter"), dict) else period,
            })

        return {"symbol": symbol, "quarters": quarters[-8:]}
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def analyst_ratings(symbol: str) -> dict[str, Any]:
    """
    Fetch analyst consensus buy/hold/sell ratings and price targets.

    Returns {symbol, recommendation, mean_rating, price_target_mean, price_target_high,
             price_target_low, num_analysts, breakdown: {strong_buy, buy, hold, sell, strong_sell}}.
    """
    symbol = symbol.upper().strip()
    data = _yf_v10_get(
        f"quoteSummary/{symbol}",
        {"modules": "recommendationTrend,financialData"},
    )

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = (data.get("quoteSummary", {}).get("result") or [{}])[0]
        fd = result.get("financialData", {})
        rt = result.get("recommendationTrend", {})

        trend = (rt.get("trend") or [{}])[0] if rt.get("trend") else {}

        return {
            "symbol":             symbol,
            "recommendation":     fd.get("recommendationKey", "none"),
            "mean_rating":        _raw_val(fd.get("recommendationMean")),
            "price_target_mean":  _raw_val(fd.get("targetMeanPrice")),
            "price_target_high":  _raw_val(fd.get("targetHighPrice")),
            "price_target_low":   _raw_val(fd.get("targetLowPrice")),
            "num_analysts":       _raw_val(fd.get("numberOfAnalystOpinions")),
            "breakdown": {
                "strong_buy":  trend.get("strongBuy",  0),
                "buy":         trend.get("buy",        0),
                "hold":        trend.get("hold",       0),
                "sell":        trend.get("sell",       0),
                "strong_sell": trend.get("strongSell", 0),
            },
        }
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def dividend_history(symbol: str, years: int = 5) -> dict[str, Any]:
    """
    Fetch dividend payment history for the past N years.

    Returns {symbol, years, dividends: [{date, amount}], annual_summary: [{year, total}]}.
    """
    symbol = symbol.upper().strip()
    range_map = {1: "1y", 2: "2y", 3: "5y", 5: "5y", 10: "10y"}
    range_str = range_map.get(years, "5y") if years <= 5 else "10y"

    data = _yf_get(
        f"chart/{symbol}",
        {"interval": "1d", "range": range_str, "events": "dividends"},
    )

    if "error" in data:
        return {"symbol": symbol, "error": data["error"]}

    try:
        result = data["chart"]["result"][0]
        raw_divs = result.get("events", {}).get("dividends", {})

        dividends = []
        annual: dict[str, float] = {}
        for ts_str, info in raw_divs.items():
            ts  = int(ts_str)
            amt = info.get("amount", 0)
            dt  = datetime.fromtimestamp(ts, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            yr = str(dt.year)
            dividends.append({"date": date_str, "amount": round(amt, 4)})
            annual[yr] = round(annual.get(yr, 0) + amt, 4)

        dividends.sort(key=lambda x: x["date"])
        annual_summary = [{"year": k, "total": v} for k, v in sorted(annual.items())]

        return {
            "symbol": symbol,
            "years": years,
            "dividends": dividends,
            "annual_summary": annual_summary,
        }
    except (KeyError, IndexError, TypeError) as exc:
        return {"symbol": symbol, "error": f"parse error: {exc}"}


def sec_filings(symbol: str, form_type: str = "10-K") -> dict[str, Any]:
    """
    Fetch recent SEC filings via the EDGAR full-text search API.

    Returns {symbol, form_type, filings: [{accession, filed, period, url}]}.
    """
    symbol = symbol.upper().strip()

    # Map ticker to CIK via EDGAR company search
    try:
        resp = requests.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&forms={form_type}",
            headers={"User-Agent": "autoearn/1.0 contact@example.com"},
            timeout=15,
        )
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
    except Exception as exc:
        return {"symbol": symbol, "form_type": form_type, "error": str(exc), "filings": []}

    filings = []
    for hit in hits[:10]:
        src = hit.get("_source", {})
        accession = src.get("file_num", hit.get("_id", ""))
        filings.append({
            "accession": accession,
            "filed":     src.get("file_date", ""),
            "period":    src.get("period_of_report", ""),
            "form":      src.get("form_type", form_type),
            "company":   src.get("entity_name", ""),
            "url":       f"https://www.sec.gov/Archives/{src.get('file_path', '')}",
        })

    # Try the ticker-to-CIK approach for better results
    try:
        cik_resp = requests.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22"
            f"&dateRange=custom&startdt={datetime.now().year - 3}-01-01&forms={form_type}",
            headers={"User-Agent": "autoearn/1.0 contact@example.com"},
            timeout=15,
        )
        cik_data = cik_resp.json()
        more_hits = cik_data.get("hits", {}).get("hits", [])
        seen_ids = {f["accession"] for f in filings}
        for hit in more_hits[:5]:
            src = hit.get("_source", {})
            acc = src.get("file_num", hit.get("_id", ""))
            if acc not in seen_ids:
                filings.append({
                    "accession": acc,
                    "filed":     src.get("file_date", ""),
                    "period":    src.get("period_of_report", ""),
                    "form":      src.get("form_type", form_type),
                    "company":   src.get("entity_name", ""),
                    "url":       f"https://www.sec.gov/Archives/{src.get('file_path', '')}",
                })
    except Exception:
        pass

    return {"symbol": symbol, "form_type": form_type, "filings": filings}


def stock_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    """
    Fetch recent news headlines for a ticker using Yahoo Finance.

    Returns {symbol, articles: [{title, publisher, url, published}]}.
    """
    symbol = symbol.upper().strip()

    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v1/finance/search",
            headers=_HEADERS,
            params={
                "q": symbol,
                "lang": "en-US",
                "region": "US",
                "quotesCount": 0,
                "newsCount": limit,
                "enableFuzzyQuery": False,
                "quotesQueryId": "tss_match_phrase_query",
                "multiQuoteQueryId": "multi_quote_single_token_query",
                "newsQueryId": "news_cie_vespa",
                "enableCb": True,
                "enableNavLinks": False,
                "enableEnhancedTrivialQuery": True,
            },
            timeout=12,
        )
        data = resp.json()
        raw_news = data.get("news", [])
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc), "articles": []}

    articles = []
    for item in raw_news[:limit]:
        pub_time = item.get("providerPublishTime", 0)
        pub_str  = datetime.fromtimestamp(pub_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") \
                   if pub_time else "unknown"
        articles.append({
            "title":     item.get("title", ""),
            "publisher": item.get("publisher", ""),
            "url":       item.get("link", ""),
            "published": pub_str,
            "type":      item.get("type", ""),
        })

    return {"symbol": symbol, "articles": articles}
