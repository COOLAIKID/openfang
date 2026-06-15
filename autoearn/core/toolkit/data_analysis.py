from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_output_path(filename: str) -> Path:
    """Return an absolute path inside OUTPUT_DIR for the given filename."""
    return OUTPUT_DIR / filename


def _coerce_value(raw: str) -> float | str:
    """Try to convert a string to float; return the original string on failure."""
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        return raw


def _read_csv_raw(path: str | Path) -> tuple[list[str], list[list[str]]]:
    """
    Read a CSV file.  *path* may be:
      - an absolute path, or
      - a bare filename that lives under OUTPUT_DIR.
    Returns (headers, rows) where every cell is a plain string.
    """
    p = Path(path)
    if not p.is_absolute():
        p = OUTPUT_DIR / p
    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        headers: list[str] = next(reader, [])
        rows: list[list[str]] = list(reader)
    return headers, rows


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    """Write headers + rows to a CSV file at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_csv(path: str) -> str:
    """
    Read a CSV file from the output directory (or an absolute path).

    Returns a JSON string:
        {
            "headers": [...],
            "rows":    [[...], ...],
            "shape":   [num_rows, num_cols]
        }
    """
    headers, rows = _read_csv_raw(path)
    result = {
        "headers": headers,
        "rows": rows,
        "shape": [len(rows), len(headers)],
    }
    return json.dumps(result, ensure_ascii=False)


def summarize_csv(path: str) -> str:
    """
    Compute per-column statistics for a CSV file.

    Returns a JSON string mapping each column name to:
        {count, nulls, min, max, mean, median}
    Numeric statistics are only filled for columns whose non-null values can
    all be parsed as numbers; otherwise they are None.
    """
    headers, rows = _read_csv_raw(path)
    summary: dict[str, dict] = {}

    for col_idx, header in enumerate(headers):
        raw_values = [row[col_idx] if col_idx < len(row) else "" for row in rows]
        nulls = sum(1 for v in raw_values if v.strip() == "")
        non_null = [v for v in raw_values if v.strip() != ""]

        numeric: list[float] = []
        all_numeric = True
        for v in non_null:
            try:
                numeric.append(float(v))
            except ValueError:
                all_numeric = False
                break

        if all_numeric and numeric:
            col_stats: dict = {
                "count": len(non_null),
                "nulls": nulls,
                "min": min(numeric),
                "max": max(numeric),
                "mean": round(statistics.mean(numeric), 6),
                "median": statistics.median(numeric),
            }
        else:
            col_stats = {
                "count": len(non_null),
                "nulls": nulls,
                "min": min(non_null, default=None),
                "max": max(non_null, default=None),
                "mean": None,
                "median": None,
            }

        summary[header] = col_stats

    return json.dumps(summary, ensure_ascii=False)


def filter_csv(path: str, column: str, operator: str, value: str) -> str:
    """
    Filter rows in a CSV by a column condition.

    Supported operators: eq, ne, gt, lt, contains, startswith.

    Returns a JSON string:
        {"headers": [...], "rows": [[...], ...], "shape": [n, m]}
    """
    headers, rows = _read_csv_raw(path)

    if column not in headers:
        return json.dumps({"error": f"Column '{column}' not found", "headers": headers})

    col_idx = headers.index(column)
    op = operator.lower().strip()

    def _matches(row: list[str]) -> bool:
        cell = row[col_idx] if col_idx < len(row) else ""
        if op == "eq":
            return cell == value
        if op == "ne":
            return cell != value
        if op == "contains":
            return value.lower() in cell.lower()
        if op == "startswith":
            return cell.lower().startswith(value.lower())
        # Numeric comparisons
        try:
            cell_f = float(cell)
            val_f = float(value)
        except ValueError:
            return False
        if op == "gt":
            return cell_f > val_f
        if op == "lt":
            return cell_f < val_f
        return False

    filtered = [row for row in rows if _matches(row)]
    return json.dumps(
        {"headers": headers, "rows": filtered, "shape": [len(filtered), len(headers)]},
        ensure_ascii=False,
    )


def sort_csv(path: str, column: str, ascending: bool = True) -> str:
    """
    Sort rows in a CSV by a column.

    Returns a JSON string:
        {"headers": [...], "rows": [[...], ...], "shape": [n, m]}
    """
    headers, rows = _read_csv_raw(path)

    if column not in headers:
        return json.dumps({"error": f"Column '{column}' not found"})

    col_idx = headers.index(column)

    def _sort_key(row: list[str]):
        cell = row[col_idx] if col_idx < len(row) else ""
        try:
            return (0, float(cell))
        except ValueError:
            return (1, cell.lower())

    sorted_rows = sorted(rows, key=_sort_key, reverse=not ascending)
    return json.dumps(
        {"headers": headers, "rows": sorted_rows, "shape": [len(sorted_rows), len(headers)]},
        ensure_ascii=False,
    )


def group_and_count(path: str, column: str) -> str:
    """
    Group rows by a column and count occurrences.

    Returns a JSON string — a list of {"value": ..., "count": ...} objects,
    sorted by count descending.
    """
    headers, rows = _read_csv_raw(path)

    if column not in headers:
        return json.dumps({"error": f"Column '{column}' not found"})

    col_idx = headers.index(column)
    counts: dict[str, int] = {}
    for row in rows:
        cell = row[col_idx] if col_idx < len(row) else ""
        counts[cell] = counts.get(cell, 0) + 1

    result = sorted(
        [{"value": v, "count": c} for v, c in counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )
    return json.dumps(result, ensure_ascii=False)


def merge_csv_files(paths: list[str], on_column: str) -> str:
    """
    Inner-join multiple CSV files on a common column.

    The merged file is saved as output/merged_<timestamp>.csv.
    Returns the absolute path string of the saved file.
    """
    if not paths:
        return json.dumps({"error": "No paths provided"})

    # Load first file as base
    base_headers, base_rows = _read_csv_raw(paths[0])
    if on_column not in base_headers:
        return json.dumps({"error": f"Join column '{on_column}' not in {paths[0]}"})

    # Build a dict keyed by the join-column value for base
    base_key_idx = base_headers.index(on_column)
    base_dict: dict[str, list[str]] = {}
    for row in base_rows:
        key = row[base_key_idx] if base_key_idx < len(row) else ""
        base_dict[key] = row

    merged_headers = list(base_headers)
    merged_dict: dict[str, list[str]] = {k: list(v) for k, v in base_dict.items()}

    for extra_path in paths[1:]:
        extra_headers, extra_rows = _read_csv_raw(extra_path)
        if on_column not in extra_headers:
            return json.dumps({"error": f"Join column '{on_column}' not in {extra_path}"})

        extra_key_idx = extra_headers.index(on_column)
        extra_non_key = [h for h in extra_headers if h != on_column]

        # Extend merged headers with new columns (avoid duplicates)
        for h in extra_non_key:
            if h not in merged_headers:
                merged_headers.append(h)
            else:
                merged_headers.append(f"{h}_{Path(extra_path).stem}")

        # Build lookup for extra file
        extra_lookup: dict[str, list[str]] = {}
        for row in extra_rows:
            key = row[extra_key_idx] if extra_key_idx < len(row) else ""
            non_key_vals = [
                row[extra_headers.index(h)] if extra_headers.index(h) < len(row) else ""
                for h in extra_non_key
            ]
            extra_lookup[key] = non_key_vals

        # Inner join: only keep keys present in both
        new_merged: dict[str, list[str]] = {}
        for key, merged_row in merged_dict.items():
            if key in extra_lookup:
                new_merged[key] = merged_row + extra_lookup[key]
        merged_dict = new_merged

    # Build final rows in header order
    final_rows = list(merged_dict.values())
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = _resolve_output_path(f"merged_{timestamp}.csv")
    _write_csv(out_path, merged_headers, final_rows)
    return str(out_path)


def compute_stats(numbers: list[float | int]) -> str:
    """
    Compute descriptive statistics for a list of numbers.

    Returns JSON with: mean, median, std, p25, p75, p95, min, max.
    """
    if not numbers:
        return json.dumps({"error": "Empty list"})

    nums = [float(n) for n in numbers]
    nums_sorted = sorted(nums)
    n = len(nums_sorted)

    def _percentile(data: list[float], pct: float) -> float:
        if len(data) == 1:
            return data[0]
        idx = (pct / 100) * (len(data) - 1)
        lo = int(idx)
        hi = lo + 1
        frac = idx - lo
        if hi >= len(data):
            return data[-1]
        return data[lo] + frac * (data[hi] - data[lo])

    std = statistics.pstdev(nums) if n > 1 else 0.0

    result = {
        "mean": statistics.mean(nums),
        "median": statistics.median(nums),
        "std": std,
        "p25": _percentile(nums_sorted, 25),
        "p75": _percentile(nums_sorted, 75),
        "p95": _percentile(nums_sorted, 95),
        "min": nums_sorted[0],
        "max": nums_sorted[-1],
        "count": n,
    }
    return json.dumps(result, ensure_ascii=False)


def detect_trend(values: list[float | int]) -> str:
    """
    Fit a simple linear regression to a series of values and report trend.

    Returns JSON with: slope, intercept, r_squared, direction ('up'/'down'/'flat').
    """
    if len(values) < 2:
        return json.dumps({"error": "Need at least 2 values"})

    n = len(values)
    xs = list(range(n))
    ys = [float(v) for v in values]

    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    ss_xy = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    ss_xx = sum((xs[i] - x_mean) ** 2 for i in range(n))
    ss_yy = sum((ys[i] - y_mean) ** 2 for i in range(n))

    slope = ss_xy / ss_xx if ss_xx != 0 else 0.0
    intercept = y_mean - slope * x_mean

    if ss_xx == 0 or ss_yy == 0:
        r_squared = 0.0
    else:
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

    # Classify direction: "flat" if slope magnitude is < 1% of mean abs value
    abs_mean = sum(abs(v) for v in ys) / n if ys else 1
    threshold = 0.01 * abs_mean if abs_mean != 0 else 1e-9
    if abs(slope) < threshold:
        direction = "flat"
    elif slope > 0:
        direction = "up"
    else:
        direction = "down"

    result = {
        "slope": round(slope, 8),
        "intercept": round(intercept, 8),
        "r_squared": round(r_squared, 6),
        "direction": direction,
        "n": n,
    }
    return json.dumps(result, ensure_ascii=False)


def json_to_csv(json_str: str, output_path: str) -> str:
    """
    Convert a JSON array of objects to a CSV file saved under output/.

    *output_path* may be a bare filename; the file is always written inside OUTPUT_DIR.
    Returns the absolute path of the created file.
    """
    data = json.loads(json_str)
    if not isinstance(data, list) or not data:
        return json.dumps({"error": "json_str must be a non-empty JSON array"})

    # Collect all keys as headers (preserving insertion order)
    headers: list[str] = []
    for item in data:
        if isinstance(item, dict):
            for key in item:
                if key not in headers:
                    headers.append(key)

    rows = [[str(item.get(h, "")) for h in headers] for item in data if isinstance(item, dict)]

    out = _resolve_output_path(Path(output_path).name)
    _write_csv(out, headers, rows)
    return str(out)


def parse_json_path(json_str: str, json_path: str) -> str:
    """
    Extract a value from a JSON document using dot-notation path.

    Example path: 'data.items.0.price'
    Numeric segments are treated as list indices.
    Returns the extracted value serialised as JSON, or an error object.
    """
    data = json.loads(json_str)
    segments = json_path.split(".")

    current = data
    for seg in segments:
        if seg == "":
            continue
        if isinstance(current, list):
            try:
                current = current[int(seg)]
            except (ValueError, IndexError) as exc:
                return json.dumps({"error": str(exc), "path": json_path})
        elif isinstance(current, dict):
            if seg not in current:
                return json.dumps({"error": f"Key '{seg}' not found", "path": json_path})
            current = current[seg]
        else:
            return json.dumps({"error": f"Cannot traverse into {type(current).__name__}", "path": json_path})

    return json.dumps(current, ensure_ascii=False)


def normalize_numbers(values: list[float | int], method: str = "minmax") -> str:
    """
    Normalize a list of numbers.

    method='minmax'  → scale to [0, 1]
    method='zscore'  → subtract mean, divide by std dev

    Returns a JSON list of normalized floats.
    """
    if not values:
        return json.dumps([])

    nums = [float(v) for v in values]
    method = method.lower().strip()

    if method == "minmax":
        lo, hi = min(nums), max(nums)
        rng = hi - lo
        if rng == 0:
            normalized = [0.0] * len(nums)
        else:
            normalized = [(v - lo) / rng for v in nums]
    elif method in ("zscore", "z-score", "z_score"):
        if len(nums) < 2:
            return json.dumps([0.0] * len(nums))
        mu = statistics.mean(nums)
        sigma = statistics.pstdev(nums)
        if sigma == 0:
            normalized = [0.0] * len(nums)
        else:
            normalized = [(v - mu) / sigma for v in nums]
    else:
        return json.dumps({"error": f"Unknown method '{method}'. Use 'minmax' or 'zscore'."})

    return json.dumps([round(v, 8) for v in normalized], ensure_ascii=False)


def correlation(xs: list[float | int], ys: list[float | int]) -> float:
    """
    Compute the Pearson correlation coefficient between two equal-length lists.

    Returns a float in [-1, 1], or 0.0 if the correlation is undefined.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    if len(xs) < 2:
        return 0.0

    x_vals = [float(v) for v in xs]
    y_vals = [float(v) for v in ys]

    n = len(x_vals)
    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n

    numerator = sum((x_vals[i] - x_mean) * (y_vals[i] - y_mean) for i in range(n))
    denom_x = math.sqrt(sum((v - x_mean) ** 2 for v in x_vals))
    denom_y = math.sqrt(sum((v - y_mean) ** 2 for v in y_vals))

    if denom_x == 0 or denom_y == 0:
        return 0.0

    return round(numerator / (denom_x * denom_y), 8)


def moving_average(values: list[float | int], window: int) -> str:
    """
    Compute the simple moving average over a sliding window.

    Returns a JSON list; the first (window-1) positions are None.
    """
    if window < 1:
        return json.dumps({"error": "Window must be >= 1"})

    nums = [float(v) for v in values]
    result: list[float | None] = [None] * (window - 1)
    for i in range(window - 1, len(nums)):
        avg = sum(nums[i - window + 1: i + 1]) / window
        result.append(round(avg, 8))

    return json.dumps(result, ensure_ascii=False)


def find_outliers(values: list[float | int], method: str = "iqr") -> str:
    """
    Detect outlier indices in a list of numbers.

    method='iqr'     → values outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR]
    method='zscore'  → values with |z| > 3

    Returns JSON: {"outlier_indices": [...], "outlier_values": [...]}
    """
    if not values:
        return json.dumps({"outlier_indices": [], "outlier_values": []})

    nums = [float(v) for v in values]
    method = method.lower().strip()
    outlier_indices: list[int] = []

    if method == "iqr":
        sorted_nums = sorted(nums)
        n = len(sorted_nums)

        def _pct(data: list[float], p: float) -> float:
            idx = p * (len(data) - 1)
            lo = int(idx)
            hi = lo + 1
            frac = idx - lo
            return data[lo] if hi >= len(data) else data[lo] + frac * (data[hi] - data[lo])

        q1 = _pct(sorted_nums, 0.25)
        q3 = _pct(sorted_nums, 0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_indices = [i for i, v in enumerate(nums) if v < lower or v > upper]

    elif method in ("zscore", "z-score", "z_score"):
        if len(nums) < 2:
            return json.dumps({"outlier_indices": [], "outlier_values": []})
        mu = statistics.mean(nums)
        sigma = statistics.pstdev(nums)
        if sigma == 0:
            return json.dumps({"outlier_indices": [], "outlier_values": []})
        outlier_indices = [i for i, v in enumerate(nums) if abs((v - mu) / sigma) > 3]
    else:
        return json.dumps({"error": f"Unknown method '{method}'. Use 'iqr' or 'zscore'."})

    return json.dumps(
        {
            "outlier_indices": outlier_indices,
            "outlier_values": [nums[i] for i in outlier_indices],
        },
        ensure_ascii=False,
    )


def generate_chart_data(
    x_values: list,
    y_values: list[float | int],
    chart_type: str = "line",
) -> str:
    """
    Build a Chart.js-compatible configuration object for a simple chart.

    Supported chart_type values: 'line', 'bar', 'scatter', 'pie', 'doughnut'.
    Returns a JSON string that can be passed directly to new Chart(ctx, config).
    """
    chart_type = chart_type.lower().strip()

    _PALETTE = [
        "rgba(54,162,235,0.7)",
        "rgba(255,99,132,0.7)",
        "rgba(75,192,192,0.7)",
        "rgba(255,205,86,0.7)",
        "rgba(153,102,255,0.7)",
        "rgba(255,159,64,0.7)",
        "rgba(201,203,207,0.7)",
    ]

    x_labels = [str(v) for v in x_values]
    y_nums = [float(v) for v in y_values]

    if chart_type in ("pie", "doughnut"):
        dataset = {
            "data": y_nums,
            "backgroundColor": [_PALETTE[i % len(_PALETTE)] for i in range(len(y_nums))],
        }
        config = {
            "type": chart_type,
            "data": {"labels": x_labels, "datasets": [dataset]},
            "options": {"responsive": True},
        }
    elif chart_type == "scatter":
        points = [{"x": float(x_values[i]), "y": y_nums[i]} for i in range(min(len(x_values), len(y_nums)))]
        dataset = {
            "label": "Data",
            "data": points,
            "backgroundColor": _PALETTE[0],
        }
        config = {
            "type": "scatter",
            "data": {"datasets": [dataset]},
            "options": {
                "responsive": True,
                "scales": {"x": {"type": "linear", "position": "bottom"}, "y": {}},
            },
        }
    else:
        # line or bar
        border_color = _PALETTE[0].replace("0.7", "1")
        dataset = {
            "label": "Dataset",
            "data": y_nums,
            "backgroundColor": _PALETTE[0],
            "borderColor": border_color,
            "borderWidth": 2,
            "fill": chart_type == "line",
            "tension": 0.3 if chart_type == "line" else 0,
        }
        config = {
            "type": chart_type,
            "data": {"labels": x_labels, "datasets": [dataset]},
            "options": {
                "responsive": True,
                "plugins": {"legend": {"position": "top"}},
                "scales": {"y": {"beginAtZero": True}},
            },
        }

    return json.dumps(config, ensure_ascii=False)
