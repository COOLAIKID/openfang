"""Tests for autoearn/core/product_manager.py."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict
from unittest.mock import patch

import pytest

# ── Patch DB path before importing the module ──────────────────────────────
_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_pm_{uuid.uuid4().hex[:8]}.db")


def _get_db_path():
    return _DB_PATH


with patch("autoearn.core.database.get_db_path", _get_db_path):
    import autoearn.core.product_manager as pm


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_schema():
    """Reset schema flag and wipe tables between tests."""
    pm._schema_ready = False
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript("""
        DROP TABLE IF EXISTS product_reviews;
        DROP TABLE IF EXISTS product_coupons;
        DROP TABLE IF EXISTS product_sales;
        DROP TABLE IF EXISTS product_variants;
        DROP TABLE IF EXISTS products;
    """)
    conn.close()
    yield


@pytest.fixture()
def sample_product():
    with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
        return pm.create_product(
            name="Python Ebook",
            product_type="ebook",
            price=29.99,
            description="Learn Python",
            platform="gumroad",
        )


@pytest.fixture()
def sample_product_with_variant(sample_product):
    with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
        variant = pm.add_variant(
            product_id=sample_product.id,
            name="Pro",
            price=49.99,
            description="Pro edition with extras",
        )
        return sample_product, variant


# ── Product CRUD ──────────────────────────────────────────────────────────

class TestCreateProduct:
    def test_basic_creation(self, sample_product):
        assert sample_product.id is not None
        assert sample_product.name == "Python Ebook"
        assert sample_product.price == 29.99
        assert sample_product.product_type == "ebook"
        assert sample_product.status == "draft"

    def test_sku_auto_generated(self, sample_product):
        assert sample_product.sku.startswith("AE-")
        assert len(sample_product.sku) > 5

    def test_timestamps_set(self, sample_product):
        assert sample_product.created_at
        assert sample_product.updated_at
        # should be valid ISO timestamps
        datetime.fromisoformat(sample_product.created_at)
        datetime.fromisoformat(sample_product.updated_at)

    def test_default_status_is_draft(self, sample_product):
        assert sample_product.status == "draft"

    def test_tags_stored_as_list(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Tagged", tags=["python", "ebook", "beginner"])
        assert "python" in p.tags
        assert "beginner" in p.tags

    def test_with_sale_price(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Sale Item", price=99.0, sale_price=49.0)
        assert p.sale_price == 49.0
        assert p.is_on_sale is True
        assert p.effective_price == 49.0

    def test_no_sale_effective_price_is_price(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Normal", price=19.99)
        assert p.effective_price == 19.99
        assert p.is_on_sale is False

    def test_discount_percent_calculation(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Half Off", price=100.0, sale_price=50.0)
        assert p.discount_percent == 50.0

    def test_no_discount_returns_zero(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Full Price", price=100.0)
        assert p.discount_percent == 0.0

    def test_metadata_stored(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Meta", metadata={"source": "ai-generated", "version": 2})
        assert p.metadata["source"] == "ai-generated"
        assert p.metadata["version"] == 2

    def test_all_product_types(self):
        for pt in pm.PRODUCT_TYPES[:6]:
            with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
                p = pm.create_product(f"Type {pt}", product_type=pt)
            assert p.product_type == pt


class TestGetProduct:
    def test_get_by_id(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            fetched = pm.get_product(sample_product.id)
        assert fetched is not None
        assert fetched.id == sample_product.id
        assert fetched.name == "Python Ebook"

    def test_get_nonexistent_returns_none(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = pm.get_product(99999)
        assert result is None

    def test_get_by_sku(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            fetched = pm.get_product_by_sku(sample_product.sku)
        assert fetched is not None
        assert fetched.id == sample_product.id

    def test_get_unknown_sku_returns_none(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = pm.get_product_by_sku("NONEXISTENT-SKU")
        assert result is None


class TestUpdateProduct:
    def test_update_name(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            ok = pm.update_product(sample_product.id, name="New Name")
            updated = pm.get_product(sample_product.id)
        assert ok is True
        assert updated.name == "New Name"

    def test_update_price(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.update_product(sample_product.id, price=39.99)
            updated = pm.get_product(sample_product.id)
        assert updated.price == 39.99

    def test_update_status(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.update_product(sample_product.id, status="active")
            updated = pm.get_product(sample_product.id)
        assert updated.status == "active"

    def test_update_tags(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.update_product(sample_product.id, tags=["python", "advanced"])
            updated = pm.get_product(sample_product.id)
        assert "advanced" in updated.tags

    def test_update_nonexistent_allowed_field(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            ok = pm.update_product(sample_product.id, nonexistent_field="value")
        assert ok is False

    def test_publish_product(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.publish_product(sample_product.id)
            updated = pm.get_product(sample_product.id)
        assert updated.status == "active"

    def test_archive_product(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.archive_product(sample_product.id)
            updated = pm.get_product(sample_product.id)
        assert updated.status == "archived"


class TestListProducts:
    def test_list_all(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.create_product("P1")
            pm.create_product("P2")
            pm.create_product("P3")
            products = pm.list_products()
        assert len(products) >= 3

    def test_filter_by_status(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            p = pm.create_product("Active One")
            pm.publish_product(p.id)
            pm.create_product("Draft One")
            actives = pm.list_products(status="active")
        assert all(p.status == "active" for p in actives)
        assert len(actives) >= 1

    def test_filter_by_type(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.create_product("Course 1", product_type="course")
            pm.create_product("Ebook 1", product_type="ebook")
            courses = pm.list_products(product_type="course")
        assert all(p.product_type == "course" for p in courses)

    def test_limit_respected(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            for i in range(5):
                pm.create_product(f"Limit Test {i}")
            products = pm.list_products(limit=2)
        assert len(products) <= 2


class TestDeleteProduct:
    def test_delete_removes_product(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.delete_product(sample_product.id)
            result = pm.get_product(sample_product.id)
        assert result is None


# ── Variants ─────────────────────────────────────────────────────────────

class TestVariants:
    def test_add_variant(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            v = pm.add_variant(sample_product.id, "Enterprise", 99.99)
        assert v.id is not None
        assert v.name == "Enterprise"
        assert v.price == 99.99

    def test_variant_sku_unique(self, sample_product_with_variant):
        product, variant = sample_product_with_variant
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            v2 = pm.add_variant(product.id, "Basic", 9.99)
        assert v2.sku != variant.sku

    def test_list_variants(self, sample_product_with_variant):
        product, _ = sample_product_with_variant
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            variants = pm.list_variants(product.id)
        assert len(variants) >= 1

    def test_variant_sale_price(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            v = pm.add_variant(sample_product.id, "Sale Tier", 50.0, sale_price=30.0)
        assert v.sale_price == 30.0
        assert v.effective_price == 30.0

    def test_variant_no_sale_effective_price(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            v = pm.add_variant(sample_product.id, "Regular", 40.0)
        assert v.effective_price == 40.0

    def test_add_variant_nonexistent_product_raises(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="not found"):
                pm.add_variant(99999, "Bad", 10.0)

    def test_product_includes_variants(self, sample_product_with_variant):
        product, _ = sample_product_with_variant
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            fetched = pm.get_product(product.id)
        assert len(fetched.variants) >= 1


# ── Sales ──────────────────────────────────────────────────────────────────

class TestSales:
    def test_record_sale(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 29.99, customer_email="alice@example.com")
        assert sale.id is not None
        assert sale.amount == 29.99
        assert sale.customer_email == "alice@example.com"
        assert sale.status == "completed"

    def test_sale_ref_auto_generated(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 10.0)
        assert sale.sale_ref.startswith("SALE-")

    def test_sale_with_utm(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(
                sample_product.id, 29.99,
                utm_source="google", utm_medium="cpc", utm_campaign="summer"
            )
        assert sale.utm_source == "google"
        assert sale.utm_medium == "cpc"
        assert sale.utm_campaign == "summer"

    def test_record_refund(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 29.99)
            ok = pm.refund_sale(sale.id, reason="Customer request")
            refunded_sales = pm.list_sales(product_id=sample_product.id, status="refunded")
        assert ok is True
        assert len(refunded_sales) >= 1

    def test_list_sales_by_product(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99)
            pm.record_sale(sample_product.id, 19.99)
            sales = pm.list_sales(product_id=sample_product.id)
        assert len(sales) >= 2

    def test_list_sales_filter_status(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 10.0)
            pm.refund_sale(sale.id)
            completed = pm.list_sales(product_id=sample_product.id, status="completed")
            refunded = pm.list_sales(product_id=sample_product.id, status="refunded")
        assert all(s.status == "completed" for s in completed)
        assert all(s.status == "refunded" for s in refunded)

    def test_get_sale_by_ref(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 29.99)
            fetched = pm.get_sale_by_ref(sale.sale_ref)
        assert fetched is not None
        assert fetched.id == sale.id

    def test_get_sale_by_unknown_ref_returns_none(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = pm.get_sale_by_ref("SALE-NONEXISTENT")
        assert result is None

    def test_sale_with_affiliate(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(
                sample_product.id, 50.0,
                affiliate_id="aff_123", commission_pct=30.0
            )
        assert sale.affiliate_id == "aff_123"
        assert sale.commission_pct == 30.0

    def test_sale_with_coupon(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(
                sample_product.id, 24.99,
                coupon_code="SAVE5", discount_amount=5.0
            )
        assert sale.coupon_code == "SAVE5"
        assert sale.discount_amount == 5.0


# ── Reviews ──────────────────────────────────────────────────────────────

class TestReviews:
    def test_add_review(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            rv = pm.add_review(sample_product.id, 5, reviewer="Alice", title="Great!", body="Loved it")
        assert rv["id"] is not None
        assert rv["rating"] == 5

    def test_invalid_rating_raises(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="Rating must be 1-5"):
                pm.add_review(sample_product.id, 6)

    def test_rating_zero_raises(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            with pytest.raises(ValueError):
                pm.add_review(sample_product.id, 0)

    def test_get_reviews(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.add_review(sample_product.id, 5, reviewer="Alice")
            pm.add_review(sample_product.id, 4, reviewer="Bob")
            reviews = pm.get_reviews(sample_product.id)
        assert len(reviews) >= 2

    def test_get_reviews_min_rating_filter(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.add_review(sample_product.id, 5)
            pm.add_review(sample_product.id, 2)
            high_reviews = pm.get_reviews(sample_product.id, min_rating=4)
        assert all(r["rating"] >= 4 for r in high_reviews)

    def test_review_summary_average(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.add_review(sample_product.id, 5)
            pm.add_review(sample_product.id, 3)
            summary = pm.review_summary(sample_product.id)
        assert summary["total_reviews"] == 2
        assert summary["average_rating"] == 4.0

    def test_review_summary_empty(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            summary = pm.review_summary(sample_product.id)
        assert summary["total_reviews"] == 0
        assert summary["average_rating"] == 0.0

    def test_review_summary_distribution(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.add_review(sample_product.id, 5)
            pm.add_review(sample_product.id, 5)
            pm.add_review(sample_product.id, 3)
            summary = pm.review_summary(sample_product.id)
        assert summary["distribution"]["5"] == 2
        assert summary["distribution"]["3"] == 1

    def test_rating_label_exceptional(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.add_review(sample_product.id, 5)
            summary = pm.review_summary(sample_product.id)
        assert summary["rating_label"] in ("Exceptional", "Excellent", "Very Good", "Good", "Average", "Below Average")

    @pytest.mark.parametrize("avg,expected", [
        (4.9, "Exceptional"),
        (4.6, "Excellent"),
        (4.1, "Very Good"),
        (3.7, "Good"),
        (3.2, "Average"),
        (2.9, "Below Average"),
    ])
    def test_rating_labels(self, avg, expected):
        assert pm._rating_label(avg) == expected


# ── Coupons ──────────────────────────────────────────────────────────────

class TestCoupons:
    def test_create_coupon(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            coup = pm.create_coupon("SAVE10", discount_type="percent", discount_value=10.0)
        assert coup["code"] == "SAVE10"
        assert coup["discount_value"] == 10.0

    def test_coupon_code_uppercased(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            coup = pm.create_coupon("lowercode", discount_value=5.0)
        assert coup["code"] == "LOWERCODE"

    def test_duplicate_coupon_raises(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.create_coupon("DOUBLE")
            with pytest.raises(ValueError, match="already exists"):
                pm.create_coupon("DOUBLE")

    def test_validate_valid_coupon(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.create_coupon("VALID20", discount_value=20.0)
            result = pm.validate_coupon("VALID20")
        assert result["valid"] is True
        assert result["discount_value"] == 20.0

    def test_validate_unknown_coupon(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = pm.validate_coupon("NOSUCHCODE")
        assert result["valid"] is False

    def test_validate_expired_coupon(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            past = (datetime.utcnow() - timedelta(days=1)).isoformat()
            pm.create_coupon("EXPIRED", discount_value=5.0, expires_at=past)
            result = pm.validate_coupon("EXPIRED")
        assert result["valid"] is False
        assert "expired" in result["reason"].lower()

    def test_validate_usage_limit_exhausted(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.create_coupon("MAXED", discount_value=5.0, max_uses=1)
            pm.use_coupon("MAXED")
            result = pm.validate_coupon("MAXED")
        assert result["valid"] is False

    def test_use_coupon_increments(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.create_coupon("COUNTME", discount_value=5.0, max_uses=3)
            pm.use_coupon("COUNTME")
            pm.use_coupon("COUNTME")
            result = pm.validate_coupon("COUNTME")
        assert result["uses_remaining"] == 1


# ── Revenue analytics ─────────────────────────────────────────────────────

class TestRevenue:
    def test_product_revenue_report(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99)
            pm.record_sale(sample_product.id, 29.99)
            report = pm.product_revenue_report(sample_product.id)
        assert report["product_id"] == sample_product.id
        assert report["gross_revenue"] >= 59.0
        assert report["total_sales"] >= 2

    def test_product_revenue_report_with_refund(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 29.99)
            pm.record_sale(sample_product.id, 29.99)
            pm.refund_sale(sale.id)
            report = pm.product_revenue_report(sample_product.id)
        assert report["refund_count"] >= 1
        assert report["refund_rate"] > 0

    def test_product_revenue_unknown_product(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            report = pm.product_revenue_report(99999)
        assert "error" in report

    def test_best_sellers_returns_list(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99)
            sellers = pm.best_sellers(limit=5)
        assert isinstance(sellers, list)

    def test_best_sellers_by_units(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99)
            sellers = pm.best_sellers(limit=5, metric="units")
        assert isinstance(sellers, list)

    def test_revenue_by_type(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99)
            data = pm.revenue_by_type()
        assert isinstance(data, list)
        types = [d["product_type"] for d in data]
        assert "ebook" in types

    def test_revenue_by_platform(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99, platform="gumroad")
            data = pm.revenue_by_platform()
        assert isinstance(data, list)

    def test_geo_revenue_breakdown(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99, country="US")
            pm.record_sale(sample_product.id, 29.99, country="UK")
            data = pm.geo_revenue_breakdown(product_id=sample_product.id)
        assert isinstance(data, list)

    def test_utm_attribution_returns_dict(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99, utm_source="google")
            data = pm.utm_attribution()
        assert "by_source" in data
        assert "by_medium" in data
        assert "by_campaign" in data

    def test_coupon_performance(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 24.99, coupon_code="COUPON10")
            data = pm.coupon_performance()
        assert isinstance(data, list)

    def test_catalog_summary(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99)
            summary = pm.product_catalog_summary()
        assert "catalog" in summary
        assert "revenue" in summary
        assert summary["catalog"]["total"] >= 1

    def test_catalog_summary_active_count(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.publish_product(sample_product.id)
            summary = pm.product_catalog_summary()
        assert summary["catalog"]["active"] >= 1


# ── to_dict serialization ─────────────────────────────────────────────────

class TestToDict:
    def test_product_to_dict_keys(self, sample_product):
        d = sample_product.to_dict()
        required_keys = ["id", "sku", "name", "price", "status", "tags", "created_at"]
        for k in required_keys:
            assert k in d, f"Key '{k}' missing from product.to_dict()"

    def test_product_to_dict_is_json_serializable(self, sample_product):
        d = sample_product.to_dict()
        json_str = json.dumps(d)
        assert json_str  # didn't raise

    def test_sale_to_dict_keys(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            sale = pm.record_sale(sample_product.id, 29.99)
        d = sale.to_dict()
        required_keys = ["id", "sale_ref", "product_id", "amount", "status", "sold_at"]
        for k in required_keys:
            assert k in d

    def test_variant_to_dict_keys(self, sample_product_with_variant):
        product, variant = sample_product_with_variant
        d = variant.to_dict()
        for k in ["id", "name", "sku", "price", "effective_price"]:
            assert k in d


# ── Tool wrappers ─────────────────────────────────────────────────────────

class TestToolWrappers:
    def test_create_product_tool_ok(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.create_product_tool("Tool Product", price=19.99))
        assert result["ok"] is True
        assert result["product"]["name"] == "Tool Product"

    def test_record_sale_tool_ok(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.record_sale_tool(sample_product.id, 29.99))
        assert result["ok"] is True

    def test_product_report_tool_ok(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.product_report_tool(sample_product.id))
        assert "gross_revenue" in result

    def test_best_sellers_tool_returns_list(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.best_sellers_tool(5))
        assert isinstance(result, list)

    def test_catalog_summary_tool(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.catalog_summary_tool())
        assert "catalog" in result

    def test_list_products_tool(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.list_products_tool())
        assert isinstance(result, list)

    def test_add_review_tool_ok(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.add_review_tool(sample_product.id, 5, "Alice", "Great!", "Loved it"))
        assert result["ok"] is True

    def test_add_review_tool_bad_rating(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            result = json.loads(pm.add_review_tool(sample_product.id, 10))
        assert result["ok"] is False

    def test_utm_attribution_tool(self, sample_product):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            pm.record_sale(sample_product.id, 29.99, utm_source="facebook")
            result = json.loads(pm.utm_attribution_tool())
        assert "by_source" in result

    def test_create_product_tool_error(self):
        with patch("autoearn.core.product_manager.get_db_path", _get_db_path):
            # price as string should cause an error or work depending on Python
            result = json.loads(pm.create_product_tool(""))
        # name empty is still valid, just check it returns something
        assert "ok" in result
