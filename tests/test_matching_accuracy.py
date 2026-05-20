from datetime import date

import pandas as pd

from src.analysis import analyze_refunds_returns
from src.analyzer import build_match_diagnostics, build_order_audit_table, build_order_lifecycle
from src.matcher import detect_columns, normalize_order_id


def test_payments_chinese_columns_are_detected_without_matching_other_column():
    payments = pd.DataFrame(
        columns=[
            "日期/时间",
            "交易类型",
            "交易说明",
            "订单编号",
            "商城",
            "SKU",
            "商品详情",
            "商品销售额",
            "其他",
        ]
    )

    columns = detect_columns(payments, "payments")

    assert columns.refund_date == "日期/时间"
    assert columns.transaction_type == "交易类型"
    assert columns.description == "交易说明"
    assert columns.order_id == "订单编号"
    assert columns.marketplace == "商城"
    assert columns.sku == "SKU"
    assert columns.product_name == "商品详情"
    assert columns.refund_amount == "商品销售额"


def test_normalize_order_id_trims_hidden_chars_and_extracts_embedded_order_id():
    assert normalize_order_id(" \u200b702-1234567-7654321 ") == "702-1234567-7654321"
    assert normalize_order_id("Refund for order 702-1234567-7654321 / return") == "702-1234567-7654321"


def test_returns_payments_match_after_order_id_cleaning_and_amount_abs_conversion():
    returns = pd.DataFrame(
        [
            {
                "order-id": "702-1234567-7654321",
                "sku": "SKU-1",
                "asin": "B001",
                "return-date": "2026-03-20",
                "detailed-disposition": "SELLABLE",
            }
        ]
    )
    payments = pd.DataFrame(
        [
            {
                "订单编号": " \u200b702-1234567-7654321 ",
                "日期/时间": "03/18/2026",
                "交易类型": "退款",
                "商品销售额": "$-1,234.56",
                "商品详情": "Widget",
                "SKU": "SKU-1",
            }
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
    )

    row = result.iloc[0]
    assert row["Order ID"] == "702-1234567-7654321"
    assert row["Return Date"].isoformat() == "2026-03-20"
    assert row["Refund Amount"] == 1234.56
    assert row["Matched Returns"] == "是"


def test_ledger_matches_by_sku_and_refund_date_when_reference_id_is_not_order_id():
    returns = pd.DataFrame(columns=["order-id"])
    payments = pd.DataFrame(
        [
            {
                "order-id": "702-1234567-7654321",
                "posted-date": "2026-03-18",
                "transaction-type": "Refund",
                "total": "-50",
                "sku": "SKU-1",
            }
        ]
    )
    ledger = pd.DataFrame(
        [
            {
                "Date": "2026-03-22",
                "Disposition": "SELLABLE",
                "Event Type": "CustomerReturns",
                "Reference ID": "not-an-order-id",
                "FNSKU": "X001",
                "MSKU": "SKU-1",
                "Quantity": "1",
                "Fulfillment Center": "ONT8",
                "Reason": "Customer return",
            }
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
        ledger_df=ledger,
    )

    assert result.loc[0, "Matched Ledger"] == "是"
    assert result.loc[0, "Ledger Match Method"] == "sku + date window match"
    assert result.loc[0, "Amazon Warehouse Received"] == "是"


def test_match_diagnostics_and_order_audit_table_explain_match_status():
    analysis = pd.DataFrame(
        [
            {
                "Order ID": "702-1",
                "SKU": "SKU-1",
                "ASIN": "B001",
                "Product Name": "Widget",
                "Refund Date": date(2026, 3, 18),
                "Refund Amount": 50.0,
                "Return Date": pd.NaT,
                "Ledger Date": pd.NaT,
                "Return Reason": "",
                "Disposition / Sellable Status": "",
                "Ledger Disposition": "",
                "Matched Returns": "否",
                "Matched Ledger": "否",
                "Days Since Refund": 61,
                "Risk Level": "High",
                "Risk Reasons": "Refunded 61 days ago but no warehouse receiving event found.",
                "Suggested Action": "优先核查是否可向 Amazon 申请 reimbursement",
            }
        ]
    )

    diagnostics = build_match_diagnostics(analysis)
    audit = build_order_audit_table(analysis)

    assert diagnostics.loc[0, "Metric"] == "退款订单总数"
    assert diagnostics.loc[0, "Value"] == 1
    assert diagnostics.loc[3, "Metric"] == "未匹配订单数量"
    assert diagnostics.loc[3, "Value"] == 1
    assert audit.loc[0, "是否找到退货记录"] == "否"
    assert audit.loc[0, "是否找到入仓记录"] == "否"


def test_match_diagnostics_include_matching_logs_and_unmatched_reason():
    analysis = pd.DataFrame(
        [
            {
                "Order ID": "702-1",
                "Matched Returns": "否",
                "Matched Ledger": "否",
                "Return Match Method": "",
                "Ledger Match Method": "",
                "Matching Logs": "Attempt 1: order_id exact match -> Failed\nAttempt 2: sku + refund_date +/-30 days -> Failed",
                "Unmatched Reason": "No Returns match; No Ledger reference-id or SKU/date inventory movement found",
            }
        ]
    )

    audit = build_order_audit_table(analysis)

    assert audit.loc[0, "匹配方法"] == ""
    assert "Attempt 1" in audit.loc[0, "技术匹配日志"]
    assert "No Ledger" in audit.loc[0, "未匹配原因"]


def test_reimbursement_match_status_flows_into_analysis():
    returns = pd.DataFrame(columns=["order-id"])
    payments = pd.DataFrame(
        [
            {
                "order-id": "702-1234567-7654321",
                "posted-date": "2026-03-18",
                "transaction-type": "Refund",
                "total": "-50",
                "sku": "SKU-1",
            }
        ]
    )
    reimbursements = pd.DataFrame(
        [
            {
                "amazon-order-id": "702-1234567-7654321",
                "approval-date": "2026-04-01",
                "amount-total": "50",
                "reason": "Customer return not received",
            }
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
        reimbursement_df=reimbursements,
    )

    assert result.loc[0, "Reimbursement Status"] == "已赔偿"
    assert result.loc[0, "Reimbursement Reason"] == "Customer return not received"


def test_order_lifecycle_includes_reimbursement_and_missing_warning_steps():
    order = pd.Series(
        {
            "Refund Date": date(2026, 3, 1),
            "Return Date": pd.NaT,
            "Ledger Date": pd.NaT,
            "Ledger Disposition": "",
            "Days Since Refund": 67,
            "Reimbursement Status": "未赔偿",
        }
    )

    lifecycle = build_order_lifecycle(order, overdue_days=60)
    labels = [step["label"] for step in lifecycle]

    assert labels == [
        "Order Created",
        "Refunded",
        "Return Requested",
        "Return Received",
        "Inventory Ledger Event",
        "Sellable / Unsellable",
        "Reimbursement",
    ]
    assert lifecycle[3]["status"] == "missing"
    assert lifecycle[4]["status"] == "missing"
    assert lifecycle[6]["status"] == "missing"
