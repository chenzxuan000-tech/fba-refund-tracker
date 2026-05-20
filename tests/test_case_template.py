import pandas as pd

from src.case_template import build_amazon_case_text


def test_case_template_includes_order_amount_and_timing():
    row = pd.Series(
        {
            "Order ID": "701-1234567-1234567",
            "Refund Amount": 129.99,
            "Refund Date": "2026-03-01",
            "Days Since Refund": 67,
            "Risk Diagnosis": "需要人工确认",
            "Risk Level With Confidence": "需要人工确认（低可信）",
        }
    )

    text = build_amazon_case_text(row)

    assert "请协助核查订单" in text
    assert "701-1234567-1234567" in text
    assert "$129.99" in text
    assert "2026-03-01" in text
    assert "67 天" in text
    assert "您好" in text


def test_case_template_mentions_buyer_damaged_when_unsellable():
    row = pd.Series(
        {
            "Order ID": "701-1",
            "Refund Amount": 75,
            "Days Since Refund": 12,
            "Ledger Disposition": "CUSTOMER_DAMAGED",
            "Return Reason": "DAMAGED_BY_BUYER",
        }
    )

    text = build_amazon_case_text(row)

    assert "买家损坏" in text
    assert "不可售" in text


def test_case_template_mentions_inventory_shortage_when_no_warehouse_evidence():
    row = pd.Series(
        {
            "Order ID": "701-2",
            "Refund Amount": 55,
            "Days Since Refund": 80,
            "Matched Returns": "否",
            "Matched Ledger": "否",
            "Amazon Warehouse Received": "否",
        }
    )

    text = build_amazon_case_text(row)

    assert "暂未匹配到明确退货入仓证据" in text
    assert "人工确认" in text
