from __future__ import annotations

import pandas as pd


def apply_operation_priorities(orders_df: pd.DataFrame) -> pd.DataFrame:
    result = orders_df.copy()
    if result.empty:
        result["Operation Priority"] = []
        result["Priority Score"] = []
        result["Priority Action"] = []
        result["Priority Reasons"] = []
        return result

    _ensure_priority_columns(result)
    asin_counts = _clean_text(result["ASIN"]).value_counts()
    sku_counts = _clean_text(result["SKU"]).value_counts()
    sku_refund_totals = result.groupby("SKU", dropna=False)["Refund Amount"].sum()
    repeat_reason_counts = (
        result.groupby(["ASIN", "Return Reason Category"], dropna=False)["Order ID"]
        .count()
        if "Return Reason Category" in result.columns
        else pd.Series(dtype=int)
    )

    scores = []
    priorities = []
    actions = []
    reasons = []
    for _, row in result.iterrows():
        score, reason_text = _score_priority(
            row,
            asin_counts=asin_counts,
            sku_counts=sku_counts,
            sku_refund_totals=sku_refund_totals,
            repeat_reason_counts=repeat_reason_counts,
        )
        priority, action = _priority_label(score)
        scores.append(score)
        priorities.append(priority)
        actions.append(action)
        reasons.append(reason_text)

    result["Priority Score"] = scores
    result["Operation Priority"] = priorities
    result["Priority Action"] = actions
    result["Priority Reasons"] = reasons
    return result


def _score_priority(
    row: pd.Series,
    asin_counts: pd.Series,
    sku_counts: pd.Series,
    sku_refund_totals: pd.Series,
    repeat_reason_counts: pd.Series,
) -> tuple[int, str]:
    score = 0
    reasons = []
    refund_amount = _number(row.get("Refund Amount"))
    days = _number(row.get("Days Since Refund"))
    asin = _text(row.get("ASIN"))
    sku = _text(row.get("SKU"))
    risk_level = _text(row.get("Risk Level"))
    buyer_high_risk_count = _number(row.get("Buyer High Risk Count"))
    reason_category = _text(row.get("Return Reason Category"))

    if refund_amount >= 100:
        score += 35
        reasons.append("退款金额较高")
    elif refund_amount >= 50:
        score += 20
        reasons.append("退款金额中等")

    if days >= 90:
        score += 30
        reasons.append("超时天数较长")
    elif days >= 60:
        score += 20
        reasons.append("超过观察窗口")
    elif days >= 30:
        score += 10
        reasons.append("接近或进入观察窗口")

    asin_count = int(asin_counts.get(asin, 0)) if asin else 0
    if asin_count >= 5:
        score += 20
        reasons.append("ASIN高频出现")
    elif asin_count >= 3:
        score += 15
        reasons.append("ASIN重复出现")

    sku_total = float(sku_refund_totals.get(sku, 0)) if sku else 0.0
    sku_count = int(sku_counts.get(sku, 0)) if sku else 0
    if refund_amount >= 100 or sku_total >= 200:
        score += 15
        reasons.append("高价值SKU")
    elif sku_count >= 3:
        score += 8
        reasons.append("SKU重复出现")

    if risk_level == "High":
        score += 25
        reasons.append("真正高风险")
    elif risk_level == "Needs Review":
        score += 10
        reasons.append("需要人工确认")

    if pd.notna(buyer_high_risk_count) and buyer_high_risk_count >= 2:
        score += 15
        reasons.append("买家历史重复问题")

    if asin and reason_category:
        repeat_count = int(repeat_reason_counts.get((asin, reason_category), 0))
        if repeat_count >= 3:
            score += 15
            reasons.append("历史重复问题")

    score = min(score, 100)
    if not reasons:
        reasons.append("暂无明显优先处理信号")
    return int(score), "；".join(dict.fromkeys(reasons))


def _priority_label(score: int) -> tuple[str, str]:
    if score >= 70:
        return "P1", "立即处理"
    if score >= 35:
        return "P2", "今日处理"
    return "P3", "观察即可"


def _ensure_priority_columns(df: pd.DataFrame) -> None:
    defaults = {
        "Order ID": "",
        "ASIN": "",
        "SKU": "",
        "Refund Amount": 0.0,
        "Days Since Refund": 0,
        "Risk Level": "",
        "Buyer High Risk Count": 0,
        "Return Reason Category": "",
    }
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default
    df["Refund Amount"] = pd.to_numeric(df["Refund Amount"], errors="coerce").fillna(0).abs()
    df["Days Since Refund"] = pd.to_numeric(df["Days Since Refund"], errors="coerce").fillna(0)


def _clean_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _number(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(number) else float(number)
