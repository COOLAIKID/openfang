"""Tests for core/toolkit/data_analysis.py — CSV/stats analysis toolkit."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from core.toolkit import data_analysis as da


# ---------------------------------------------------------------------------
# CSV fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def simple_csv(tmp_path) -> Path:
    """A simple numeric CSV: name, age, salary."""
    p = tmp_path / "simple.csv"
    rows = [
        ["name", "age", "salary"],
        ["Alice", "30", "75000"],
        ["Bob", "25", "55000"],
        ["Carol", "35", "90000"],
        ["Dave", "28", "62000"],
        ["Eve", "32", "80000"],
    ]
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    return p


@pytest.fixture()
def category_csv(tmp_path) -> Path:
    """CSV with a category column for grouping."""
    p = tmp_path / "categories.csv"
    rows = [
        ["product", "category", "price"],
        ["Widget A", "electronics", "29.99"],
        ["Widget B", "electronics", "49.99"],
        ["Gadget X", "gadgets", "19.99"],
        ["Gadget Y", "gadgets", "39.99"],
        ["Gadget Z", "gadgets", "59.99"],
        ["Tool 1", "tools", "9.99"],
    ]
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    return p


@pytest.fixture()
def mixed_csv(tmp_path) -> Path:
    """CSV with text and numeric columns mixed."""
    p = tmp_path / "mixed.csv"
    rows = [
        ["id", "label", "value"],
        ["1", "alpha", "10.5"],
        ["2", "beta", "20.0"],
        ["3", "gamma", ""],  # empty value
        ["4", "delta", "15.3"],
    ]
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    return p


# ---------------------------------------------------------------------------
# load_csv
# ---------------------------------------------------------------------------

class TestLoadCSV:
    def test_load_returns_json(self, simple_csv):
        result = da.load_csv(str(simple_csv))
        data = json.loads(result)
        assert "headers" in data
        assert "rows" in data
        assert "shape" in data

    def test_load_headers_correct(self, simple_csv):
        data = json.loads(da.load_csv(str(simple_csv)))
        assert data["headers"] == ["name", "age", "salary"]

    def test_load_row_count(self, simple_csv):
        data = json.loads(da.load_csv(str(simple_csv)))
        assert data["shape"][0] == 5  # 5 data rows

    def test_load_col_count(self, simple_csv):
        data = json.loads(da.load_csv(str(simple_csv)))
        assert data["shape"][1] == 3  # 3 columns

    def test_load_rows_are_lists(self, simple_csv):
        data = json.loads(da.load_csv(str(simple_csv)))
        assert all(isinstance(r, list) for r in data["rows"])

    def test_load_first_row_values(self, simple_csv):
        data = json.loads(da.load_csv(str(simple_csv)))
        first = data["rows"][0]
        assert "Alice" in first
        assert "30" in first


# ---------------------------------------------------------------------------
# summarize_csv
# ---------------------------------------------------------------------------

class TestSummarizeCSV:
    def test_summarize_returns_json(self, simple_csv):
        result = da.summarize_csv(str(simple_csv))
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_summarize_all_columns_present(self, simple_csv):
        data = json.loads(da.summarize_csv(str(simple_csv)))
        for col in ("name", "age", "salary"):
            assert col in data

    def test_summarize_numeric_stats(self, simple_csv):
        data = json.loads(da.summarize_csv(str(simple_csv)))
        age_stats = data["age"]
        assert age_stats["min"] == 25.0
        assert age_stats["max"] == 35.0
        assert age_stats["count"] == 5

    def test_summarize_mean_correct(self, simple_csv):
        data = json.loads(da.summarize_csv(str(simple_csv)))
        # Ages: 30, 25, 35, 28, 32 → mean = 150/5 = 30.0
        assert abs(data["age"]["mean"] - 30.0) < 0.01

    def test_summarize_text_column_no_numeric_stats(self, simple_csv):
        data = json.loads(da.summarize_csv(str(simple_csv)))
        assert data["name"]["mean"] is None

    def test_summarize_nulls_counted(self, mixed_csv):
        data = json.loads(da.summarize_csv(str(mixed_csv)))
        # value column has one empty cell
        assert data["value"]["nulls"] == 1

    def test_summarize_count_excludes_nulls(self, mixed_csv):
        data = json.loads(da.summarize_csv(str(mixed_csv)))
        assert data["value"]["count"] == 3  # 4 rows minus 1 null


# ---------------------------------------------------------------------------
# filter_csv
# ---------------------------------------------------------------------------

class TestFilterCSV:
    def test_filter_eq(self, category_csv):
        result = json.loads(da.filter_csv(str(category_csv), "category", "eq", "gadgets"))
        assert result["shape"][0] == 3

    def test_filter_ne(self, category_csv):
        result = json.loads(da.filter_csv(str(category_csv), "category", "ne", "gadgets"))
        rows_not_gadgets = result["shape"][0]
        assert rows_not_gadgets == 3  # electronics (2) + tools (1)

    def test_filter_contains(self, category_csv):
        result = json.loads(da.filter_csv(str(category_csv), "product", "contains", "Widget"))
        assert result["shape"][0] == 2

    def test_filter_startswith(self, category_csv):
        result = json.loads(da.filter_csv(str(category_csv), "product", "startswith", "Gadget"))
        assert result["shape"][0] == 3

    def test_filter_gt(self, category_csv):
        result = json.loads(da.filter_csv(str(category_csv), "price", "gt", "30"))
        rows = result["rows"]
        prices = [float(r[2]) for r in rows]
        assert all(p > 30.0 for p in prices)

    def test_filter_lt(self, category_csv):
        result = json.loads(da.filter_csv(str(category_csv), "price", "lt", "25"))
        rows = result["rows"]
        prices = [float(r[2]) for r in rows]
        assert all(p < 25.0 for p in prices)

    def test_filter_unknown_column_returns_error(self, simple_csv):
        result = json.loads(da.filter_csv(str(simple_csv), "nonexistent", "eq", "value"))
        assert "error" in result

    def test_filter_preserves_headers(self, simple_csv):
        result = json.loads(da.filter_csv(str(simple_csv), "age", "gt", "28"))
        assert result["headers"] == ["name", "age", "salary"]


# ---------------------------------------------------------------------------
# sort_csv
# ---------------------------------------------------------------------------

class TestSortCSV:
    def test_sort_ascending(self, simple_csv):
        result = json.loads(da.sort_csv(str(simple_csv), "salary", ascending=True))
        salaries = [int(r[2]) for r in result["rows"]]
        assert salaries == sorted(salaries)

    def test_sort_descending(self, simple_csv):
        result = json.loads(da.sort_csv(str(simple_csv), "salary", ascending=False))
        salaries = [int(r[2]) for r in result["rows"]]
        assert salaries == sorted(salaries, reverse=True)

    def test_sort_text_column(self, simple_csv):
        result = json.loads(da.sort_csv(str(simple_csv), "name", ascending=True))
        names = [r[0] for r in result["rows"]]
        assert names == sorted(names)

    def test_sort_unknown_column_returns_error(self, simple_csv):
        result = json.loads(da.sort_csv(str(simple_csv), "nonexistent"))
        assert "error" in result

    def test_sort_preserves_all_rows(self, simple_csv):
        result = json.loads(da.sort_csv(str(simple_csv), "age"))
        assert result["shape"][0] == 5


# ---------------------------------------------------------------------------
# group_and_count
# ---------------------------------------------------------------------------

class TestGroupAndCount:
    def test_group_by_category(self, category_csv):
        result = json.loads(da.group_and_count(str(category_csv), "category"))
        counts = {item["value"]: item["count"] for item in result}
        assert counts["electronics"] == 2
        assert counts["gadgets"] == 3
        assert counts["tools"] == 1

    def test_group_sorted_by_count_desc(self, category_csv):
        result = json.loads(da.group_and_count(str(category_csv), "category"))
        # First item should have the highest count
        counts = [item["count"] for item in result]
        assert counts == sorted(counts, reverse=True)

    def test_group_unknown_column_returns_error(self, simple_csv):
        result = json.loads(da.group_and_count(str(simple_csv), "bad_col"))
        assert "error" in result

    def test_group_items_have_value_and_count(self, category_csv):
        result = json.loads(da.group_and_count(str(category_csv), "category"))
        for item in result:
            assert "value" in item
            assert "count" in item


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_empty_list_returns_error(self):
        result = json.loads(da.compute_stats([]))
        assert "error" in result

    def test_basic_stats(self):
        nums = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.compute_stats(nums))
        assert abs(result["mean"] - 3.0) < 0.001
        assert result["min"] == 1.0
        assert result["max"] == 5.0
        assert result["count"] == 5

    def test_median_computed(self):
        result = json.loads(da.compute_stats([1, 3, 5]))
        assert result["median"] == 3.0

    def test_std_computed(self):
        result = json.loads(da.compute_stats([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]))
        assert result["std"] > 0

    def test_percentiles_computed(self):
        nums = list(range(1, 101))
        result = json.loads(da.compute_stats(nums))
        assert "p25" in result
        assert "p75" in result
        assert "p95" in result
        assert result["p95"] >= result["p75"]

    def test_single_value(self):
        result = json.loads(da.compute_stats([42.0]))
        assert result["mean"] == 42.0
        assert result["min"] == 42.0
        assert result["max"] == 42.0


# ---------------------------------------------------------------------------
# detect_trend
# ---------------------------------------------------------------------------

class TestDetectTrend:
    def test_upward_trend(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.detect_trend(values))
        assert result["direction"] == "up"
        assert result["slope"] > 0

    def test_downward_trend(self):
        values = [10.0, 8.0, 6.0, 4.0, 2.0]
        result = json.loads(da.detect_trend(values))
        assert result["direction"] == "down"
        assert result["slope"] < 0

    def test_flat_trend(self):
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        result = json.loads(da.detect_trend(values))
        assert result["direction"] == "flat"

    def test_too_few_values_returns_error(self):
        result = json.loads(da.detect_trend([1.0]))
        assert "error" in result

    def test_r_squared_between_0_and_1(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.detect_trend(values))
        assert 0.0 <= result["r_squared"] <= 1.0

    def test_perfect_linear_high_r_squared(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = json.loads(da.detect_trend(values))
        assert result["r_squared"] > 0.99

    def test_n_field_matches_input_length(self):
        values = [1.0, 2.0, 3.0, 4.0]
        result = json.loads(da.detect_trend(values))
        assert result["n"] == 4


# ---------------------------------------------------------------------------
# moving_average
# ---------------------------------------------------------------------------

class TestMovingAverage:
    def test_window_1_returns_same(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.moving_average(values, window=1))
        assert result == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_window_3_first_two_are_none(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.moving_average(values, window=3))
        assert result[0] is None
        assert result[1] is None
        assert result[2] is not None

    def test_window_3_correct_value(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.moving_average(values, window=3))
        # MA at index 2 = (1+2+3)/3 = 2.0
        assert abs(result[2] - 2.0) < 0.001

    def test_invalid_window_returns_error(self):
        result = json.loads(da.moving_average([1.0, 2.0], window=0))
        assert "error" in result

    def test_output_length_matches_input(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = json.loads(da.moving_average(values, window=3))
        assert len(result) == len(values)


# ---------------------------------------------------------------------------
# find_outliers
# ---------------------------------------------------------------------------

class TestFindOutliers:
    def test_no_outliers_in_uniform_data(self):
        values = [10.0] * 20
        result = json.loads(da.find_outliers(values, method="iqr"))
        assert result["outlier_indices"] == []

    def test_obvious_outlier_detected_iqr(self):
        values = [1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0, 100.0]
        result = json.loads(da.find_outliers(values, method="iqr"))
        assert 9 in result["outlier_indices"]

    def test_obvious_outlier_detected_zscore(self):
        values = [1.0] * 19 + [100.0]
        result = json.loads(da.find_outliers(values, method="zscore"))
        assert 19 in result["outlier_indices"]

    def test_empty_values_returns_empty(self):
        result = json.loads(da.find_outliers([]))
        assert result["outlier_indices"] == []
        assert result["outlier_values"] == []

    def test_outlier_values_correct(self):
        values = [1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0, 100.0]
        result = json.loads(da.find_outliers(values, method="iqr"))
        for idx in result["outlier_indices"]:
            assert values[idx] in result["outlier_values"]

    def test_invalid_method_returns_error(self):
        result = json.loads(da.find_outliers([1.0, 2.0, 3.0], method="magic"))
        assert "error" in result

    def test_result_keys_present(self):
        result = json.loads(da.find_outliers([1.0, 2.0, 3.0]))
        assert "outlier_indices" in result
        assert "outlier_values" in result


# ---------------------------------------------------------------------------
# normalize_numbers
# ---------------------------------------------------------------------------

class TestNormalizeNumbers:
    def test_minmax_range_0_to_1(self):
        values = [0.0, 25.0, 50.0, 75.0, 100.0]
        result = json.loads(da.normalize_numbers(values, "minmax"))
        assert result[0] == 0.0
        assert result[-1] == 1.0
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_zscore_mean_near_zero(self):
        import statistics
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = json.loads(da.normalize_numbers(values, "zscore"))
        assert abs(sum(result) / len(result)) < 0.001

    def test_empty_returns_empty_list(self):
        result = json.loads(da.normalize_numbers([]))
        assert result == []

    def test_unknown_method_returns_error(self):
        result = json.loads(da.normalize_numbers([1.0, 2.0], "bad_method"))
        assert "error" in result

    def test_uniform_values_all_zero_minmax(self):
        values = [5.0] * 5
        result = json.loads(da.normalize_numbers(values, "minmax"))
        assert all(v == 0.0 for v in result)


# ---------------------------------------------------------------------------
# correlation
# ---------------------------------------------------------------------------

class TestCorrelation:
    def test_perfect_positive_correlation(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        result = da.correlation(xs, ys)
        assert abs(result - 1.0) < 0.001

    def test_perfect_negative_correlation(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [5.0, 4.0, 3.0, 2.0, 1.0]
        result = da.correlation(xs, ys)
        assert abs(result + 1.0) < 0.001

    def test_unequal_length_raises(self):
        with pytest.raises(ValueError):
            da.correlation([1.0, 2.0], [1.0])

    def test_single_pair_returns_zero(self):
        result = da.correlation([1.0], [1.0])
        assert result == 0.0

    def test_result_in_range(self):
        xs = [1.0, 3.0, 5.0, 2.0, 4.0]
        ys = [2.0, 5.0, 3.0, 4.0, 1.0]
        result = da.correlation(xs, ys)
        assert -1.0 <= result <= 1.0
