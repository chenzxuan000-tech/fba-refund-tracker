from io import BytesIO

import pandas as pd

from src.ai_report import (
    build_report_context,
    build_report_messages,
    generate_ai_report,
    get_provider_config,
)
from src.analysis import build_excel_export


def test_build_report_context_keeps_only_operational_summary_fields():
    order_analysis = pd.DataFrame(
        [
            {
                "Order ID": "111-1",
                "ASIN": "B001",
                "SKU": "SKU-1",
                "Product Name": "Widget A",
                "Refund Date": "2026-03-01",
                "Return Date": "",
                "Days Since Refund": 78,
                "Return Reason": "",
                "Refund Amount": 39.99,
                "Status": "已退款但超过60天未退货",
                "Risk Level": "High",
                "Suggested Action": "优先核查是否可向 Amazon 申请 reimbursement",
            }
        ]
    )
    reason_analysis = {
        "asin_summary": pd.DataFrame(
            [{"ASIN": "B001", "SKU": "SKU-1", "退款订单数": 3, "总退款金额": 99.99}]
        ),
        "reason_category_summary": pd.DataFrame(
            [{"Reason Category": "疑似白嫖 / 异常退款", "Reason Count": 1, "Reason Share": 1.0}]
        ),
        "asin_reason_distribution": pd.DataFrame(
            [{"ASIN": "B001", "Reason Category": "疑似白嫖 / 异常退款", "Reason Count": 1}]
        ),
        "asin_recommendations": pd.DataFrame(
            [{"ASIN": "B001", "建议重点": "跟进超过60天未退回退款订单"}]
        ),
    }
    reason_summary = pd.DataFrame(
        [{"Return Reason": "not returned", "Return Count": 1, "Reason Share": 1.0}]
    )

    context = build_report_context(order_analysis, reason_summary, reason_analysis)

    assert context["overall"]["refund_order_count"] == 1
    assert context["overall"]["high_risk_order_count"] == 1
    assert context["top_review_orders"][0]["Order ID"] == "111-1"
    assert context["asin_summary"][0]["ASIN"] == "B001"
    assert context["asin_recommendations"][0]["建议重点"] == "跟进超过60天未退回退款订单"


def test_build_report_messages_requires_chinese_operations_report_sections():
    context = {"overall": {"refund_order_count": 1}, "top_review_orders": []}

    messages = build_report_messages(context)
    prompt = messages[-1]["content"]

    assert messages[0]["role"] == "system"
    assert "整体退款风险总结" in prompt
    assert "哪些订单建议人工去卖家后台开 Case 核查" in prompt
    assert "中文" in prompt
    assert "运营总监" in prompt
    assert "不要营销文风" in prompt
    assert "已确认事实" in prompt
    assert "高概率推断" in prompt
    assert "AI猜测" in prompt
    assert "禁止把“系统未匹配到明确回仓记录”写成“商品未退回”" in prompt


def test_generate_ai_report_posts_to_selected_provider_and_extracts_content():
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "## 运营分析报告\n建议核查 B001。"}}
                ]
            }

    class FakeSession:
        def __init__(self):
            self.request = None

        def post(self, url, headers, json, timeout):
            self.request = {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
            return FakeResponse()

    session = FakeSession()
    config = get_provider_config("deepseek")

    report = generate_ai_report(
        provider_config=config,
        api_key="secret-key",
        model="deepseek-chat",
        context={"overall": {"refund_order_count": 1}},
        session=session,
    )

    assert "运营分析报告" in report
    assert session.request["url"] == "https://api.deepseek.com/chat/completions"
    assert session.request["headers"]["Authorization"] == "Bearer secret-key"
    assert session.request["json"]["model"] == "deepseek-chat"
    assert session.request["json"]["messages"][0]["role"] == "system"
    assert session.request["json"]["temperature"] == 0.3


def test_deepseek_default_model_uses_v4_pro():
    config = get_provider_config("deepseek")

    assert config.default_model == "deepseek-v4-pro"
    assert "flash" not in config.default_model.lower()
    assert "lite" not in config.default_model.lower()


def test_excel_export_can_include_ai_report_sheet():
    content = build_excel_export(
        pd.DataFrame([{"Order ID": "111-1"}]),
        pd.DataFrame(),
        reason_analysis=None,
        ai_report_markdown="## AI 报告\n建议核查高风险订单。",
    )
    workbook = pd.ExcelFile(BytesIO(content))

    assert "AI报告" in workbook.sheet_names
