from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Optional

import pandas as pd

from src.analyzer import attach_ledger_receipts
from src.matcher import ColumnMap, detect_columns, find_column, normalize_column_name, normalize_order_id
from src.parser import parse_date_series, read_report_file
from src.risk import apply_risk_scoring


OUTPUT_COLUMNS = [
    "Order ID",
    "ASIN",
    "SKU",
    "Product Name",
    "Refund Date",
    "Return Date",
    "Return Quantity",
    "Days Since Refund",
    "Return Reason",
    "Disposition / Sellable Status",
    "Refund Amount",
    "Marketplace",
    "Buyer Name",
    "Buyer Email",
    "Matched Returns",
    "Return Match Method",
    "Matched Ledger",
    "Ledger Match Method",
    "Matching Logs",
    "Unmatched Reason",
    "Amazon Warehouse Received",
    "Ledger Date",
    "Ledger Disposition",
    "Ledger Event Type",
    "Ledger FNSKU",
    "Ledger SKU",
    "Ledger Quantity",
    "Fulfillment Center ID",
    "Ledger Reason",
    "Reimbursement Status",
    "Reimbursement Date",
    "Reimbursement Amount",
    "Reimbursement Reason",
    "Status",
    "Risk Level",
    "Risk Diagnosis",
    "Risk Level With Confidence",
    "Risk Score",
    "Risk Explanation",
    "Risk Factors",
    "Risk Reasons",
    "Evidence Confidence",
    "Diagnostic Confidence Reason",
    "Evidence Summary",
    "Conclusion Type",
    "Conclusion Boundary",
    "Amazon Support Confirmation",
    "Potential Return Abuse",
    "Buyer High Risk Count",
    "Buyer Total Refund Amount",
    "Suggested Action",
]

REASON_CATEGORIES = [
    "Listing 信息不清楚",
    "尺寸/适配问题",
    "产品质量问题",
    "缺件问题",
    "买错/下错单",
    "不想要了",
    "配送/无法送达",
    "配送破损",
    "产品故障",
    "疑似白嫖/异常退款",
    "其他",
]

REASON_CATEGORY_KEYWORDS = {
    "Listing 信息不清楚": [
        "not as described",
        "not described",
        "description",
        "wrong item",
        "missing details",
        "not match",
        "inaccurate",
        "listing",
        "与描述不符",
        "描述",
        "信息不清",
        "不符",
    ],
    "尺寸/适配问题": [
        "not_compatible",
        "not compatible",
        "too small",
        "too large",
        "size",
        "dimension",
        "fit",
        "规格",
        "尺寸",
        "太小",
        "太大",
        "不合适",
        "适配",
        "不兼容",
    ],
    "产品质量问题": [
        "quality_unacceptable",
        "quality unacceptable",
        "quality",
        "faulty",
        "poor",
        "质量",
        "做工",
        "材质",
        "耐用",
    ],
    "缺件问题": [
        "missing_parts",
        "missing parts",
        "missing part",
        "缺件",
        "少件",
        "配件缺失",
    ],
    "买错/下错单": [
        "ordered_wrong_item",
        "ordered wrong item",
        "ordered by mistake",
        "accidental order",
        "wrong item",
        "买错",
        "下错",
        "误购",
    ],
    "不想要了": [
        "unwanted_item",
        "unwanted item",
        "no longer needed",
        "changed mind",
        "better price",
        "不想要",
        "不需要",
        "后悔",
    ],
    "配送/无法送达": [
        "undeliverable_unknown",
        "undeliverable unknown",
        "undeliverable",
        "delivery",
        "无法送达",
        "未妥投",
        "配送失败",
    ],
    "配送破损": [
        "damaged_by_carrier",
        "damaged by carrier",
        "arrived damaged",
        "shipping",
        "carrier",
        "package",
        "运输",
        "配送破损",
        "包装破损",
        "到货损坏",
    ],
    "产品故障": [
        "defective",
        "broken",
        "not working",
        "stopped working",
        "故障",
        "坏",
        "不能用",
        "不好用",
    ],
    "疑似白嫖/异常退款": [
        "item not received",
        "missing item",
        "empty box",
        "refund only",
        "not returned",
        "未收到",
        "空包",
        "仅退款",
        "未退货",
    ],
}

REASON_CATEGORY_ADVICE = {
    "Listing 信息不清楚": {
        "可能原因": "买家对产品功能、适用范围或包装内容理解不一致。",
        "对产品的影响": "容易引发预期落差，推高退款率和差评风险。",
        "建议优化动作": "检查标题、主图卖点、五点描述和 A+ 内容，补充关键限制条件和使用前提。",
    },
    "尺寸/适配问题": {
        "可能原因": "买家没有准确理解尺寸、规格、型号或兼容范围。",
        "对产品的影响": "退货多发生在购买决策前信息不足，可能影响转化质量。",
        "建议优化动作": "补充尺寸图、适配清单、对比图和使用场景，避免只在文字里说明规格。",
    },
    "产品质量问题": {
        "可能原因": "用户认为产品做工、材质、耐用性不符合预期。",
        "对产品的影响": "可能带来差评、不可售退货和账号健康风险。",
        "建议优化动作": "检查差评内容、退货备注和批次质量，确认主图卖点是否过度承诺。",
    },
    "缺件问题": {
        "可能原因": "包装内配件、说明书或组件缺失，或买家未理解包装内容。",
        "对产品的影响": "会直接影响可用性，也容易引发客服投诉。",
        "建议优化动作": "核查装箱 SOP，主图或 A+ 明确包装清单，必要时增加出厂抽检。",
    },
    "买错/下错单": {
        "可能原因": "买家选择了错误型号、颜色、规格或误下单。",
        "对产品的影响": "通常不是产品硬伤，但说明变体和适配提示可能不够清晰。",
        "建议优化动作": "优化变体命名、规格选择提示和对比表，减少误购。",
    },
    "不想要了": {
        "可能原因": "买家主观改变需求或冲动购买后退货。",
        "对产品的影响": "对产品质量指向较弱，但会推高运营成本。",
        "建议优化动作": "关注高退款广告词和购买场景，减少不精准流量。",
    },
    "配送/无法送达": {
        "可能原因": "地址、配送或仓库履约异常导致无法送达。",
        "对产品的影响": "更多指向履约链路，需区分是否与产品包装尺寸有关。",
        "建议优化动作": "查看 FBA 配送异常、包装尺寸和站点配送限制，必要时开 Case 核查。",
    },
    "配送破损": {
        "可能原因": "运输途中挤压、包装保护不足或承运商损坏。",
        "对产品的影响": "可能造成不可售退货和赔偿机会。",
        "建议优化动作": "加强包装保护，核查 Ledger disposition，并筛选可开 Case 的订单。",
    },
    "产品故障": {
        "可能原因": "产品无法正常使用、功能失效或买家认为存在缺陷。",
        "对产品的影响": "质量风险较高，可能影响评分和复购。",
        "建议优化动作": "复盘差评、客服反馈和批次，补充使用说明，必要时暂停问题批次。",
    },
    "疑似白嫖/异常退款": {
        "可能原因": "已退款但长期未发现退货或入仓记录。",
        "对产品的影响": "直接造成退款损失，需要运营人工核查。",
        "建议优化动作": "优先核查订单生命周期、库存流水和赔偿状态，必要时去卖家后台开 Case。",
    },
    "其他": {
        "可能原因": "报表原因字段为空、站点原因码不在当前规则内，或原因描述过于模糊。",
        "对产品的影响": "需要结合订单和买家反馈二次判断。",
        "建议优化动作": "抽样查看原始退货备注，补充本店铺常见原因码到分类规则。",
    },
}


def analyze_refunds_returns(
    returns_df: pd.DataFrame,
    payments_df: pd.DataFrame,
    observation_window_days: int,
    as_of: Optional[date] = None,
    ledger_df: Optional[pd.DataFrame] = None,
    reimbursement_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    as_of = as_of or date.today()
    return_columns = detect_columns(returns_df, "returns")
    payment_columns = detect_columns(payments_df, "payments")

    returns = _prepare_returns(returns_df, return_columns)
    refunds = _prepare_refunds(payments_df, payment_columns)

    merged = refunds.merge(returns, on="Order ID", how="left", suffixes=("", "_return"))
    if "ASIN_return" in merged.columns:
        merged["ASIN"] = _fill_blank_from(merged["ASIN"], merged["ASIN_return"])
    if "SKU_return" in merged.columns:
        merged["SKU"] = _fill_blank_from(merged["SKU"], merged["SKU_return"])
    if "Product Name_return" in merged.columns:
        merged["Product Name"] = _fill_blank_from(merged["Product Name"], merged["Product Name_return"])
    merged["Matched Returns"] = merged["Return Date"].notna().map({True: "是", False: "否"})
    merged["Return Match Method"] = merged["Matched Returns"].map(
        {"是": "order_id exact match", "否": ""}
    )
    merged["Matching Logs"] = merged["Matched Returns"].map(
        {
            "是": "Attempt 1: order_id == return order-id -> Matched",
            "否": "Attempt 1: order_id == return order-id -> Failed",
        }
    )
    merged["Unmatched Reason"] = merged["Matched Returns"].map(
        {"是": "", "否": "No Returns match"}
    )

    merged["Days Since Refund"] = (
        pd.Timestamp(as_of) - pd.to_datetime(merged["Refund Date"], errors="coerce")
    ).dt.days
    merged["Status"] = merged.apply(
        lambda row: _status_for_row(row, observation_window_days), axis=1
    )
    merged["Risk Level"] = merged["Status"].map(_risk_for_status)
    merged["Suggested Action"] = merged["Status"].map(_suggested_action_for_status)

    for column in OUTPUT_COLUMNS:
        if column not in merged.columns:
            merged[column] = pd.NA

    result = merged[OUTPUT_COLUMNS].copy()
    result["Refund Date"] = _date_series(result["Refund Date"])
    result["Return Date"] = _date_series(result["Return Date"])
    result["Refund Amount"] = pd.to_numeric(result["Refund Amount"], errors="coerce").abs()
    result = attach_ledger_receipts(result, ledger_df)
    if "Ledger SKU" in result.columns:
        result["SKU"] = _fill_blank_from(result["SKU"], result["Ledger SKU"])
    result = _attach_reimbursements(result, reimbursement_df)
    result["Reimbursement Report Available"] = "是" if reimbursement_df is not None else "否"
    result = apply_risk_scoring(result, overdue_days=observation_window_days)
    return result.sort_values(
        by=["Risk Score", "Days Since Refund"],
        key=_risk_sort_key,
        ascending=[False, False],
    ).reset_index(drop=True)


def summarize_return_reasons(returns_df: pd.DataFrame) -> pd.DataFrame:
    columns = _detect_return_summary_columns(returns_df)
    returns = pd.DataFrame(
        {
            "ASIN": _optional_text(returns_df, columns.asin),
            "SKU": _optional_text(returns_df, columns.sku),
            "Return Reason": _optional_text(returns_df, columns.return_reason),
        }
    )
    returns["Reason Category"] = returns["Return Reason"].apply(categorize_return_reason)
    summary = (
        returns.groupby(["ASIN", "SKU", "Return Reason", "Reason Category"], dropna=False)
        .size()
        .reset_index(name="Return Count")
        .sort_values(["Return Count", "ASIN", "SKU"], ascending=[False, True, True])
        .reset_index(drop=True)
    )
    total = summary["Return Count"].sum()
    summary["Reason Share"] = 0 if total == 0 else (summary["Return Count"] / total).round(4)
    return summary[["ASIN", "SKU", "Return Reason", "Reason Category", "Return Count", "Reason Share"]]


def categorize_return_reason(reason: object) -> str:
    text = str(reason or "").strip().lower()
    if not text or text == "nan":
        return "其他"

    for category, keywords in REASON_CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "其他"


def build_return_reason_analysis(analysis_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    enriched = analysis_df.copy()
    for column in OUTPUT_COLUMNS:
        if column not in enriched.columns:
            enriched[column] = pd.NA

    enriched["Return Reason Category"] = enriched["Return Reason"].apply(categorize_return_reason)
    if "Ledger SKU" in enriched.columns:
        enriched["SKU"] = _fill_blank_from(enriched["SKU"], enriched["Ledger SKU"])
    enriched["ASIN"] = _label_blank_identifier(enriched["ASIN"], "未识别 ASIN")
    enriched["SKU"] = _label_blank_identifier(enriched["SKU"], "未识别 SKU")
    amazon_received = enriched.get(
        "Amazon Warehouse Received",
        pd.Series(["未知"] * len(enriched), index=enriched.index),
    ).eq("是")
    support_confirmation = enriched.get(
        "Amazon Support Confirmation",
        pd.Series([""] * len(enriched), index=enriched.index),
    ).fillna("").astype(str)
    support_returned = support_confirmation.isin(
        ["Amazon已确认退回", "Amazon确认可售", "Amazon确认不可售"]
    )
    support_sellable = support_confirmation.eq("Amazon确认可售")
    support_unsellable = support_confirmation.eq("Amazon确认不可售")
    enriched["Is Returned"] = enriched["Return Date"].notna() | amazon_received | support_returned
    enriched["Is Not Returned"] = ~enriched["Is Returned"]
    enriched["Is Overdue Not Returned"] = _is_overdue_not_returned(enriched, 60)
    enriched["Is Sellable Return"] = enriched["Status"].eq("已退货且可售") | support_sellable
    enriched["Is Unsellable Return"] = enriched["Status"].eq("已退货但不可售") | support_unsellable
    enriched["Refund Amount"] = pd.to_numeric(enriched["Refund Amount"], errors="coerce").fillna(0)
    enriched.loc[
        enriched["Is Overdue Not Returned"] & enriched["Risk Level"].eq("High"),
        "Return Reason Category",
    ] = "疑似白嫖/异常退款"

    asin_summary = _build_asin_summary(enriched)
    reason_category_summary = _build_reason_category_summary(enriched)
    asin_reason_distribution = _build_asin_reason_distribution(enriched)
    asin_recommendations = _build_asin_recommendations(asin_summary, asin_reason_distribution)

    return {
        "order_analysis": enriched,
        "asin_summary": asin_summary,
        "reason_category_summary": reason_category_summary,
        "asin_reason_distribution": asin_reason_distribution,
        "asin_recommendations": asin_recommendations,
    }


def build_reason_category_advice() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"原因分类": category, **advice}
            for category, advice in REASON_CATEGORY_ADVICE.items()
        ]
    )


def build_excel_export(
    analysis_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    reason_analysis: Optional[dict[str, pd.DataFrame]] = None,
    ai_report_markdown: Optional[str] = None,
    raw_frames: Optional[dict[str, pd.DataFrame]] = None,
) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        risk_level = analysis_df["Risk Level"] if "Risk Level" in analysis_df.columns else pd.Series([""] * len(analysis_df), index=analysis_df.index)
        risk_orders = analysis_df[risk_level.isin(["High", "Needs Review", "Data Incomplete"])]
        if risk_orders.empty:
            risk_orders = analysis_df
        _localize_export(risk_orders).to_excel(writer, sheet_name="风险订单", index=False)
        _localize_export(analysis_df).to_excel(writer, sheet_name="匹配诊断", index=False)
        summary_df.to_excel(writer, sheet_name="退货原因汇总", index=False)
        if reason_analysis:
            reason_analysis["asin_summary"].to_excel(
                writer, sheet_name="ASIN 汇总", index=False
            )
            reason_analysis["reason_category_summary"].to_excel(
                writer, sheet_name="原因分类汇总", index=False
            )
            reason_analysis["asin_reason_distribution"].to_excel(
                writer, sheet_name="ASIN 原因分布", index=False
            )
            reason_analysis["asin_recommendations"].to_excel(
                writer, sheet_name="ASIN 优化建议", index=False
            )
            build_reason_category_advice().to_excel(writer, sheet_name="原因建议", index=False)
        if raw_frames:
            for name, frame in raw_frames.items():
                if frame is not None and not frame.empty:
                    sheet_name = f"原始数据-{name}"[:31]
                    frame.head(5000).to_excel(writer, sheet_name=sheet_name, index=False)
        if ai_report_markdown:
            pd.DataFrame(
                {
                    "AI Report": [
                        line for line in ai_report_markdown.splitlines() if line.strip()
                    ]
                }
            ).to_excel(writer, sheet_name="AI报告", index=False)
    return output.getvalue()


def _build_asin_summary(enriched: pd.DataFrame) -> pd.DataFrame:
    summary = (
        enriched.groupby(["ASIN", "SKU"], dropna=False)
        .agg(
            Product_Name=("Product Name", "first"),
            退款订单数=("Order ID", "nunique"),
            退货订单数=("Is Returned", "sum"),
            未退回订单数=("Is Not Returned", "sum"),
            超过60天未退回订单数=("Is Overdue Not Returned", "sum"),
            可售退货数=("Is Sellable Return", "sum"),
            不可售退货数=("Is Unsellable Return", "sum"),
            总退款金额=("Refund Amount", "sum"),
        )
        .reset_index()
        .rename(columns={"Product_Name": "Product Name"})
    )
    return summary.sort_values(
        ["总退款金额", "退款订单数"], ascending=[False, False]
    ).reset_index(drop=True)


def _build_reason_category_summary(enriched: pd.DataFrame) -> pd.DataFrame:
    summary = (
        enriched.groupby("Return Reason Category", dropna=False)
        .agg(
            Reason_Count=("Order ID", "nunique"),
            Refund_Amount=("Refund Amount", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "Return Reason Category": "Reason Category",
                "Reason_Count": "Reason Count",
                "Refund_Amount": "Refund Amount",
            }
        )
    )
    total = summary["Reason Count"].sum()
    summary["Reason Share"] = 0 if total == 0 else (summary["Reason Count"] / total).round(4)
    return summary.sort_values(["Reason Count", "Refund Amount"], ascending=[False, False]).reset_index(drop=True)


def _build_asin_reason_distribution(enriched: pd.DataFrame) -> pd.DataFrame:
    distribution = (
        enriched.groupby(["ASIN", "SKU", "Return Reason Category"], dropna=False)
        .agg(
            Reason_Count=("Order ID", "nunique"),
            Refund_Amount=("Refund Amount", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "Return Reason Category": "Reason Category",
                "Reason_Count": "Reason Count",
                "Refund_Amount": "Refund Amount",
            }
        )
    )
    totals = distribution.groupby(["ASIN", "SKU"], dropna=False)["Reason Count"].transform("sum")
    distribution["Reason Share"] = (distribution["Reason Count"] / totals).fillna(0).round(4)
    return distribution.sort_values(
        ["ASIN", "Reason Count", "Refund Amount"], ascending=[True, False, False]
    ).reset_index(drop=True)


def _build_asin_recommendations(
    asin_summary: pd.DataFrame,
    asin_reason_distribution: pd.DataFrame,
) -> pd.DataFrame:
    category_sets = (
        asin_reason_distribution.groupby(["ASIN", "SKU"], dropna=False)["Reason Category"]
        .apply(set)
        .reset_index(name="Categories")
    )
    recommendations = asin_summary.merge(category_sets, on=["ASIN", "SKU"], how="left")
    recommendations["Categories"] = recommendations["Categories"].apply(
        lambda value: value if isinstance(value, set) else set()
    )

    recommendations["是否需要优化主图"] = recommendations["Categories"].apply(
        lambda categories: _yes_no(bool(categories & {"Listing 信息不清楚"}))
    )
    recommendations["是否需要补充尺寸图"] = recommendations["Categories"].apply(
        lambda categories: _yes_no(bool(categories & {"尺寸/适配问题"}))
    )
    recommendations["是否需要优化五点描述"] = recommendations["Categories"].apply(
        lambda categories: _yes_no(bool(categories & {"Listing 信息不清楚", "产品质量问题", "产品故障", "缺件问题"}))
    )
    recommendations["是否需要增加使用场景说明"] = recommendations["Categories"].apply(
        lambda categories: _yes_no(bool(categories & {"不想要了", "买错/下错单", "尺寸/适配问题"}))
    )
    recommendations["是否存在产品质量风险"] = (
        (recommendations["不可售退货数"] > 0)
        | recommendations["Categories"].apply(lambda categories: bool(categories & {"产品质量问题", "产品故障", "缺件问题"}))
    ).map(_yes_no)
    recommendations["建议重点"] = recommendations.apply(_recommendation_focus, axis=1)

    columns = [
        "ASIN",
        "SKU",
        "Product Name",
        "退款订单数",
        "退货订单数",
        "未退回订单数",
        "超过60天未退回订单数",
        "总退款金额",
        "是否需要优化主图",
        "是否需要补充尺寸图",
        "是否需要优化五点描述",
        "是否需要增加使用场景说明",
        "是否存在产品质量风险",
        "建议重点",
    ]
    return recommendations[columns].reset_index(drop=True)


def _localize_export(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "Order ID": "订单号",
        "ASIN": "ASIN",
        "SKU": "SKU",
        "Product Name": "产品名称",
        "Refund Date": "退款日期",
        "Return Date": "退货日期",
        "Return Quantity": "退货数量",
        "Days Since Refund": "距离退款天数",
        "Return Reason": "退货原因",
        "Disposition / Sellable Status": "退货状态",
        "Refund Amount": "退款金额",
        "Marketplace": "站点",
        "Buyer Name": "买家姓名",
        "Buyer Email": "买家邮箱",
        "Matched Returns": "是否找到退货记录",
        "Matched Ledger": "是否找到入仓记录",
        "Amazon Warehouse Received": "FBA 是否入仓",
        "Ledger Date": "入仓记录日期",
        "Ledger Disposition": "库存流水商品状态",
        "Ledger Event Type": "库存流水事件类型",
        "Ledger FNSKU": "库存流水 FNSKU",
        "Ledger SKU": "库存流水 SKU",
        "Ledger Quantity": "库存流水数量",
        "Fulfillment Center ID": "FBA 仓库",
        "Ledger Reason": "库存流水原因",
        "Reimbursement Status": "赔偿状态",
        "Reimbursement Date": "赔偿日期",
        "Reimbursement Amount": "赔偿金额",
        "Reimbursement Reason": "赔偿原因",
        "Operation Priority": "优先级",
        "Priority Score": "优先级分数",
        "Priority Action": "处理时效",
        "Priority Reasons": "优先级原因",
        "Operation Status": "处理状态",
        "Risk Level": "原始风险等级",
        "Risk Diagnosis": "风险诊断",
        "Risk Level With Confidence": "风险等级",
        "Risk Score": "风险分数",
        "Risk Explanation": "风险说明",
        "Risk Factors": "风险原因",
        "Evidence Confidence": "诊断可信度",
        "Diagnostic Confidence Reason": "可信度说明",
        "Evidence Summary": "证据摘要",
        "Conclusion Type": "结论类型",
        "Conclusion Boundary": "结论边界",
        "Amazon Support Confirmation": "Amazon Support确认",
        "Potential Return Abuse": "疑似买家滥用",
        "Buyer High Risk Count": "买家高风险次数",
        "Buyer Total Refund Amount": "买家累计退款金额",
        "Suggested Action": "建议动作",
        "Matching Logs": "技术匹配日志",
        "Unmatched Reason": "未匹配原因",
    }
    localized = df.copy().rename(columns=rename_map)
    if "原始风险等级" in localized.columns:
        localized["原始风险等级"] = localized["原始风险等级"].map(
            {
                "High": "真正高风险",
                "Needs Review": "需要人工确认",
                "Data Incomplete": "数据不完整",
                "Medium": "需要人工确认",
                "Low": "正常/低风险",
            }
        ).fillna(localized["原始风险等级"])
    if "诊断可信度" in localized.columns:
        localized["诊断可信度"] = localized["诊断可信度"].map(
            {"High Confidence": "高可信", "Medium Confidence": "中可信", "Low Confidence": "低可信"}
        ).fillna(localized["诊断可信度"])
    return localized


def _is_overdue_not_returned(enriched: pd.DataFrame, days: int) -> pd.Series:
    not_returned = enriched["Is Not Returned"].fillna(False)
    status = enriched["Status"].astype(str)
    if "Days Since Refund" in enriched.columns:
        days_since_refund = pd.to_numeric(enriched["Days Since Refund"], errors="coerce")
        status_fallback = days_since_refund.isna() & status.str.contains(f"超过{days}天", na=False)
        return not_returned & ((days_since_refund > days) | status_fallback)

    return not_returned & status.str.contains(f"超过{days}天", na=False)


def _detect_return_summary_columns(df: pd.DataFrame) -> ColumnMap:
    normalized = {normalize_column_name(column): column for column in df.columns}
    return ColumnMap(
        asin=find_column(normalized, ["asin"]),
        sku=find_column(normalized, ["sku", "merchantsku", "sellersku", "msku"]),
        return_reason=find_column(normalized, ["returnreason", "reason", "customerreturnreason"]),
    )


def _prepare_returns(df: pd.DataFrame, columns: ColumnMap) -> pd.DataFrame:
    prepared = pd.DataFrame()
    prepared["Order ID"] = _clean_text(df[columns.order_id]).apply(normalize_order_id)
    prepared["ASIN"] = _optional_text(df, columns.asin)
    prepared["SKU"] = _optional_text(df, columns.sku)
    prepared["Product Name"] = _optional_text(df, columns.product_name)
    prepared["Buyer Name"] = _optional_text(df, columns.buyer_name)
    prepared["Buyer Email"] = _optional_text(df, columns.buyer_email)
    prepared["Return Date"] = _optional_date(df, columns.return_date)
    prepared["Return Quantity"] = pd.to_numeric(
        _optional_text(df, columns.return_quantity), errors="coerce"
    )
    prepared["Return Reason"] = _optional_text(df, columns.return_reason)
    prepared["Disposition / Sellable Status"] = _optional_text(df, columns.disposition)
    prepared = prepared.dropna(subset=["Order ID"])
    prepared = prepared[prepared["Order ID"] != ""]
    return prepared.sort_values("Return Date").drop_duplicates("Order ID", keep="last")


def _prepare_refunds(df: pd.DataFrame, columns: ColumnMap) -> pd.DataFrame:
    prepared = pd.DataFrame()
    order_id = _clean_text(df[columns.order_id]).apply(normalize_order_id)
    if columns.description:
        description_order_id = _optional_text(df, columns.description).apply(normalize_order_id)
        order_id = order_id.mask(order_id.eq(""), description_order_id)
    prepared["Order ID"] = order_id
    prepared["Refund Date"] = _optional_date(df, columns.refund_date)
    prepared["Refund Amount"] = _parse_amount_series(_optional_text(df, columns.refund_amount))
    prepared["ASIN"] = _optional_text(df, columns.asin)
    prepared["SKU"] = _optional_text(df, columns.sku)
    prepared["Product Name"] = _optional_text(df, columns.product_name)
    prepared["Marketplace"] = _optional_text(df, columns.marketplace)
    prepared["Buyer Name"] = _optional_text(df, columns.buyer_name)
    prepared["Buyer Email"] = _optional_text(df, columns.buyer_email)

    type_text = _optional_text(df, columns.transaction_type).str.lower()
    description_text = _optional_text(df, columns.description).str.lower()
    refund_text = type_text.str.cat(description_text, sep=" ")
    refund_mask = refund_text.str.contains("refund|退款|return", regex=True, na=False)
    if refund_mask.any():
        prepared = prepared[refund_mask]

    prepared = prepared.dropna(subset=["Order ID", "Refund Date"])
    prepared = prepared[prepared["Order ID"] != ""]
    grouped = (
        prepared.groupby("Order ID", dropna=False)
        .agg(
            {
                "Refund Date": "min",
                "Refund Amount": "sum",
                "ASIN": _first_nonblank,
                "SKU": _first_nonblank,
                "Product Name": _first_nonblank,
                "Marketplace": _first_nonblank,
                "Buyer Name": _first_nonblank,
                "Buyer Email": _first_nonblank,
            }
        )
        .reset_index()
    )
    return grouped


def _attach_reimbursements(
    analysis_df: pd.DataFrame,
    reimbursement_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    enriched = analysis_df.copy()
    defaults = {
        "Reimbursement Status": "未赔偿",
        "Reimbursement Date": pd.NaT,
        "Reimbursement Amount": pd.NA,
        "Reimbursement Reason": "",
    }
    for column, default in defaults.items():
        if column not in enriched.columns:
            enriched[column] = default
    if reimbursement_df is None or reimbursement_df.empty:
        return enriched

    columns = detect_columns(reimbursement_df, "reimbursements")
    prepared = pd.DataFrame()
    order_id = _optional_text(reimbursement_df, columns.order_id).apply(normalize_order_id)
    if columns.reference_id:
        reference_order_id = _optional_text(reimbursement_df, columns.reference_id).apply(normalize_order_id)
        order_id = order_id.mask(order_id.eq(""), reference_order_id)
    prepared["Order ID"] = order_id
    prepared["Reimbursement Date"] = _optional_date(reimbursement_df, columns.reimbursement_date)
    prepared["Reimbursement Amount"] = _parse_amount_series(_optional_text(reimbursement_df, columns.refund_amount)).abs()
    prepared["Reimbursement Reason"] = _optional_text(reimbursement_df, columns.reason)
    prepared = prepared[prepared["Order ID"] != ""]
    if prepared.empty:
        return enriched
    prepared = (
        prepared.groupby("Order ID", dropna=False)
        .agg(
            {
                "Reimbursement Date": "min",
                "Reimbursement Amount": "sum",
                "Reimbursement Reason": "first",
            }
        )
        .reset_index()
    )
    enriched = enriched.merge(prepared, on="Order ID", how="left", suffixes=("", "_reimbursement"))
    matched = enriched["Reimbursement Date_reimbursement"].notna() if "Reimbursement Date_reimbursement" in enriched.columns else pd.Series(False, index=enriched.index)
    enriched.loc[matched, "Reimbursement Status"] = "已赔偿"
    for column in ["Reimbursement Date", "Reimbursement Amount", "Reimbursement Reason"]:
        new_column = f"{column}_reimbursement"
        if new_column in enriched.columns:
            enriched[column] = enriched[column].where(enriched[new_column].isna(), enriched[new_column])
            enriched = enriched.drop(columns=[new_column])
    return enriched


def _clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _fill_blank_from(primary: pd.Series, fallback: pd.Series) -> pd.Series:
    primary_text = primary.fillna("").astype(str).str.strip()
    fallback_text = fallback.fillna("").astype(str).str.strip()
    missing = primary_text.eq("") | primary_text.str.lower().isin({"nan", "none", "null", "-", "--"})
    return primary.where(~missing, fallback_text)


def _first_nonblank(series: pd.Series):
    text = series.fillna("").astype(str).str.strip()
    text = text[~(text.eq("") | text.str.lower().isin({"nan", "none", "null", "-", "--", "<na>"}))]
    if text.empty:
        return ""
    return text.iloc[0]


def _label_blank_identifier(series: pd.Series, label: str) -> pd.Series:
    text = series.fillna("").astype(str).str.strip()
    missing = text.eq("") | text.str.lower().isin({"nan", "none", "null", "-", "--", "<na>"})
    return text.where(~missing, label)


def _optional_text(df: pd.DataFrame, column: Optional[str]) -> pd.Series:
    if column and column in df.columns:
        return _clean_text(df[column])
    return pd.Series([""] * len(df), index=df.index, dtype="object")


def _optional_date(df: pd.DataFrame, column: Optional[str]) -> pd.Series:
    if column and column in df.columns:
        return parse_date_series(df[column])
    return pd.Series([pd.NaT] * len(df), index=df.index)


def _parse_amount_series(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str).str.strip()
    is_parentheses_negative = text.str.match(r"^\(.*\)$", na=False)
    cleaned = text.str.replace(r"[\$,]", "", regex=True)
    cleaned = cleaned.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    amount = pd.to_numeric(cleaned, errors="coerce")
    amount = amount.where(~is_parentheses_negative, -amount.abs())
    return amount


def _date_series(series: pd.Series) -> pd.Series:
    return parse_date_series(series)


def _status_for_row(row: pd.Series, observation_window_days: int) -> str:
    has_return = pd.notna(row.get("Return Date"))
    if has_return:
        disposition = str(row.get("Disposition / Sellable Status", "")).lower()
        if "sellable" in disposition and "unsellable" not in disposition:
            return "已退货且可售"
        return "已退货但不可售"

    days_since_refund = row.get("Days Since Refund")
    if pd.notna(days_since_refund) and int(days_since_refund) > observation_window_days:
        return f"已退款但超过{observation_window_days}天未退货"
    return "已退款但未找到退货记录"


def _risk_for_status(status: str) -> str:
    if "超过" in status:
        return "Needs Review"
    if status in {"已退款但未找到退货记录", "已退货但不可售"}:
        return "Needs Review"
    return "Low"


def _suggested_action_for_status(status: str) -> str:
    if "超过" in status:
        return "需要人工确认；不要仅凭超期或未匹配流水直接判断未退回"
    if status == "已退款但未找到退货记录":
        return "继续观察，接近窗口期时复核订单"
    if status == "已退货但不可售":
        return "检查退货原因与商品状态，评估 Listing 或质量问题"
    return "无需处理，保留记录"


def _yes_no(value: bool) -> str:
    return "是" if bool(value) else "否"


def _recommendation_focus(row: pd.Series) -> str:
    focus = []
    if row["是否存在产品质量风险"] == "是":
        focus.append("先排查产品质量和不可售退货")
    if row["是否需要补充尺寸图"] == "是":
        focus.append("补充尺寸/规格图")
    if row["是否需要优化主图"] == "是":
        focus.append("检查主图是否传达关键信息")
    if row["是否需要优化五点描述"] == "是":
        focus.append("强化五点描述中的限制与卖点")
    if row["是否需要增加使用场景说明"] == "是":
        focus.append("增加使用场景和适用边界")
    if int(row.get("超过60天未退回订单数", 0)) > 0:
        focus.append("跟进超过60天未退回退款订单")
    if not focus:
        return "持续观察，无明显 Listing 或质量风险"
    return "；".join(focus)


def _risk_sort_key(series: pd.Series) -> pd.Series:
    if series.name == "Risk Level":
        return series.map({"High": 0, "Needs Review": 1, "Data Incomplete": 2, "Medium": 1, "Low": 3}).fillna(4)
    return series
