import pandas as pd

from src.priority import apply_operation_priorities


def test_priority_marks_high_amount_overdue_frequent_asin_as_p1():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "B001",
                "SKU": "SKU-HIGH",
                "Refund Amount": 150.0,
                "Days Since Refund": 75,
                "Risk Level": "Needs Review",
                "Return Reason Category": "产品质量问题",
            },
            {
                "Order ID": "111-2",
                "ASIN": "B001",
                "SKU": "SKU-HIGH",
                "Refund Amount": 80.0,
                "Days Since Refund": 40,
                "Risk Level": "Needs Review",
                "Return Reason Category": "产品质量问题",
            },
            {
                "Order ID": "111-3",
                "ASIN": "B001",
                "SKU": "SKU-HIGH",
                "Refund Amount": 60.0,
                "Days Since Refund": 20,
                "Risk Level": "Needs Review",
                "Return Reason Category": "产品质量问题",
            },
        ]
    )

    result = apply_operation_priorities(orders)
    row = result[result["Order ID"].eq("111-1")].iloc[0]

    assert row["Operation Priority"] == "P1"
    assert row["Priority Action"] == "立即处理"
    assert "退款金额较高" in row["Priority Reasons"]
    assert "ASIN重复出现" in row["Priority Reasons"]


def test_priority_marks_moderate_review_order_as_p2():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "B002",
                "SKU": "SKU-MID",
                "Refund Amount": 55.0,
                "Days Since Refund": 35,
                "Risk Level": "Needs Review",
            }
        ]
    )

    result = apply_operation_priorities(orders)

    assert result.loc[0, "Operation Priority"] == "P2"
    assert result.loc[0, "Priority Action"] == "今日处理"


def test_priority_marks_low_value_normal_order_as_p3():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "B003",
                "SKU": "SKU-LOW",
                "Refund Amount": 12.0,
                "Days Since Refund": 8,
                "Risk Level": "Low",
            }
        ]
    )

    result = apply_operation_priorities(orders)

    assert result.loc[0, "Operation Priority"] == "P3"
    assert result.loc[0, "Priority Action"] == "观察即可"
