from __future__ import annotations

import json
import math
import re
import statistics
import datetime
from typing import Any


# ---------------------------------------------------------------------------
# competitive_pricing
# ---------------------------------------------------------------------------

def competitive_pricing(
    product_name: str,
    my_price: float,
    competitors: list[dict[str, Any]],
) -> str:
    """Analyze competitive pricing and return recommendations.

    Args:
        product_name: Name of the product being priced.
        my_price: Your current price.
        competitors: List of {name, price} dicts.

    Returns:
        JSON with market stats and pricing recommendation.
    """
    if not competitors:
        return json.dumps({"error": "No competitor data provided"})

    prices = [c["price"] for c in competitors if isinstance(c.get("price"), (int, float))]
    if not prices:
        return json.dumps({"error": "No valid competitor prices found"})

    avg_price = statistics.mean(prices)
    median_price = statistics.median(prices)
    min_price = min(prices)
    max_price = max(prices)
    price_range = max_price - min_price
    std_dev = statistics.stdev(prices) if len(prices) > 1 else 0.0

    # Position in market
    below_count = sum(1 for p in prices if my_price < p)
    percentile = (below_count / len(prices)) * 100

    # Recommendation logic
    gap_from_avg = my_price - avg_price
    gap_pct = (gap_from_avg / avg_price) * 100 if avg_price else 0

    if my_price > max_price * 1.05:
        position = "premium_outlier"
        recommendation = "Price significantly above market. Ensure clear value differentiation or consider lowering to max+5%."
        suggested_price = round(max_price * 1.05, 2)
    elif my_price < min_price * 0.95:
        position = "underpriced"
        recommendation = "Price significantly below market. Risk of perceived low quality; consider raising to near minimum competitor price."
        suggested_price = round(min_price * 0.95, 2)
    elif gap_pct > 10:
        position = "above_average"
        recommendation = "Priced above average. Justifiable if product has strong differentiators; otherwise consider matching average."
        suggested_price = round(avg_price * 1.05, 2)
    elif gap_pct < -10:
        position = "below_average"
        recommendation = "Priced below average. Opportunity to raise price and improve margins while staying competitive."
        suggested_price = round(avg_price * 0.95, 2)
    else:
        position = "competitive"
        recommendation = "Price is competitive and within normal market range. Small adjustments for psychological pricing may help."
        suggested_price = my_price

    return json.dumps({
        "product": product_name,
        "my_price": my_price,
        "market": {
            "avg": round(avg_price, 2),
            "median": round(median_price, 2),
            "min": round(min_price, 2),
            "max": round(max_price, 2),
            "range": round(price_range, 2),
            "std_dev": round(std_dev, 2),
        },
        "position": position,
        "percentile_vs_competitors": round(percentile, 1),
        "gap_from_avg_pct": round(gap_pct, 1),
        "recommendation": recommendation,
        "suggested_price": suggested_price,
        "competitors": competitors,
        "analyzed_at": datetime.datetime.utcnow().isoformat(),
    }, indent=2)


# ---------------------------------------------------------------------------
# psychological_pricing
# ---------------------------------------------------------------------------

def psychological_pricing(target_price: float) -> str:
    """Suggest psychological price points near the target price.

    Args:
        target_price: The raw target price.

    Returns:
        JSON list of psychological price suggestions with rationale.
    """
    suggestions: list[dict[str, Any]] = []

    floor = math.floor(target_price)
    ceiling = math.ceil(target_price)

    # .99 endings (charm pricing)
    for base in [floor - 1, floor, ceiling]:
        if base > 0:
            charm = base + 0.99
            suggestions.append({
                "price": charm,
                "strategy": "charm_pricing",
                "rationale": f"Ends in .99; perceived as significantly less than {base + 1}",
                "delta_from_target": round(charm - target_price, 2),
            })

    # .97 endings (used in retail/clearance)
    for base in [floor, ceiling]:
        if base > 0:
            p = base - 0.03
            if p > 0:
                suggestions.append({
                    "price": p,
                    "strategy": "just_below_round",
                    "rationale": "Ends in .97; common in discounting to signal value",
                    "delta_from_target": round(p - target_price, 2),
                })

    # Round number (prestige pricing — common in luxury/SaaS)
    for rnd in [floor, ceiling]:
        if rnd > 0:
            suggestions.append({
                "price": float(rnd),
                "strategy": "prestige_round",
                "rationale": "Round number signals confidence and premium quality",
                "delta_from_target": round(rnd - target_price, 2),
            })

    # 5-ending (e.g., $45, $95)
    nearest_five_down = math.floor(target_price / 5) * 5
    nearest_five_up = math.ceil(target_price / 5) * 5
    for pf in [nearest_five_down, nearest_five_up]:
        if pf > 0:
            suggestions.append({
                "price": float(pf),
                "strategy": "five_ending",
                "rationale": "Divisible by 5; easy to remember and compare",
                "delta_from_target": round(pf - target_price, 2),
            })

    # Deduplicate by price
    seen: set[float] = set()
    unique: list[dict[str, Any]] = []
    for s in suggestions:
        if s["price"] not in seen and s["price"] > 0:
            seen.add(s["price"])
            unique.append(s)

    unique.sort(key=lambda x: abs(x["delta_from_target"]))

    return json.dumps({
        "target_price": target_price,
        "suggestions": unique,
    }, indent=2)


# ---------------------------------------------------------------------------
# price_elasticity
# ---------------------------------------------------------------------------

def price_elasticity(price_history: list[float], quantity_history: list[float]) -> str:
    """Estimate price elasticity of demand from historical data.

    Args:
        price_history: List of historical prices (same order as quantities).
        quantity_history: List of corresponding quantities sold.

    Returns:
        JSON with elasticity estimate, interpretation, and per-period data.
    """
    if len(price_history) != len(quantity_history):
        return json.dumps({"error": "price_history and quantity_history must have equal length"})
    if len(price_history) < 2:
        return json.dumps({"error": "Need at least 2 data points"})

    elasticities: list[float] = []
    periods: list[dict[str, Any]] = []

    for i in range(1, len(price_history)):
        p0, p1 = price_history[i - 1], price_history[i]
        q0, q1 = quantity_history[i - 1], quantity_history[i]

        if p0 == 0 or q0 == 0:
            continue

        pct_change_price = (p1 - p0) / p0
        pct_change_qty = (q1 - q0) / q0

        if pct_change_price == 0:
            # No price change — skip for elasticity calc
            continue

        e = pct_change_qty / pct_change_price
        elasticities.append(e)
        periods.append({
            "period": i,
            "price_from": p0,
            "price_to": p1,
            "qty_from": q0,
            "qty_to": q1,
            "pct_price_change": round(pct_change_price * 100, 2),
            "pct_qty_change": round(pct_change_qty * 100, 2),
            "elasticity": round(e, 4),
        })

    if not elasticities:
        return json.dumps({"error": "Could not compute elasticity (no price changes found)"})

    avg_elasticity = statistics.mean(elasticities)
    abs_e = abs(avg_elasticity)

    if abs_e > 1:
        interpretation = "elastic"
        advice = "Demand is sensitive to price. Lowering price may increase total revenue."
    elif abs_e < 1:
        interpretation = "inelastic"
        advice = "Demand is insensitive to price. Raising price may increase total revenue."
    else:
        interpretation = "unit_elastic"
        advice = "Price changes have proportional effect on quantity. Revenue is stable across price changes."

    return json.dumps({
        "avg_elasticity": round(avg_elasticity, 4),
        "interpretation": interpretation,
        "advice": advice,
        "periods": periods,
    }, indent=2)


# ---------------------------------------------------------------------------
# ab_test_prices
# ---------------------------------------------------------------------------

def ab_test_prices(
    prices: list[float],
    conversions: list[int],
    visitors: list[int],
) -> str:
    """Statistical significance test for A/B price experiments.

    Uses a two-proportion z-test to compare conversion rates.

    Args:
        prices: List of prices tested (one per variant).
        conversions: Number of conversions per variant.
        visitors: Number of visitors per variant.

    Returns:
        JSON with conversion rates, z-score, p-value, and winner recommendation.
    """
    if not (len(prices) == len(conversions) == len(visitors)):
        return json.dumps({"error": "prices, conversions, and visitors must have equal length"})
    if len(prices) < 2:
        return json.dumps({"error": "Need at least 2 variants"})

    variants: list[dict[str, Any]] = []
    for i, (price, conv, vis) in enumerate(zip(prices, conversions, visitors)):
        rate = conv / vis if vis > 0 else 0.0
        revenue_per_visitor = price * rate
        variants.append({
            "variant": chr(65 + i),  # A, B, C, ...
            "price": price,
            "conversions": conv,
            "visitors": vis,
            "conversion_rate": round(rate, 4),
            "revenue_per_visitor": round(revenue_per_visitor, 4),
        })

    # For each pair, compute z-test (focus on first two for simplicity)
    results: list[dict[str, Any]] = []
    for i in range(len(variants)):
        for j in range(i + 1, len(variants)):
            va = variants[i]
            vb = variants[j]
            p_a = va["conversion_rate"]
            p_b = vb["conversion_rate"]
            n_a = va["visitors"]
            n_b = vb["visitors"]

            if n_a == 0 or n_b == 0:
                continue

            # Pooled proportion
            p_pool = (va["conversions"] + vb["conversions"]) / (n_a + n_b)
            if p_pool == 0 or p_pool == 1:
                continue

            se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
            if se == 0:
                continue

            z = (p_a - p_b) / se
            # Approximate p-value using error function (two-tailed)
            p_value = 2 * (1 - _normal_cdf(abs(z)))
            significant = p_value < 0.05

            lift = ((p_a - p_b) / p_b) * 100 if p_b != 0 else 0
            rev_lift = (
                (va["revenue_per_visitor"] - vb["revenue_per_visitor"])
                / vb["revenue_per_visitor"] * 100
                if vb["revenue_per_visitor"] != 0 else 0
            )

            results.append({
                "comparison": f"{va['variant']} vs {vb['variant']}",
                "z_score": round(z, 4),
                "p_value": round(p_value, 4),
                "statistically_significant": significant,
                "conversion_lift_pct": round(lift, 2),
                "revenue_lift_pct": round(rev_lift, 2),
                "winner": va["variant"] if p_a > p_b else vb["variant"],
            })

    best_variant = max(variants, key=lambda v: v["revenue_per_visitor"])

    return json.dumps({
        "variants": variants,
        "comparisons": results,
        "recommended_price": best_variant["price"],
        "recommended_variant": best_variant["variant"],
    }, indent=2)


def _normal_cdf(z: float) -> float:
    """Approximate standard normal CDF using math.erf."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ---------------------------------------------------------------------------
# value_based_pricing
# ---------------------------------------------------------------------------

def value_based_pricing(
    cost: float,
    target_margin: float,
    competitor_avg: float,
    unique_value_score: float,
) -> str:
    """Calculate a value-based recommended price.

    Args:
        cost: Unit cost to produce/deliver.
        target_margin: Desired gross margin as a decimal (e.g., 0.6 for 60%).
        competitor_avg: Average competitor price for similar product.
        unique_value_score: Score 0.0–1.0 representing unique value vs. competitors.

    Returns:
        JSON with recommended price and supporting breakdown.
    """
    if target_margin >= 1.0 or target_margin < 0:
        return json.dumps({"error": "target_margin must be between 0 and 1 (exclusive)"})

    # Cost-plus floor
    cost_plus_price = cost / (1 - target_margin) if target_margin < 1 else cost

    # Value premium: unique_value_score allows up to 50% above competitor avg
    value_premium_factor = 1.0 + (unique_value_score * 0.5)
    value_price = competitor_avg * value_premium_factor

    # Blend: weight cost-plus (40%) with value price (60%)
    recommended = 0.4 * cost_plus_price + 0.6 * value_price

    margin_at_recommended = (recommended - cost) / recommended if recommended > 0 else 0
    margin_vs_competitor = ((recommended - competitor_avg) / competitor_avg) * 100 if competitor_avg > 0 else 0

    return json.dumps({
        "recommended_price": round(recommended, 2),
        "cost_plus_price": round(cost_plus_price, 2),
        "value_based_price": round(value_price, 2),
        "inputs": {
            "cost": cost,
            "target_margin": target_margin,
            "competitor_avg": competitor_avg,
            "unique_value_score": unique_value_score,
        },
        "achieved_margin": round(margin_at_recommended, 4),
        "premium_vs_competitor_pct": round(margin_vs_competitor, 2),
    }, indent=2)


# ---------------------------------------------------------------------------
# dynamic_pricing_signal
# ---------------------------------------------------------------------------

def dynamic_pricing_signal(
    current_inventory: int,
    demand_velocity: float,
    competitor_price: float,
    cost: float,
) -> str:
    """Generate a raise/lower/hold pricing signal based on supply/demand dynamics.

    Args:
        current_inventory: Units currently in stock.
        demand_velocity: Units sold per day (recent average).
        competitor_price: Current competitor reference price.
        cost: Unit cost.

    Returns:
        JSON with signal ('raise', 'lower', 'hold'), confidence, and rationale.
    """
    if demand_velocity <= 0:
        days_of_stock = float("inf")
    else:
        days_of_stock = current_inventory / demand_velocity

    min_price = cost * 1.05  # Never price below 5% margin

    signals: list[tuple[str, float, str]] = []  # (signal, weight, reason)

    # Inventory pressure
    if days_of_stock < 7:
        signals.append(("raise", 0.9, f"Low stock: only {days_of_stock:.1f} days of inventory at current velocity"))
    elif days_of_stock > 60:
        signals.append(("lower", 0.7, f"High inventory: {days_of_stock:.1f} days of stock; need to accelerate sell-through"))
    else:
        signals.append(("hold", 0.5, f"Healthy inventory level: {days_of_stock:.1f} days"))

    # Demand momentum
    if demand_velocity > 0:
        # Normalize demand score relative to inventory
        demand_score = demand_velocity / max(current_inventory, 1)
        if demand_score > 0.05:
            signals.append(("raise", 0.7, "High demand velocity relative to stock level"))
        elif demand_score < 0.005:
            signals.append(("lower", 0.6, "Low demand velocity; reducing price may stimulate demand"))

    # Competitive position
    # We assume our current effective price is competitor_price * 1.0 (placeholder comparison)
    # Just score vs cost-based floor
    if competitor_price > 0:
        if min_price > competitor_price:
            signals.append(("hold", 1.0, "Cost floor exceeds competitor price; cannot lower without losing margin"))
        elif competitor_price < cost * 1.2:
            signals.append(("hold", 0.8, "Competitor pricing is close to cost; avoid a race to the bottom"))
        else:
            signals.append(("hold", 0.4, "Competitor pricing is reasonable; use other signals"))

    # Aggregate signals
    weights: dict[str, float] = {"raise": 0.0, "lower": 0.0, "hold": 0.0}
    for sig, weight, _ in signals:
        weights[sig] += weight

    total_weight = sum(weights.values()) or 1.0
    for k in weights:
        weights[k] /= total_weight

    final_signal = max(weights, key=lambda k: weights[k])
    confidence = round(weights[final_signal], 3)

    # Suggested adjustment
    if final_signal == "raise":
        adjustment_pct = min(15.0, round((1 - (days_of_stock / 30)) * 10, 1))
    elif final_signal == "lower":
        adjustment_pct = min(20.0, round(((days_of_stock - 30) / 30) * 10, 1))
    else:
        adjustment_pct = 0.0

    rationale = [r for _, _, r in signals]

    return json.dumps({
        "signal": final_signal,
        "confidence": confidence,
        "suggested_adjustment_pct": adjustment_pct,
        "days_of_stock": round(days_of_stock, 1) if days_of_stock != float("inf") else None,
        "demand_velocity": demand_velocity,
        "competitor_price": competitor_price,
        "cost_floor": round(min_price, 2),
        "rationale": rationale,
        "signal_weights": {k: round(v, 3) for k, v in weights.items()},
    }, indent=2)


# ---------------------------------------------------------------------------
# freemium_conversion_estimate
# ---------------------------------------------------------------------------

def freemium_conversion_estimate(
    free_users: int,
    feature_gate_score: float,
    competitor_premium_pct: float,
) -> str:
    """Estimate the expected freemium-to-premium conversion rate.

    Args:
        free_users: Current number of free tier users.
        feature_gate_score: 0.0–1.0; how strong/compelling the premium gate is.
        competitor_premium_pct: Fraction (0–1) of competitor users on paid plans.

    Returns:
        JSON with estimated conversion rate, expected paid users, and advice.
    """
    # Industry baseline: typical SaaS freemium conversion is ~2–5%
    baseline = 0.03

    # Feature gate amplifier: strong gate (score=1) can double conversion
    gate_multiplier = 1.0 + feature_gate_score

    # Competitor anchor: if competitors have high conversion, market is willing to pay
    market_multiplier = 1.0 + (competitor_premium_pct * 0.5)

    estimated_rate = baseline * gate_multiplier * market_multiplier
    # Cap at 25% (realistic ceiling for freemium)
    estimated_rate = min(estimated_rate, 0.25)

    expected_paid = int(free_users * estimated_rate)

    if estimated_rate < 0.02:
        advice = "Very low estimated conversion. Strengthen the value proposition of paid features."
    elif estimated_rate < 0.05:
        advice = "Below average conversion. Review feature gating — ensure premium features are desirable but not essential for basic use."
    elif estimated_rate < 0.10:
        advice = "Good conversion rate. Focus on onboarding to get free users to the 'aha moment' faster."
    else:
        advice = "Strong conversion signal. Consider testing a price increase or annual plan discount."

    return json.dumps({
        "estimated_conversion_rate": round(estimated_rate, 4),
        "estimated_conversion_pct": round(estimated_rate * 100, 2),
        "free_users": free_users,
        "expected_paid_users": expected_paid,
        "inputs": {
            "feature_gate_score": feature_gate_score,
            "competitor_premium_pct": competitor_premium_pct,
        },
        "advice": advice,
    }, indent=2)


# ---------------------------------------------------------------------------
# lifetime_value
# ---------------------------------------------------------------------------

def lifetime_value(
    avg_monthly_revenue: float,
    monthly_churn_rate: float,
    gross_margin: float,
) -> str:
    """Calculate customer Lifetime Value (LTV).

    Args:
        avg_monthly_revenue: Average monthly revenue per customer (MRR per customer).
        monthly_churn_rate: Monthly churn rate as a decimal (e.g., 0.05 for 5%).
        gross_margin: Gross margin as a decimal (e.g., 0.70 for 70%).

    Returns:
        JSON with LTV, average customer lifespan, LTV:CAC guidance.
    """
    if monthly_churn_rate <= 0:
        return json.dumps({"error": "monthly_churn_rate must be greater than 0"})
    if not (0 < gross_margin <= 1):
        return json.dumps({"error": "gross_margin must be between 0 and 1"})

    avg_lifespan_months = 1.0 / monthly_churn_rate
    avg_lifespan_years = avg_lifespan_months / 12

    # LTV = (MRR * gross_margin) / churn_rate
    ltv = (avg_monthly_revenue * gross_margin) / monthly_churn_rate

    # Annual revenue per customer
    annual_revenue = avg_monthly_revenue * 12

    # LTV:CAC benchmark guidance
    ltv_cac_good = ltv / 3  # Ideally CAC <= LTV/3
    payback_months = None

    if ltv > 0 and avg_monthly_revenue > 0:
        # Months to recover CAC if CAC == LTV/3
        cac_estimate = ltv / 3
        payback_months = round(cac_estimate / (avg_monthly_revenue * gross_margin), 1)

    return json.dumps({
        "ltv": round(ltv, 2),
        "avg_lifespan_months": round(avg_lifespan_months, 1),
        "avg_lifespan_years": round(avg_lifespan_years, 2),
        "annual_revenue_per_customer": round(annual_revenue, 2),
        "inputs": {
            "avg_monthly_revenue": avg_monthly_revenue,
            "monthly_churn_rate": monthly_churn_rate,
            "gross_margin": gross_margin,
        },
        "ltv_cac_guidance": {
            "healthy_max_cac": round(ltv_cac_good, 2),
            "ideal_payback_months": payback_months,
            "note": "Healthy SaaS LTV:CAC is 3:1 or better; payback under 12 months is ideal",
        },
    }, indent=2)


# ---------------------------------------------------------------------------
# break_even_analysis
# ---------------------------------------------------------------------------

def break_even_analysis(
    fixed_costs: float,
    variable_cost_per_unit: float,
    price_per_unit: float,
) -> str:
    """Calculate break-even point in units and revenue.

    Args:
        fixed_costs: Total fixed costs (rent, salaries, subscriptions).
        variable_cost_per_unit: Cost per unit produced/delivered.
        price_per_unit: Selling price per unit.

    Returns:
        JSON with break-even units, break-even revenue, and margin of safety info.
    """
    contribution_margin = price_per_unit - variable_cost_per_unit

    if contribution_margin <= 0:
        return json.dumps({
            "error": "Price per unit must exceed variable cost per unit",
            "contribution_margin": contribution_margin,
        })

    break_even_units = fixed_costs / contribution_margin
    break_even_revenue = break_even_units * price_per_unit

    cm_ratio = contribution_margin / price_per_unit

    # Sensitivity: impact of 10% price change
    price_up_10 = price_per_unit * 1.1
    be_units_price_up = fixed_costs / (price_up_10 - variable_cost_per_unit)

    price_down_10 = price_per_unit * 0.9
    if price_down_10 > variable_cost_per_unit:
        be_units_price_down = fixed_costs / (price_down_10 - variable_cost_per_unit)
    else:
        be_units_price_down = None

    return json.dumps({
        "break_even_units": round(break_even_units, 2),
        "break_even_revenue": round(break_even_revenue, 2),
        "contribution_margin": round(contribution_margin, 2),
        "contribution_margin_ratio": round(cm_ratio, 4),
        "inputs": {
            "fixed_costs": fixed_costs,
            "variable_cost_per_unit": variable_cost_per_unit,
            "price_per_unit": price_per_unit,
        },
        "sensitivity": {
            "be_units_at_price_plus_10pct": round(be_units_price_up, 2),
            "be_units_at_price_minus_10pct": round(be_units_price_down, 2) if be_units_price_down else "unprofitable",
        },
    }, indent=2)


# ---------------------------------------------------------------------------
# pricing_page_templates
# ---------------------------------------------------------------------------

def pricing_page_templates(plans: list[dict[str, Any]]) -> str:
    """Generate an HTML pricing table from plan definitions.

    Args:
        plans: List of {name, price, features, highlighted} dicts.

    Returns:
        HTML string for a pricing table.
    """
    def _escape(text: str) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    plan_cols = []
    for plan in plans:
        name = _escape(plan.get("name", "Plan"))
        price = plan.get("price", 0)
        features = plan.get("features", [])
        highlighted = plan.get("highlighted", False)

        price_str = f"${price:,.2f}/mo" if isinstance(price, (int, float)) else _escape(str(price))
        highlight_class = " pricing-card--highlight" if highlighted else ""
        badge = '<div class="pricing-badge">Most Popular</div>' if highlighted else ""

        feature_rows = "\n".join(
            f'        <li class="pricing-feature">&#10003; {_escape(f)}</li>'
            for f in features
        )

        plan_cols.append(f"""    <div class="pricing-card{highlight_class}">
      {badge}
      <h3 class="pricing-plan-name">{name}</h3>
      <div class="pricing-price">{price_str}</div>
      <ul class="pricing-features">
{feature_rows}
      </ul>
      <a href="#signup" class="pricing-cta">Get Started</a>
    </div>""")

    cards = "\n".join(plan_cols)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pricing</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f8fafc; color: #1a202c; }}
    .pricing-section {{ max-width: 1100px; margin: 60px auto; padding: 0 20px; text-align: center; }}
    .pricing-title {{ font-size: 2.5rem; font-weight: 700; margin-bottom: 12px; }}
    .pricing-subtitle {{ color: #718096; font-size: 1.1rem; margin-bottom: 48px; }}
    .pricing-grid {{ display: flex; gap: 24px; justify-content: center; flex-wrap: wrap; }}
    .pricing-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 36px 28px; width: 300px; position: relative; transition: box-shadow 0.2s; }}
    .pricing-card:hover {{ box-shadow: 0 8px 30px rgba(0,0,0,0.08); }}
    .pricing-card--highlight {{ border-color: #4f46e5; box-shadow: 0 0 0 2px #4f46e5; }}
    .pricing-badge {{ position: absolute; top: -12px; left: 50%; transform: translateX(-50%); background: #4f46e5; color: #fff; font-size: 0.75rem; font-weight: 600; padding: 4px 14px; border-radius: 12px; white-space: nowrap; }}
    .pricing-plan-name {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 12px; color: #2d3748; }}
    .pricing-price {{ font-size: 2.2rem; font-weight: 700; color: #4f46e5; margin-bottom: 24px; }}
    .pricing-features {{ list-style: none; text-align: left; margin-bottom: 28px; }}
    .pricing-feature {{ padding: 6px 0; color: #4a5568; font-size: 0.95rem; border-bottom: 1px solid #f0f4f8; }}
    .pricing-feature:last-child {{ border-bottom: none; }}
    .pricing-cta {{ display: inline-block; background: #4f46e5; color: #fff; padding: 12px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 0.95rem; transition: background 0.2s; }}
    .pricing-cta:hover {{ background: #4338ca; }}
    .pricing-card--highlight .pricing-cta {{ background: #4f46e5; }}
  </style>
</head>
<body>
  <section class="pricing-section">
    <h2 class="pricing-title">Simple, Transparent Pricing</h2>
    <p class="pricing-subtitle">Choose the plan that fits your needs. Upgrade or cancel anytime.</p>
    <div class="pricing-grid">
{cards}
    </div>
  </section>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# discount_strategy
# ---------------------------------------------------------------------------

def discount_strategy(
    original_price: float,
    inventory_age_days: int,
    target_sellthrough: float,
) -> str:
    """Recommend a discount percentage based on inventory age and sell-through targets.

    Args:
        original_price: Original/full price.
        inventory_age_days: How many days the item has been in inventory.
        target_sellthrough: Target sell-through rate 0–1 (e.g., 0.9 for 90%).

    Returns:
        JSON with recommended discount, discounted price, and rationale.
    """
    # Base discount curve: age-based urgency
    if inventory_age_days < 30:
        base_discount = 0.0
    elif inventory_age_days < 60:
        base_discount = 0.05
    elif inventory_age_days < 90:
        base_discount = 0.10
    elif inventory_age_days < 120:
        base_discount = 0.20
    elif inventory_age_days < 180:
        base_discount = 0.30
    else:
        base_discount = 0.40

    # Adjust for sell-through target
    if target_sellthrough >= 0.95:
        urgency_adder = 0.10
    elif target_sellthrough >= 0.80:
        urgency_adder = 0.05
    elif target_sellthrough >= 0.60:
        urgency_adder = 0.0
    else:
        urgency_adder = -0.05  # Low target; less pressure to discount

    recommended_discount = min(base_discount + urgency_adder, 0.70)  # Cap at 70%
    recommended_discount = max(recommended_discount, 0.0)

    discounted_price = original_price * (1 - recommended_discount)

    # Round to a psychological price
    discounted_price = math.floor(discounted_price) + 0.99 if discounted_price > 1 else discounted_price

    stages = [
        {"age_threshold_days": 30, "discount_pct": 0},
        {"age_threshold_days": 60, "discount_pct": 5},
        {"age_threshold_days": 90, "discount_pct": 10},
        {"age_threshold_days": 120, "discount_pct": 20},
        {"age_threshold_days": 180, "discount_pct": 30},
        {"age_threshold_days": 999, "discount_pct": 40},
    ]

    return json.dumps({
        "original_price": original_price,
        "recommended_discount_pct": round(recommended_discount * 100, 1),
        "discounted_price": round(discounted_price, 2),
        "savings": round(original_price - discounted_price, 2),
        "inventory_age_days": inventory_age_days,
        "target_sellthrough_pct": round(target_sellthrough * 100, 1),
        "discount_stages": stages,
        "rationale": (
            f"Item is {inventory_age_days} days old. "
            f"Base age discount: {base_discount*100:.0f}%. "
            f"Sell-through urgency adjustment: {urgency_adder*100:+.0f}%."
        ),
    }, indent=2)


# ---------------------------------------------------------------------------
# subscription_metrics
# ---------------------------------------------------------------------------

def subscription_metrics(
    mrr: float,
    new_mrr: float,
    churned_mrr: float,
    expansion_mrr: float,
) -> str:
    """Calculate key SaaS subscription metrics.

    Args:
        mrr: Current Monthly Recurring Revenue.
        new_mrr: New MRR added this month from new customers.
        churned_mrr: MRR lost this month from cancellations.
        expansion_mrr: MRR gained from upgrades/upsells to existing customers.

    Returns:
        JSON with net new MRR, growth rate, quick ratio, churn rate.
    """
    if mrr <= 0:
        return json.dumps({"error": "MRR must be positive"})

    net_new_mrr = new_mrr + expansion_mrr - churned_mrr
    end_of_month_mrr = mrr + net_new_mrr

    mrr_growth_rate = (net_new_mrr / mrr) * 100 if mrr > 0 else 0

    # Quick Ratio: (new + expansion) / churned — > 4 is excellent
    quick_ratio = (new_mrr + expansion_mrr) / churned_mrr if churned_mrr > 0 else float("inf")

    # Churn rate (MRR churn)
    mrr_churn_rate = (churned_mrr / mrr) * 100 if mrr > 0 else 0

    # ARR projection
    arr = end_of_month_mrr * 12

    # Health assessment
    if quick_ratio == float("inf"):
        health = "excellent"
    elif quick_ratio >= 4:
        health = "excellent"
    elif quick_ratio >= 2:
        health = "good"
    elif quick_ratio >= 1:
        health = "neutral"
    else:
        health = "at_risk"

    return json.dumps({
        "mrr": round(mrr, 2),
        "end_of_month_mrr": round(end_of_month_mrr, 2),
        "arr_projection": round(arr, 2),
        "net_new_mrr": round(net_new_mrr, 2),
        "mrr_growth_rate_pct": round(mrr_growth_rate, 2),
        "quick_ratio": round(quick_ratio, 2) if quick_ratio != float("inf") else "inf",
        "mrr_churn_rate_pct": round(mrr_churn_rate, 2),
        "components": {
            "new_mrr": round(new_mrr, 2),
            "expansion_mrr": round(expansion_mrr, 2),
            "churned_mrr": round(churned_mrr, 2),
        },
        "health": health,
        "health_note": "Quick Ratio: >4 excellent, 2–4 good, 1–2 neutral, <1 at risk",
    }, indent=2)


# ---------------------------------------------------------------------------
# anchor_pricing
# ---------------------------------------------------------------------------

def anchor_pricing(premium_price: float, target_price: float) -> str:
    """Generate an anchoring pricing strategy.

    Shows how to position a premium tier so the target price feels like strong value.

    Args:
        premium_price: The high anchor price (premium/enterprise plan).
        target_price: The plan you actually want customers to choose.

    Returns:
        JSON with anchoring strategy, positioning copy, and recommended display order.
    """
    if premium_price <= target_price:
        return json.dumps({
            "error": "premium_price must be greater than target_price for effective anchoring"
        })

    savings = premium_price - target_price
    savings_pct = (savings / premium_price) * 100

    # Decoy/anchor ratio: ideal anchor is 2x–3x the target
    ratio = premium_price / target_price

    if ratio < 1.5:
        anchor_strength = "weak"
        anchor_note = "Anchor is too close to target. Consider increasing premium price or adding a third, higher tier."
    elif ratio <= 2.5:
        anchor_strength = "strong"
        anchor_note = "Good anchor ratio. Target price will feel like strong value."
    elif ratio <= 4.0:
        anchor_strength = "very_strong"
        anchor_note = "Excellent anchor. Target will look like an exceptional deal."
    else:
        anchor_strength = "extreme"
        anchor_note = "Anchor may seem unrealistic. Customers might question value of premium tier."

    copy_suggestions = [
        f"Save ${savings:.2f} ({savings_pct:.0f}% off) compared to our Premium plan",
        f"Everything in Premium — at a fraction of the cost",
        f"Most teams choose this plan over our ${premium_price:.2f} option",
        f"Best value: ${target_price:.2f}/mo vs ${premium_price:.2f}/mo for full access",
    ]

    display_order = [
        {"position": 1, "role": "anchor", "price": premium_price, "label": "Premium (Full Access)"},
        {"position": 2, "role": "target", "price": target_price, "label": "Most Popular"},
        {"position": 3, "role": "entry", "price": round(target_price * 0.5, 2), "label": "Starter"},
    ]

    return json.dumps({
        "anchor_price": premium_price,
        "target_price": target_price,
        "savings": round(savings, 2),
        "savings_pct": round(savings_pct, 1),
        "anchor_ratio": round(ratio, 2),
        "anchor_strength": anchor_strength,
        "anchor_note": anchor_note,
        "copy_suggestions": copy_suggestions,
        "recommended_display_order": display_order,
        "strategy": (
            "Lead with the anchor price first in your display. "
            "Highlight the target plan as 'Most Popular'. "
            "Always show both prices side-by-side so the savings are immediately visible."
        ),
    }, indent=2)


# ---------------------------------------------------------------------------
# price_to_value_matrix
# ---------------------------------------------------------------------------

def price_to_value_matrix(
    features: list[str],
    importance_weights: list[float],
) -> str:
    """Build a weighted feature scoring matrix for pricing tiers.

    Args:
        features: List of feature names.
        importance_weights: Importance weight for each feature (0–10).

    Returns:
        JSON with normalized scores, tier suggestions, and tier boundaries.
    """
    if len(features) != len(importance_weights):
        return json.dumps({"error": "features and importance_weights must have equal length"})
    if not features:
        return json.dumps({"error": "features list is empty"})

    total_weight = sum(importance_weights)
    if total_weight == 0:
        return json.dumps({"error": "All importance weights are zero"})

    normalized = [w / total_weight for w in importance_weights]

    # Score each feature for inclusion in Standard (0.5 threshold) and Premium (0.2 threshold)
    scored: list[dict[str, Any]] = []
    for feat, raw_w, norm_w in zip(features, importance_weights, normalized):
        scored.append({
            "feature": feat,
            "raw_weight": raw_w,
            "normalized_weight": round(norm_w, 4),
            "pct_of_total_value": round(norm_w * 100, 2),
        })

    # Sort by value contribution
    scored_sorted = sorted(scored, key=lambda x: x["normalized_weight"], reverse=True)

    # Build tiers using cumulative value
    cumulative = 0.0
    tier_assignments: dict[str, str] = {}
    for item in scored_sorted:
        cumulative += item["normalized_weight"]
        if cumulative <= 0.40:
            tier = "starter"
        elif cumulative <= 0.75:
            tier = "standard"
        else:
            tier = "premium"
        tier_assignments[item["feature"]] = tier
        item["suggested_tier"] = tier

    # Aggregate per tier
    tier_value: dict[str, float] = {"starter": 0.0, "standard": 0.0, "premium": 0.0}
    tier_features: dict[str, list[str]] = {"starter": [], "standard": [], "premium": []}
    for item in scored_sorted:
        t = item["suggested_tier"]
        tier_value[t] += item["normalized_weight"]
        tier_features[t].append(item["feature"])

    # Pricing multiplier suggestions based on value share
    starter_share = tier_value["starter"]
    standard_share = starter_share + tier_value["standard"]

    # If base price is 1.0 (relative):
    pricing_multipliers = {
        "starter": 1.0,
        "standard": round(1.0 / starter_share, 2) if starter_share > 0 else 2.0,
        "premium": round(1.0 / (starter_share * 0.5), 2) if starter_share > 0 else 4.0,
    }

    return json.dumps({
        "features_scored": scored_sorted,
        "tiers": {
            "starter": {
                "features": tier_features["starter"],
                "cumulative_value_pct": round(tier_value["starter"] * 100, 1),
                "price_multiplier": pricing_multipliers["starter"],
            },
            "standard": {
                "features": tier_features["standard"],
                "cumulative_value_pct": round(tier_value["standard"] * 100, 1),
                "cumulative_incl_lower_pct": round(standard_share * 100, 1),
                "price_multiplier": pricing_multipliers["standard"],
            },
            "premium": {
                "features": tier_features["premium"],
                "cumulative_value_pct": round(tier_value["premium"] * 100, 1),
                "price_multiplier": pricing_multipliers["premium"],
            },
        },
        "note": (
            "Price multipliers are relative to the Starter tier base price. "
            "Features in 'starter' represent the top 40% of cumulative value; "
            "'standard' covers 40–75%; 'premium' covers the remaining 25%."
        ),
    }, indent=2)
