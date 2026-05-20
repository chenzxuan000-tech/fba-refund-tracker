from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional

import pandas as pd
import requests


REPORT_SECTIONS = [
    "整体退款风险总结",
    "哪些 ASIN 需要重点关注",
    "哪些订单疑似退款后未退货",
    "主要退货原因是什么",
    "对产品本身的优化建议",
    "对 Listing 的优化建议",
    "对图片、标题、五点描述、A+ 的修改建议",
    "对客服和售后策略的建议",
    "哪些订单建议人工去卖家后台开 Case 核查",
]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    label: str
    endpoint: str
    default_model: str


PROVIDER_CONFIGS = {
    "openai": ProviderConfig(
        name="openai",
        label="OpenAI",
        endpoint="https://api.openai.com/v1/chat/completions",
        default_model="gpt-5.4",
    ),
    "minimax": ProviderConfig(
        name="minimax",
        label="MiniMax",
        endpoint="https://api.minimax.io/v1/chat/completions",
        default_model="MiniMax-M2.7",
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        label="DeepSeek",
        endpoint="https://api.deepseek.com/chat/completions",
        default_model="deepseek-v4-pro",
    ),
}


def get_provider_config(provider: str) -> ProviderConfig:
    key = provider.strip().lower()
    if key not in PROVIDER_CONFIGS:
        raise ValueError(f"Unsupported AI provider: {provider}")
    return PROVIDER_CONFIGS[key]


def build_report_context(
    order_analysis_df: pd.DataFrame,
    return_reason_summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    returned_mask = _has_return_date(order_analysis_df["Return Date"])
    high_risk = order_analysis_df[order_analysis_df["Risk Level"].eq("High")]
    review_orders = order_analysis_df[order_analysis_df["Risk Level"].isin(["High", "Needs Review"])]
    not_returned = order_analysis_df[~returned_mask]
    suspicious = review_orders[~_has_return_date(review_orders["Return Date"])]

    total_refund_amount = pd.to_numeric(
        order_analysis_df["Refund Amount"], errors="coerce"
    ).fillna(0).sum()

    return {
        "overall": {
            "refund_order_count": int(len(order_analysis_df)),
            "returned_order_count": int(returned_mask.sum()),
            "not_returned_order_count": int(len(not_returned)),
            "high_risk_order_count": int(len(high_risk)),
            "manual_review_order_count": int(len(review_orders)),
            "total_refund_amount": round(float(total_refund_amount), 2),
        },
        "asin_summary": _frame_records(
            reason_analysis.get("asin_summary", pd.DataFrame()),
            limit=20,
        ),
        "reason_category_summary": _frame_records(
            reason_analysis.get("reason_category_summary", pd.DataFrame()),
            limit=20,
        ),
        "return_reason_summary": _frame_records(return_reason_summary_df, limit=20),
        "asin_reason_distribution": _frame_records(
            reason_analysis.get("asin_reason_distribution", pd.DataFrame()),
            limit=40,
        ),
        "asin_recommendations": _frame_records(
            reason_analysis.get("asin_recommendations", pd.DataFrame()),
            limit=20,
        ),
        "top_review_orders": _frame_records(
            suspicious.sort_values(
                ["Days Since Refund", "Refund Amount"], ascending=[False, False]
            ),
            limit=20,
            columns=[
                "Order ID",
                "ASIN",
                "SKU",
                "Product Name",
                "Refund Date",
                "Days Since Refund",
                "Refund Amount",
                "Operation Priority",
                "Priority Action",
                "Priority Reasons",
                "Operation Status",
                "Risk Level With Confidence",
                "Risk Diagnosis",
                "Conclusion Type",
                "Conclusion Boundary",
                "Evidence Summary",
                "Suggested Action",
            ],
        ),
    }


def build_report_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "你是一名亚马逊运营总监，负责基于退款、退货、ASIN 表现和可疑订单数据，"
        "输出稳定、专业、结构化、可执行的运营分析报告。"
        "只根据用户提供的数据分析，不要编造外部事实；不要营销文风、不要口语化、不要发散。"
        "必须明确区分已确认事实、高概率推断和 AI 猜测，禁止把推断包装成事实。"
    )
    section_text = "\n".join(f"{index}. {section}" for index, section in enumerate(REPORT_SECTIONS, 1))
    user = f"""
请根据以下 JSON 数据生成一份中文亚马逊 FBA 退货退款运营分析报告。

写作要求：
- 语言使用中文。
- 语气像运营总监写给运营团队的内部分析报告：专业、克制、稳定。
- 输出必须简洁、结构化、可执行，优先给结论、风险判断和行动项。
- 不要营销文风，不要口语化，不要夸张修辞，不要发散联想。
- 不要空泛建议；每条建议尽量对应 ASIN、订单、原因分类或具体运营动作。
- 每条关键判断必须标注依据类型：[已确认事实]、[高概率推断] 或 [AI猜测]。
- 禁止把“系统未匹配到明确回仓记录”写成“商品未退回”；应写成“系统暂未匹配到明确回仓记录，建议人工确认”。
- AI猜测只能用于产品、Listing、客服策略假设，不能用于订单是否退回的事实判断。
- 若数据不足，请明确说明“当前数据不足以判断”，不要编造。
- 对订单核查建议要给出 Order ID、ASIN、退款金额、天数和建议动作。
- 用 Markdown 输出，标题层级清晰。
- 不要输出 JSON、代码块、```、原始数据字典或接口调试信息。
- 表格只在非常必要时使用，优先用短段落和要点列表。
- 每个章节控制在 3-6 条要点，避免长篇堆叠。

报告必须包含以下章节：
{section_text}

分析数据 JSON：
{json.dumps(_json_safe(context), ensure_ascii=False, indent=2)}
""".strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def generate_ai_report(
    provider_config: ProviderConfig,
    api_key: str,
    model: str,
    context: dict[str, Any],
    endpoint_override: Optional[str] = None,
    session: Any = requests,
    timeout: int = 90,
) -> str:
    if not api_key.strip():
        raise ValueError("API Key is required")
    if not model.strip():
        raise ValueError("Model is required")

    endpoint = endpoint_override.strip() if endpoint_override else provider_config.endpoint
    payload = {
        "model": model.strip(),
        "messages": build_report_messages(context),
        "temperature": 0.3,
    }
    response = session.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"AI API request failed: {response.status_code} {_response_text(response)}")

    data = response.json()
    content = _extract_chat_completion_text(data)
    if not content:
        raise RuntimeError("AI API response did not include report content")
    return content.strip()


def _frame_records(
    df: pd.DataFrame,
    limit: int,
    columns: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    frame = df.copy()
    if columns:
        frame = frame[[column for column in columns if column in frame.columns]]
    return [_json_safe(record) for record in frame.head(limit).to_dict("records")]


def _has_return_date(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype(str).str.strip().ne("")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _extract_chat_completion_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
            ).strip()
    return str(data.get("output_text") or "")


def _response_text(response: Any) -> str:
    try:
        return response.text
    except AttributeError:
        return ""
