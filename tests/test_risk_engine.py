from datetime import date

import pandas as pd

from src.analysis import analyze_refunds_returns
from src.matcher import detect_columns
from src.risk import (
    apply_risk_scoring,
    apply_support_confirmation_overrides,
    build_buyer_abuse_summary,
    build_top_risk_orders,
)


def test_risk_scoring_marks_missing_evidence_as_manual_review_not_high_risk():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "Refund Date": date(2026, 3, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 67,
                "Refund Amount": 129.99,
                "Amazon Warehouse Received": "否",
                "Ledger Event Type": "",
                "Ledger Disposition": "",
                "Buyer Name": "Alice",
                "Buyer Email": "alice@example.com",
            }
        ]
    )

    scored = apply_risk_scoring(orders, overdue_days=60, high_amount_threshold=100)
    row = scored.iloc[0]

    assert row["Risk Level"] == "Needs Review"
    assert row["Risk Diagnosis"] == "需要人工确认"
    assert row["Risk Score"] >= 70
    assert "该订单已退款 67 天" in row["Risk Reasons"]
    assert "退款金额为 $129.99" in row["Risk Reasons"]
    assert "退款超过60天 (+25)" in row["Risk Factors"]
    assert row["Evidence Confidence"] == "Low Confidence"
    assert row["Risk Level With Confidence"] == "需要人工确认（低可信）"
    assert "缺少退货入仓事件" in row["Diagnostic Confidence Reason"]
    assert "没有赔偿报表" in row["Diagnostic Confidence Reason"]
    assert "不能直接判定商品未退回" in row["Suggested Action"]
    assert row["Risk Explanation"] == row["Risk Reasons"]
    assert row["Conclusion Type"] == "高概率推断"
    assert "不能表述为商品未退回" in row["Conclusion Boundary"]


def test_support_confirmation_can_mark_order_as_true_high_risk():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-confirmed-not-returned",
                "Refund Date": date(2026, 3, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 67,
                "Refund Amount": 129.99,
                "Amazon Warehouse Received": "否",
                "Matched Ledger": "否",
            }
        ]
    )

    scored = apply_risk_scoring(orders, overdue_days=60, high_amount_threshold=100)
    overridden = apply_support_confirmation_overrides(
        scored,
        {"111-confirmed-not-returned": "Amazon确认未退回"},
    )
    row = overridden.iloc[0]

    assert row["Risk Level"] == "High"
    assert row["Risk Diagnosis"] == "真正高风险"
    assert row["Risk Level With Confidence"] == "真正高风险（高可信）"
    assert "Amazon Support 已确认商品未退回" in row["Risk Explanation"]
    assert row["Conclusion Type"] == "已确认事实"
    assert "Amazon Support 人工确认" in row["Conclusion Boundary"]


def test_risk_scoring_lowers_confidence_when_only_ledger_evidence_is_available():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-ledger-only",
                "Refund Date": date(2026, 4, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 30,
                "Refund Amount": 35.0,
                "Amazon Warehouse Received": "是",
                "Matched Returns": "否",
                "Matched Ledger": "是",
                "Ledger Event Type": "CustomerReturns",
                "Ledger Disposition": "SELLABLE",
                "Ledger Match Method": "order_id exact match",
                "SKU": "SKU-1",
                "Ledger FNSKU": "FNSKU-1",
                "Reimbursement Report Available": "是",
            }
        ]
    )

    scored = apply_risk_scoring(orders)
    row = scored.iloc[0]

    assert row["Risk Level"] == "Low"
    assert row["Evidence Confidence"] == "Medium Confidence"
    assert row["Risk Level With Confidence"] == "正常/低风险（中可信）"
    assert "仅依赖库存流水推断" in row["Diagnostic Confidence Reason"]
    assert row["Conclusion Type"] == "已确认事实"


def test_risk_scoring_marks_fuzzy_date_window_match_as_low_confidence():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-fuzzy",
                "Refund Date": date(2026, 4, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 30,
                "Refund Amount": 35.0,
                "Amazon Warehouse Received": "是",
                "Matched Returns": "否",
                "Matched Ledger": "是",
                "Ledger Event Type": "CustomerReturns",
                "Ledger Disposition": "SELLABLE",
                "Ledger Match Method": "sku + date window match",
                "SKU": "SKU-1",
                "Ledger FNSKU": "FNSKU-1",
                "Reimbursement Report Available": "是",
            }
        ]
    )

    scored = apply_risk_scoring(orders)
    row = scored.iloc[0]

    assert row["Evidence Confidence"] == "Low Confidence"
    assert row["Risk Level With Confidence"] == "正常/低风险（低可信）"
    assert "仅通过时间窗口模糊匹配" in row["Diagnostic Confidence Reason"]


def test_risk_scoring_marks_return_created_without_warehouse_receipt_as_medium():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-2",
                "Refund Date": date(2026, 4, 1),
                "Return Date": date(2026, 4, 8),
                "Days Since Refund": 30,
                "Refund Amount": 35.0,
                "Amazon Warehouse Received": "否",
                "Ledger Event Type": "",
                "Ledger Disposition": "",
                "SKU": "SKU-2",
                "Reimbursement Report Available": "是",
            }
        ]
    )

    scored = apply_risk_scoring(orders)
    row = scored.iloc[0]

    assert row["Risk Level"] == "Needs Review"
    assert row["Risk Diagnosis"] == "需要人工确认"
    assert 30 <= row["Risk Score"] < 75
    assert "退货报表记录" in row["Risk Reasons"]
    assert "不能直接判定商品未退回" in row["Risk Reasons"]
    assert row["Evidence Confidence"] == "Medium Confidence"


def test_risk_scoring_marks_sellable_warehouse_receipt_as_low():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-3",
                "Refund Date": date(2026, 4, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 30,
                "Refund Amount": 35.0,
                "Amazon Warehouse Received": "是",
                "Ledger Event Type": "CustomerReturns",
                "Ledger Disposition": "SELLABLE",
                "SKU": "SKU-3",
                "Reimbursement Report Available": "是",
            }
        ]
    )

    scored = apply_risk_scoring(orders)
    row = scored.iloc[0]

    assert row["Risk Level"] == "Low"
    assert row["Risk Score"] <= 25
    assert "FBA 入仓记录" in row["Risk Reasons"]
    assert row["Evidence Confidence"] == "Medium Confidence"


def test_risk_scoring_does_not_mark_low_amount_ledger_miss_as_high_risk():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-low",
                "Refund Date": date(2026, 3, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 67,
                "Refund Amount": 49.99,
                "Amazon Warehouse Received": "否",
                "Matched Ledger": "否",
                "Ledger Event Type": "",
                "Ledger Disposition": "",
            }
        ]
    )

    scored = apply_risk_scoring(orders, overdue_days=60, high_amount_threshold=100)
    row = scored.iloc[0]

    assert row["Risk Level"] == "Needs Review"
    assert row["Risk Diagnosis"] == "需要人工确认"
    assert row["Evidence Confidence"] == "Low Confidence"
    assert "不能单独证明商品未退回" in row["Risk Reasons"]


def test_support_confirmation_overrides_high_risk_to_low():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-support",
                "Refund Date": date(2026, 3, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 67,
                "Refund Amount": 129.99,
                "Amazon Warehouse Received": "否",
                "Matched Ledger": "否",
            }
        ]
    )

    scored = apply_risk_scoring(orders, overdue_days=60, high_amount_threshold=100)
    overridden = apply_support_confirmation_overrides(
        scored,
        {"111-support": "Amazon确认可售"},
    )
    row = overridden.iloc[0]

    assert row["Risk Level"] == "Low"
    assert row["Risk Diagnosis"] == "正常/低风险"
    assert row["Risk Score"] == 5
    assert row["Evidence Confidence"] == "High Confidence"
    assert "不应作为退款未退货高风险订单处理" in row["Risk Explanation"]


def test_risk_scoring_flags_repeated_high_risk_buyer_as_potential_abuse():
    orders = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "Refund Date": date(2026, 3, 1),
                "Return Date": pd.NaT,
                "Days Since Refund": 80,
                "Refund Amount": 120.0,
                "Amazon Warehouse Received": "否",
                "Buyer Email": "repeat@example.com",
            },
            {
                "Order ID": "111-2",
                "Refund Date": date(2026, 3, 2),
                "Return Date": pd.NaT,
                "Days Since Refund": 79,
                "Refund Amount": 130.0,
                "Amazon Warehouse Received": "否",
                "Buyer Email": "repeat@example.com",
            },
        ]
    )

    scored = apply_risk_scoring(orders, overdue_days=60, high_amount_threshold=100)

    assert scored["Potential Return Abuse"].tolist() == ["是", "是"]
    assert scored["Buyer High Risk Count"].tolist() == [2, 2]


def test_payments_buyer_fields_flow_into_analysis_result():
    returns = pd.DataFrame(columns=["amazon-order-id"])
    payments = pd.DataFrame(
        [
            {
                "order-id": "111-4",
                "posted-date": "2026-03-01",
                "transaction-type": "Refund",
                "total": "-55.00",
                "buyer-name": "Buyer One",
                "buyer-email": "buyer@example.com",
            }
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
    )

    assert result.loc[0, "Buyer Name"] == "Buyer One"
    assert result.loc[0, "Buyer Email"] == "buyer@example.com"
    assert "Risk Score" in result.columns


def test_detect_columns_supports_buyer_aliases_in_payments_report():
    payments = pd.DataFrame(columns=["order-id", "posted-date", "total", "Buyer Name", "buyer email"])

    columns = detect_columns(payments, "payments")

    assert columns.buyer_name == "Buyer Name"
    assert columns.buyer_email == "buyer email"


def test_build_top_risk_orders_supports_sorting_by_buyer_and_refund_amount():
    orders = pd.DataFrame(
        [
            {"Order ID": "1", "Risk Score": 80, "Buyer Email": "b@example.com", "Refund Amount": 50, "ASIN": "B002", "SKU": "S2"},
            {"Order ID": "2", "Risk Score": 90, "Buyer Email": "a@example.com", "Refund Amount": 150, "ASIN": "B001", "SKU": "S1"},
        ]
    )

    by_buyer = build_top_risk_orders(orders, sort_by="Buyer")
    by_amount = build_top_risk_orders(orders, sort_by="Refund Amount")

    assert by_buyer["Order ID"].tolist() == ["2", "1"]
    assert by_amount["Order ID"].tolist() == ["2", "1"]


def test_build_buyer_abuse_summary_returns_repeated_abuse_buyers():
    scored = pd.DataFrame(
        [
            {"Buyer Key": "repeat@example.com", "Buyer Email": "repeat@example.com", "Risk Level": "High", "Potential Return Abuse": "是", "Refund Amount": 120.0, "Order ID": "1"},
            {"Buyer Key": "repeat@example.com", "Buyer Email": "repeat@example.com", "Risk Level": "High", "Potential Return Abuse": "是", "Refund Amount": 130.0, "Order ID": "2"},
            {"Buyer Key": "normal@example.com", "Buyer Email": "normal@example.com", "Risk Level": "Low", "Potential Return Abuse": "否", "Refund Amount": 20.0, "Order ID": "3"},
        ]
    )

    summary = build_buyer_abuse_summary(scored)

    assert summary.to_dict("records") == [
        {
            "Buyer Key": "repeat@example.com",
            "Buyer Email": "repeat@example.com",
            "Buyer Name": "",
            "High Risk Orders": 2,
            "Total Refund Amount": 250.0,
            "Potential Return Abuse": "是",
        }
    ]
