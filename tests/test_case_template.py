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

    assert "Dear Amazon Seller Support" in text
    assert "701-1234567-1234567" in text
    assert "$129.99" in text
    assert "2026-03-01" in text
    assert "67 days" in text
    assert "Please verify" in text


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

    assert "buyer-damaged" in text.lower()
    assert "condition and reimbursement eligibility" in text


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

    assert "no confirmed FBA receiving or inventory movement record" in text
    assert "inventory reconciliation" in text.lower()
