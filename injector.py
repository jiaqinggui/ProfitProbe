import json
import shutil
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_DB = PROJECT_ROOT / "olist_base.db"
OUTPUT_DIR = PROJECT_ROOT / "scenarios"

TARGET_WINDOWS = [
    ("2017-W32", "2017-08-07", "2017-08-14"),
    ("2017-W33", "2017-08-14", "2017-08-21"),
    ("2017-W34", "2017-08-21", "2017-08-28"),
    ("2017-W35", "2017-08-28", "2017-09-04"),
    ("2017-W40", "2017-10-02", "2017-10-09"),
    ("2017-W41", "2017-10-09", "2017-10-16"),
    ("2017-W42", "2017-10-16", "2017-10-23"),
    ("2018-W09", "2018-02-26", "2018-03-05"),
    ("2018-W10", "2018-03-05", "2018-03-12"),
    ("2018-W15", "2018-04-09", "2018-04-16"),
]
CATEGORIES = [
    "bed_bath_table",
    "health_beauty",
    "sports_leisure",
    "furniture_decor",
    "computers_accessories",
    "toys",
    "auto",
    "housewares",
    "baby",
    "stationery",
]


def init_output_dir() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)


def get_db_copy(scenario_id: int) -> Path:
    db_path = OUTPUT_DIR / f"scenario_{scenario_id:02d}.db"
    shutil.copyfile(BASE_DB, db_path)
    return db_path


def _week_filter(start: str, end: str) -> str:
    return "order_purchase_timestamp >= ? AND order_purchase_timestamp < ?"


def _category_filter(category: str) -> str:
    return "product_id IN (SELECT product_id FROM products WHERE product_category_name = ?)"


def _add_label(
    labels: list[dict],
    scenario_id: int,
    question: str,
    target_week: str,
    cause: str,
    entity: str,
    expected_answer: str,
    dollar_impact: float,
    has_distractor: bool,
) -> None:
    labels.append(
        {
            "scenario_id": scenario_id,
            "question": question,
            "target_week": target_week,
            "planted_cause": cause,
            "entity": entity,
            "expected_answer": expected_answer,
            "dollar_impact": round(max(dollar_impact, 0.0), 2),
            "has_distractor": has_distractor,
        }
    )


def _select_value(cursor: sqlite3.Cursor, query: str, parameters: tuple) -> str:
    result = cursor.execute(query, parameters).fetchone()
    if not result:
        raise RuntimeError("Unable to select an entity for a scenario.")
    return result[0]


def generate_scenarios() -> None:
    if not BASE_DB.exists():
        raise FileNotFoundError(f"Base database not found: {BASE_DB}")

    init_output_dir()
    labels: list[dict] = []
    scenario_id = 1

    # 1. Wrong discount code: ten localized category/week anomalies.
    for iteration, (target_week, start, end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        category = CATEGORIES[iteration]
        has_distractor = iteration < 2
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            impact = cursor.execute(
                f"""
                SELECT COALESCE(SUM(price * 0.90), 0)
                FROM order_items
                WHERE {_category_filter(category)}
                  AND order_id IN (
                    SELECT order_id FROM orders
                    WHERE {_week_filter(start, end)}
                  )
                """,
                (category, start, end),
            ).fetchone()[0]
            cursor.execute(
                f"""
                UPDATE order_items
                SET discount_code = 'PROMO_BUG', discount_amount = price * 0.90
                WHERE {_category_filter(category)}
                  AND order_id IN (
                    SELECT order_id FROM orders
                    WHERE {_week_filter(start, end)}
                  )
                """,
                (category, start, end),
            )
            if has_distractor:
                cursor.execute(
                    f"""
                    UPDATE order_items
                    SET freight_value = freight_value * 1.20
                    WHERE {_category_filter(CATEGORIES[(iteration + 1) % len(CATEGORIES)])}
                      AND order_id IN (
                        SELECT order_id FROM orders
                        WHERE {_week_filter(start, end)}
                      )
                    """,
                    (CATEGORIES[(iteration + 1) % len(CATEGORIES)], start, end),
                )
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did profit fall sharply in {target_week}?",
            target_week,
            "Wrong discount code",
            category,
            f"PROMO_BUG caused excessive discounts for category '{category}'.",
            impact,
            has_distractor,
        )
        scenario_id += 1

    # 2. Shipping delay: ten seller/week anomalies.
    for iteration, (target_week, start, end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            seller_id = _select_value(
                cursor,
                "SELECT seller_id FROM sellers ORDER BY seller_id LIMIT 1 OFFSET ?",
                (iteration * 10,),
            )
            impact = cursor.execute(
                f"""
                SELECT COALESCE(SUM(price), 0) FROM order_items
                WHERE seller_id = ?
                  AND order_id IN (
                    SELECT order_id FROM orders WHERE {_week_filter(start, end)}
                  )
                """,
                (seller_id, start, end),
            ).fetchone()[0]
            cursor.execute(
                f"""
                UPDATE orders
                SET order_delivered_customer_date =
                    datetime(order_delivered_customer_date, '+20 days')
                WHERE {_week_filter(start, end)}
                  AND order_id IN (
                    SELECT order_id FROM order_items WHERE seller_id = ?
                  )
                """,
                (start, end, seller_id),
            )
            if iteration < 2:
                cursor.execute(
                    """
                    UPDATE order_items
                    SET discount_amount = 1.00
                    WHERE rowid IN (SELECT rowid FROM order_items ORDER BY rowid LIMIT 20)
                    """
                )
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did delivery performance and margin deteriorate in {target_week}?",
            target_week,
            "Shipping delay",
            seller_id,
            f"Seller '{seller_id}' caused a major delivery delay.",
            impact,
            iteration < 2,
        )
        scenario_id += 1

    # 3. Returns/cancellation spike: cancel 80% of category orders in a window.
    for iteration, (target_week, start, end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        category = CATEGORIES[iteration]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            impact = cursor.execute(
                f"""
                SELECT COALESCE(SUM(oi.price), 0) FROM order_items oi
                WHERE {_category_filter(category)}
                  AND oi.order_id IN (
                    SELECT order_id FROM orders WHERE {_week_filter(start, end)}
                  )
                """,
                (category, start, end),
            ).fetchone()[0]
            cursor.execute(
                f"""
                UPDATE orders SET order_status = 'canceled'
                WHERE rowid IN (
                    SELECT o.rowid FROM orders o
                    WHERE {_week_filter(start, end)}
                      AND o.order_id IN (
                        SELECT oi.order_id FROM order_items oi
                        WHERE {_category_filter(category)}
                      )
                    ORDER BY o.order_id
                    LIMIT (
                        SELECT CAST(COUNT(*) * 0.80 AS INTEGER) FROM orders o2
                        WHERE {_week_filter(start, end)}
                          AND o2.order_id IN (
                            SELECT oi2.order_id FROM order_items oi2
                            WHERE {_category_filter(category)}
                          )
                    )
                )
                """,
                (start, end, category, start, end, category),
            )
            if iteration < 2:
                cursor.execute("UPDATE products SET unit_cost = unit_cost * 1.02")
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did revenue and completed orders fall in {target_week}?",
            target_week,
            "Returns spike",
            category,
            f"An 80% cancellation spike affected category '{category}'.",
            impact,
            iteration < 2,
        )
        scenario_id += 1

    # 4. Price error: sell a category at 10% of its normal price.
    for iteration, (target_week, start, end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        category = CATEGORIES[iteration]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            impact = cursor.execute(
                f"""
                SELECT COALESCE(SUM(price * 0.90), 0) FROM order_items
                WHERE {_category_filter(category)}
                  AND order_id IN (
                    SELECT order_id FROM orders WHERE {_week_filter(start, end)}
                  )
                """,
                (category, start, end),
            ).fetchone()[0]
            cursor.execute(
                f"""
                UPDATE order_items SET price = price * 0.10
                WHERE {_category_filter(category)}
                  AND order_id IN (
                    SELECT order_id FROM orders WHERE {_week_filter(start, end)}
                  )
                """,
                (category, start, end),
            )
            if iteration < 2:
                cursor.execute(
                    """
                    UPDATE orders
                    SET order_delivered_customer_date =
                        datetime(order_delivered_customer_date, '+2 days')
                    """
                )
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did profit collapse in {target_week}?",
            target_week,
            "Price error",
            category,
            f"Category '{category}' was sold at 10% of its normal price.",
            impact,
            iteration < 2,
        )
        scenario_id += 1

    # 5. Cost increase: multiply category unit costs across the catalog.
    for iteration, (target_week, _start, _end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        category = CATEGORIES[iteration]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            impact = cursor.execute(
                """
                SELECT COALESCE(SUM(p.unit_cost * 1.5), 0)
                FROM order_items oi JOIN products p ON p.product_id = oi.product_id
                WHERE p.product_category_name = ?
                """,
                (category,),
            ).fetchone()[0]
            cursor.execute(
                "UPDATE products SET unit_cost = unit_cost * 2.5 "
                "WHERE product_category_name = ?",
                (category,),
            )
            if iteration < 2:
                cursor.execute(
                    """
                    UPDATE order_reviews SET review_score = 1
                    WHERE rowid % 20 = 0
                    """
                )
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did the margin deteriorate for {category} around {target_week}?",
            target_week,
            "Cost increase",
            category,
            f"Unit costs for category '{category}' increased 2.5x.",
            impact,
            iteration < 2,
        )
        scenario_id += 1

    # 6. Lost channel/stockout: remove order items paid through a channel.
    payment_types = [
        "boleto",
        "voucher",
        "boleto",
        "voucher",
        "boleto",
        "voucher",
        "boleto",
        "voucher",
        "boleto",
        "voucher",
    ]
    for iteration, (target_week, start, end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        payment_type = payment_types[iteration]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            impact = cursor.execute(
                f"""
                SELECT COALESCE(SUM(oi.price), 0) FROM order_items oi
                WHERE oi.order_id IN (
                    SELECT DISTINCT op.order_id FROM order_payments op
                    WHERE op.payment_type = ?
                      AND op.order_id IN (
                        SELECT order_id FROM orders WHERE {_week_filter(start, end)}
                      )
                )
                """,
                (payment_type, start, end),
            ).fetchone()[0]
            cursor.execute(
                f"""
                DELETE FROM order_items
                WHERE order_id IN (
                    SELECT DISTINCT op.order_id FROM order_payments op
                    WHERE op.payment_type = ?
                      AND op.order_id IN (
                        SELECT order_id FROM orders WHERE {_week_filter(start, end)}
                      )
                )
                """,
                (payment_type, start, end),
            )
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did sales volume fall in {target_week}?",
            target_week,
            "Lost channel",
            payment_type,
            f"The {payment_type} payment channel went down, causing lost order volume.",
            impact,
            False,
        )
        scenario_id += 1

    # 7. Bad reviews: seller reputation crisis and future-order loss.
    for iteration, (target_week, _start, _end) in enumerate(TARGET_WINDOWS):
        db_path = get_db_copy(scenario_id)
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            seller_id = _select_value(
                cursor,
                "SELECT seller_id FROM sellers ORDER BY seller_id LIMIT 1 OFFSET ?",
                (100 + iteration * 10,),
            )
            impact = cursor.execute(
                "SELECT COALESCE(SUM(price), 0) FROM order_items WHERE seller_id = ?",
                (seller_id,),
            ).fetchone()[0]
            cursor.execute(
                """
                UPDATE order_reviews SET review_score = 1
                WHERE order_id IN (
                    SELECT DISTINCT order_id FROM order_items WHERE seller_id = ?
                )
                """,
                (seller_id,),
            )
            cursor.execute(
                """
                CREATE TEMP TABLE bad_review_orders AS
                SELECT DISTINCT oi.order_id
                FROM order_items oi
                JOIN orders o ON o.order_id = oi.order_id
                WHERE oi.seller_id = ?
                ORDER BY o.order_purchase_timestamp DESC
                LIMIT (
                    SELECT CAST(COUNT(DISTINCT oi2.order_id) * 0.70 AS INTEGER)
                    FROM order_items oi2
                    WHERE oi2.seller_id = ?
                )
                """,
                (seller_id, seller_id),
            )
            cursor.execute(
                """
                DELETE FROM order_items
                WHERE order_id IN (SELECT order_id FROM bad_review_orders)
                """,
            )
            cursor.execute(
                """
                DELETE FROM orders
                WHERE order_id IN (SELECT order_id FROM bad_review_orders)
                """
            )
            cursor.execute("DROP TABLE bad_review_orders")
            conn.commit()
        _add_label(
            labels,
            scenario_id,
            f"Why did seller reputation and future sales deteriorate around {target_week}?",
            target_week,
            "Bad reviews",
            seller_id,
            f"Seller '{seller_id}' received one-star reviews and lost future orders.",
            impact,
            False,
        )
        scenario_id += 1

    # 8. Controls: clean copies with no database modification.
    for target_week, _start, _end in TARGET_WINDOWS:
        get_db_copy(scenario_id)
        _add_label(
            labels,
            scenario_id,
            f"Why did performance change slightly in {target_week}?",
            target_week,
            "No single cause (normal week-to-week noise)",
            "None",
            "No single cause; normal week-to-week noise.",
            0.0,
            False,
        )
        scenario_id += 1

    if len(labels) != 80:
        raise RuntimeError(f"Expected 80 scenario labels, generated {len(labels)}.")
    if sum(label["has_distractor"] for label in labels) != 10:
        raise RuntimeError("Expected exactly 10 scenarios with distractors.")

    with (OUTPUT_DIR / "labels.json").open("w", encoding="utf-8") as labels_file:
        json.dump(labels, labels_file, indent=2)
    print(f"Generated {len(labels)} scenarios in {OUTPUT_DIR}.")


if __name__ == "__main__":
    generate_scenarios()
