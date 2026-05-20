from datetime import date
from io import BytesIO

import pandas as pd

from src.analysis import (
    analyze_refunds_returns,
    build_excel_export,
    build_return_reason_analysis,
    categorize_return_reason,
    detect_columns,
    summarize_return_reasons,
)


def test_detect_columns_accepts_common_seller_central_variants():
    returns = pd.DataFrame(
        columns=[
            "Amazon Order ID",
            "Merchant SKU",
            "ASIN",
            "Title",
            "Return Date",
            "Reason",
            "Detailed Disposition",
        ]
    )
    payments = pd.DataFrame(
        columns=[
            "order id",
            "posted date",
            "transaction type",
            "description",
            "total",
        ]
    )

    return_columns = detect_columns(returns, "returns")
    payment_columns = detect_columns(payments, "payments")

    assert return_columns.order_id == "Amazon Order ID"
    assert return_columns.sku == "Merchant SKU"
    assert return_columns.product_name == "Title"
    assert return_columns.return_reason == "Reason"
    assert return_columns.disposition == "Detailed Disposition"
    assert payment_columns.order_id == "order id"
    assert payment_columns.refund_date == "posted date"
    assert payment_columns.refund_amount == "total"


def test_analyze_refunds_returns_marks_returned_missing_and_overdue_orders():
    returns = pd.DataFrame(
        [
            {
                "amazon-order-id": "111-000001",
                "asin": "B001",
                "sku": "SKU-1",
                "product-name": "Widget A",
                "return-date": "2026-03-10",
                "return-reason": "No longer needed",
                "disposition": "SELLABLE",
            },
            {
                "amazon-order-id": "111-000002",
                "asin": "B002",
                "sku": "SKU-2",
                "product-name": "Widget B",
                "return-date": "2026-03-12",
                "return-reason": "Defective",
                "disposition": "CUSTOMER_DAMAGED",
            },
        ]
    )
    payments = pd.DataFrame(
        [
            {
                "order id": "111-000001",
                "posted date": "2026-03-01",
                "transaction type": "Refund",
                "description": "Refund",
                "total": "-19.99",
            },
            {
                "order id": "111-000002",
                "posted date": "2026-03-01",
                "transaction type": "Refund",
                "description": "Refund",
                "total": "-29.99",
            },
            {
                "order id": "111-000003",
                "posted date": "2026-04-15",
                "transaction type": "Refund",
                "description": "Refund",
                "total": "-39.99",
            },
            {
                "order id": "111-000004",
                "posted date": "2026-02-01",
                "transaction type": "Refund",
                "description": "Refund",
                "total": "-49.99",
            },
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
    )
    by_order = result.set_index("Order ID")

    assert by_order.loc["111-000001", "Status"] == "已退货且可售"
    assert by_order.loc["111-000001", "Risk Level"] == "Low"
    assert by_order.loc["111-000002", "Status"] == "已退货但不可售"
    assert by_order.loc["111-000002", "Risk Level"] == "Needs Review"
    assert by_order.loc["111-000003", "Status"] == "已退款但未找到退货记录"
    assert by_order.loc["111-000003", "Risk Level"] == "Needs Review"
    assert by_order.loc["111-000004", "Status"] == "已退款但超过60天未退货"
    assert by_order.loc["111-000004", "Risk Level"] == "Needs Review"
    assert by_order.loc["111-000004", "Evidence Confidence"] == "Low Confidence"
    assert by_order.loc["111-000004", "Days Since Refund"] == 106
    assert "不能直接判定商品未退回" in by_order.loc["111-000004", "Suggested Action"]


def test_analyze_refunds_returns_uses_selected_observation_window_for_risk_score():
    returns = pd.DataFrame(columns=["amazon-order-id"])
    payments = pd.DataFrame(
        [
            {
                "order id": "111-000099",
                "posted date": "2026-04-01",
                "transaction type": "Refund",
                "description": "Refund",
                "total": "-129.99",
            },
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=30,
        as_of=date(2026, 5, 18),
    )

    row = result.iloc[0]
    assert row["Status"] == "已退款但超过30天未退货"
    assert row["Risk Level"] == "Needs Review"
    assert row["Risk Diagnosis"] == "需要人工确认"
    assert "退款超过30天" in row["Risk Factors"]


def test_analyze_refunds_returns_backfills_blank_payment_asin_sku_from_returns():
    returns = pd.DataFrame(
        [
            {
                "amazon-order-id": "111-000001",
                "asin": "B001",
                "sku": "SKU-1",
                "product-name": "Widget A",
                "return-date": "2026-03-10",
                "return-reason": "QUALITY_UNACCEPTABLE",
                "disposition": "SELLABLE",
            }
        ]
    )
    payments = pd.DataFrame(
        [
            {
                "order id": "111-000001",
                "posted date": "2026-03-01",
                "transaction type": "Refund",
                "description": "Refund",
                "total": "-19.99",
                "asin": "",
                "sku": "",
                "product name": "",
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
    reason_analysis = build_return_reason_analysis(result)
    distribution = reason_analysis["asin_reason_distribution"]

    assert row["ASIN"] == "B001"
    assert row["SKU"] == "SKU-1"
    assert row["Product Name"] == "Widget A"
    assert distribution.loc[0, "ASIN"] == "B001"
    assert distribution.loc[0, "SKU"] == "SKU-1"


def test_analyze_refunds_returns_uses_first_nonblank_asin_sku_in_payment_lines():
    returns = pd.DataFrame(columns=["amazon-order-id"])
    payments = pd.DataFrame(
        [
            {
                "order id": "111-000010",
                "posted date": "2026-03-01",
                "transaction type": "Refund",
                "description": "Refund adjustment",
                "total": "-5.00",
                "asin": "",
                "sku": "",
                "product name": "",
            },
            {
                "order id": "111-000010",
                "posted date": "2026-03-01",
                "transaction type": "Refund",
                "description": "Refund principal",
                "total": "-20.00",
                "asin": "B010",
                "sku": "SKU-10",
                "product name": "Widget 10",
            },
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
    )

    assert result.loc[0, "ASIN"] == "B010"
    assert result.loc[0, "SKU"] == "SKU-10"
    assert result.loc[0, "Product Name"] == "Widget 10"
    assert result.loc[0, "Refund Amount"] == 25.0


def test_return_reason_analysis_labels_unidentified_asin_sku_instead_of_blank_cells():
    analysis = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "",
                "SKU": "",
                "Ledger SKU": "",
                "Product Name": "",
                "Return Date": pd.NaT,
                "Return Reason": "",
                "Refund Amount": 25.0,
                "Status": "已退款但超过60天未退货",
                "Risk Level": "High",
            },
            {
                "Order ID": "111-2",
                "ASIN": "",
                "SKU": "",
                "Ledger SKU": "LEDGER-SKU-1",
                "Product Name": "",
                "Return Date": pd.NaT,
                "Return Reason": "",
                "Refund Amount": 30.0,
                "Status": "已退款但超过60天未退货",
                "Risk Level": "High",
            },
        ]
    )

    result = build_return_reason_analysis(analysis)
    distribution = result["asin_reason_distribution"].sort_values("Refund Amount").reset_index(drop=True)

    assert distribution.loc[0, "ASIN"] == "未识别 ASIN"
    assert distribution.loc[0, "SKU"] == "未识别 SKU"
    assert distribution.loc[1, "ASIN"] == "未识别 ASIN"
    assert distribution.loc[1, "SKU"] == "LEDGER-SKU-1"


def test_summarize_return_reasons_groups_by_asin_sku_and_reason():
    returns = pd.DataFrame(
        [
            {"asin": "B001", "sku": "SKU-1", "return-reason": "Too small"},
            {"asin": "B001", "sku": "SKU-1", "return-reason": "Too small"},
            {"asin": "B001", "sku": "SKU-1", "return-reason": "Not as described"},
        ]
    )

    summary = summarize_return_reasons(returns)

    assert summary.to_dict("records") == [
        {
            "ASIN": "B001",
            "SKU": "SKU-1",
            "Return Reason": "Too small",
                "Reason Category": "尺寸/适配问题",
            "Return Count": 2,
            "Reason Share": 0.6667,
        },
        {
            "ASIN": "B001",
            "SKU": "SKU-1",
            "Return Reason": "Not as described",
            "Reason Category": "Listing 信息不清楚",
            "Return Count": 1,
            "Reason Share": 0.3333,
        },
    ]


def test_build_excel_export_contains_analysis_and_summary_sheets():
    analysis = pd.DataFrame(
        [{"Order ID": "111-000001", "Risk Level": "High", "Refund Amount": 19.99}]
    )
    summary = pd.DataFrame(
        [{"ASIN": "B001", "SKU": "SKU-1", "Return Reason": "Defective", "Return Count": 1}]
    )

    content = build_excel_export(analysis, summary)
    workbook = pd.ExcelFile(BytesIO(content))

    assert workbook.sheet_names == ["风险订单", "匹配诊断", "退货原因汇总"]


def test_build_excel_export_includes_return_reason_analysis_sheets():
    analysis = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "B001",
                "SKU": "SKU-1",
                "Product Name": "Widget A",
                "Return Date": pd.NaT,
                "Return Reason": "",
                "Refund Amount": 25.0,
                "Status": "已退款但超过60天未退货",
                "Risk Level": "High",
            }
        ]
    )
    summary = pd.DataFrame()
    reason_analysis = build_return_reason_analysis(analysis)

    content = build_excel_export(analysis, summary, reason_analysis)
    workbook = pd.ExcelFile(BytesIO(content))

    assert workbook.sheet_names == [
        "风险订单",
        "匹配诊断",
        "退货原因汇总",
        "ASIN 汇总",
        "原因分类汇总",
        "ASIN 原因分布",
        "ASIN 优化建议",
        "原因建议",
    ]


def test_analyze_refunds_returns_parses_currency_amount_formats():
    returns = pd.DataFrame(columns=["amazon-order-id"])
    payments = pd.DataFrame(
        [
            {
                "order id": "111-000005",
                "posted date": "2026-05-01",
                "transaction type": "Refund",
                "total": "($1,234.56)",
            }
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=30,
        as_of=date(2026, 5, 18),
    )

    assert result.loc[0, "Refund Amount"] == 1234.56


def test_categorize_return_reason_maps_common_reason_patterns():
    assert categorize_return_reason("Not as described, missing details") == "Listing 信息不清楚"
    assert categorize_return_reason("Too small for my table") == "尺寸/适配问题"
    assert categorize_return_reason("Defective item stopped working") == "产品故障"
    assert categorize_return_reason("Arrived damaged by carrier") == "配送破损"
    assert categorize_return_reason("No longer needed") == "不想要了"
    assert categorize_return_reason("QUALITY_UNACCEPTABLE") == "产品质量问题"
    assert categorize_return_reason("MISSING_PARTS") == "缺件问题"
    assert categorize_return_reason("ORDERED_WRONG_ITEM") == "买错/下错单"
    assert categorize_return_reason("UNDELIVERABLE_UNKNOWN") == "配送/无法送达"
    assert categorize_return_reason("") == "其他"


def test_build_return_reason_analysis_summarizes_asin_risk_and_recommendations():
    analysis = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "B001",
                "SKU": "SKU-1",
                "Product Name": "Widget A",
                "Return Date": date(2026, 3, 10),
                "Return Reason": "Too small",
                "Disposition / Sellable Status": "SELLABLE",
                "Refund Amount": 10.0,
                "Status": "已退货且可售",
                "Risk Level": "Low",
            },
            {
                "Order ID": "111-2",
                "ASIN": "B001",
                "SKU": "SKU-1",
                "Product Name": "Widget A",
                "Return Date": date(2026, 3, 12),
                "Return Reason": "Defective, stopped working",
                "Disposition / Sellable Status": "CUSTOMER_DAMAGED",
                "Refund Amount": 20.0,
                "Status": "已退货但不可售",
                "Risk Level": "Medium",
            },
            {
                "Order ID": "111-3",
                "ASIN": "B001",
                "SKU": "SKU-1",
                "Product Name": "Widget A",
                "Return Date": pd.NaT,
                "Return Reason": "",
                "Disposition / Sellable Status": "",
                "Refund Amount": 30.0,
                "Status": "已退款但超过60天未退货",
                "Risk Level": "High",
            },
            {
                "Order ID": "222-1",
                "ASIN": "B002",
                "SKU": "SKU-2",
                "Product Name": "Widget B",
                "Return Date": date(2026, 3, 15),
                "Return Reason": "No longer needed",
                "Disposition / Sellable Status": "SELLABLE",
                "Refund Amount": 40.0,
                "Status": "已退货且可售",
                "Risk Level": "Low",
            },
        ]
    )

    result = build_return_reason_analysis(analysis)
    asin_summary = result["asin_summary"].set_index("ASIN")
    category_summary = result["reason_category_summary"].set_index("Reason Category")
    recommendations = result["asin_recommendations"].set_index("ASIN")

    assert asin_summary.loc["B001", "退款订单数"] == 3
    assert asin_summary.loc["B001", "退货订单数"] == 2
    assert asin_summary.loc["B001", "未退回订单数"] == 1
    assert asin_summary.loc["B001", "超过60天未退回订单数"] == 1
    assert asin_summary.loc["B001", "可售退货数"] == 1
    assert asin_summary.loc["B001", "不可售退货数"] == 1
    assert asin_summary.loc["B001", "总退款金额"] == 60.0
    assert category_summary.loc["尺寸/适配问题", "Reason Count"] == 1
    assert category_summary.loc["产品故障", "Reason Share"] == 0.25
    assert recommendations.loc["B001", "是否需要补充尺寸图"] == "是"
    assert recommendations.loc["B001", "是否存在产品质量风险"] == "是"
    assert recommendations.loc["B001", "是否需要增加使用场景说明"] == "是"


def test_return_reason_analysis_treats_support_confirmed_return_as_returned():
    analysis = pd.DataFrame(
        [
            {
                "Order ID": "111-support",
                "ASIN": "B001",
                "SKU": "SKU-1",
                "Product Name": "Widget A",
                "Return Date": pd.NaT,
                "Return Reason": "",
                "Disposition / Sellable Status": "",
                "Refund Amount": 50.0,
                "Status": "已退款但超过60天未退货",
                "Risk Level": "Low",
                "Amazon Support Confirmation": "Amazon确认可售",
            }
        ]
    )

    result = build_return_reason_analysis(analysis)
    asin_summary = result["asin_summary"].set_index("ASIN")

    assert asin_summary.loc["B001", "退货订单数"] == 1
    assert asin_summary.loc["B001", "未退回订单数"] == 0
    assert asin_summary.loc["B001", "超过60天未退回订单数"] == 0
    assert asin_summary.loc["B001", "可售退货数"] == 1
