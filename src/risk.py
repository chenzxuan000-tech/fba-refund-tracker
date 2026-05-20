from __future__ import annotations

import pandas as pd


def apply_risk_scoring(
    orders_df: pd.DataFrame,
    overdue_days: int = 60,
    high_amount_threshold: float = 100.0,
) -> pd.DataFrame:
    scored = orders_df.copy()
    _ensure_columns(scored)

    scores = []
    levels = []
    reasons = []
    factors = []
    confidences = []
    confidence_reasons = []
    diagnoses = []
    risk_level_labels = []
    evidence_summaries = []
    actions = []
    for _, row in scored.iterrows():
        (
            score,
            level,
            reason_text,
            factor_text,
            confidence,
            confidence_reason,
            diagnosis,
            evidence_summary,
            action,
        ) = _score_order(row, overdue_days, high_amount_threshold)
        scores.append(score)
        levels.append(level)
        reasons.append(reason_text)
        factors.append(factor_text)
        confidences.append(confidence)
        confidence_reasons.append(confidence_reason)
        diagnoses.append(diagnosis)
        risk_level_labels.append(_risk_level_with_confidence(level, confidence, diagnosis))
        evidence_summaries.append(evidence_summary)
        actions.append(action)

    scored["Risk Score"] = scores
    scored["Risk Level"] = levels
    scored["Risk Reasons"] = reasons
    scored["Risk Explanation"] = reasons
    scored["Risk Factors"] = factors
    scored["Evidence Confidence"] = confidences
    scored["Diagnostic Confidence Reason"] = confidence_reasons
    scored["Risk Diagnosis"] = diagnoses
    scored["Risk Level With Confidence"] = risk_level_labels
    scored["Evidence Summary"] = evidence_summaries
    scored["Suggested Action"] = actions
    scored["Conclusion Type"] = scored.apply(_conclusion_type, axis=1)
    scored["Conclusion Boundary"] = scored.apply(_conclusion_boundary, axis=1)
    scored = _flag_potential_buyer_abuse(scored, overdue_days, high_amount_threshold)
    return scored


def apply_support_confirmation_overrides(
    scored_df: pd.DataFrame,
    confirmations: dict[str, str] | None,
) -> pd.DataFrame:
    """Apply manual Amazon Support findings to reduce false positives.

    The app is local-only, so confirmations are passed from session state instead
    of being persisted to a database.
    """
    if scored_df.empty or not confirmations:
        return scored_df.copy()

    result = scored_df.copy()
    if "Amazon Support Confirmation" not in result.columns:
        result["Amazon Support Confirmation"] = ""
    for order_id, status in confirmations.items():
        mask = result["Order ID"].astype(str).eq(str(order_id))
        if not mask.any():
            continue
        result.loc[mask, "Amazon Support Confirmation"] = status
        if status in {"Amazon已确认退回", "Amazon确认可售"}:
            result.loc[mask, "Risk Level"] = "Low"
            result.loc[mask, "Risk Score"] = 5
            result.loc[mask, "Evidence Confidence"] = "High Confidence"
            result.loc[mask, "Diagnostic Confidence Reason"] = "高可信：Amazon Support 已人工确认退回或可售。"
            result.loc[mask, "Risk Diagnosis"] = "正常/低风险"
            result.loc[mask, "Risk Level With Confidence"] = "正常/低风险（高可信）"
            result.loc[mask, "Evidence Summary"] = "Amazon Support 已确认商品退回或可售。"
            result.loc[mask, "Risk Explanation"] = "Amazon Support 已确认该订单商品已退回或处于可售状态，当前不应作为退款未退货高风险订单处理。"
            result.loc[mask, "Risk Reasons"] = result.loc[mask, "Risk Explanation"]
            result.loc[mask, "Risk Factors"] = "Amazon Support 人工确认退回/可售 (-风险)"
            result.loc[mask, "Suggested Action"] = "无需作为索赔订单处理，保留 Support 核查记录。"
            result.loc[mask, "Conclusion Type"] = "已确认事实"
            result.loc[mask, "Conclusion Boundary"] = "基于 Amazon Support 人工确认，属于已确认事实。"
        elif status == "Amazon确认不可售":
            result.loc[mask, "Risk Level"] = "Needs Review"
            result.loc[mask, "Risk Score"] = 35
            result.loc[mask, "Evidence Confidence"] = "High Confidence"
            result.loc[mask, "Diagnostic Confidence Reason"] = "高可信：Amazon Support 已人工确认退回但不可售。"
            result.loc[mask, "Risk Diagnosis"] = "需要人工确认"
            result.loc[mask, "Risk Level With Confidence"] = "需要人工确认（高可信）"
            result.loc[mask, "Evidence Summary"] = "Amazon Support 已确认商品退回但不可售。"
            result.loc[mask, "Risk Explanation"] = "Amazon Support 已确认商品已退回但处于不可售状态，风险重点应转为产品质量、包装或退货原因核查，而不是退款未退货。"
            result.loc[mask, "Risk Reasons"] = result.loc[mask, "Risk Explanation"]
            result.loc[mask, "Risk Factors"] = "Amazon Support 确认不可售 (+产品/运输风险)"
            result.loc[mask, "Suggested Action"] = "检查退货原因、商品状态和批次质量，必要时优化 Listing 或包装。"
            result.loc[mask, "Conclusion Type"] = "已确认事实"
            result.loc[mask, "Conclusion Boundary"] = "基于 Amazon Support 人工确认，属于已确认事实。"
        elif status == "Amazon拒绝赔偿":
            result.loc[mask, "Risk Level"] = "Low"
            result.loc[mask, "Risk Score"] = 10
            result.loc[mask, "Evidence Confidence"] = "High Confidence"
            result.loc[mask, "Diagnostic Confidence Reason"] = "高可信：Amazon Support 已确认不属于可赔偿订单。"
            result.loc[mask, "Risk Diagnosis"] = "正常/低风险"
            result.loc[mask, "Risk Level With Confidence"] = "正常/低风险（高可信）"
            result.loc[mask, "Evidence Summary"] = "Amazon Support 已确认不属于可赔偿订单。"
            result.loc[mask, "Risk Explanation"] = "Amazon Support 已确认该订单不属于可索赔订单，当前仅保留记录，不再作为高风险索赔线索。"
            result.loc[mask, "Risk Reasons"] = result.loc[mask, "Risk Explanation"]
            result.loc[mask, "Risk Factors"] = "Amazon Support 拒绝赔偿 (-索赔风险)"
            result.loc[mask, "Suggested Action"] = "保留 Case 结果，后续不再重复开 Case。"
            result.loc[mask, "Conclusion Type"] = "已确认事实"
            result.loc[mask, "Conclusion Boundary"] = "基于 Amazon Support 人工确认，属于已确认事实。"
        elif status == "Amazon确认未退回":
            result.loc[mask, "Risk Level"] = "High"
            result.loc[mask, "Risk Score"] = 95
            result.loc[mask, "Evidence Confidence"] = "High Confidence"
            result.loc[mask, "Diagnostic Confidence Reason"] = "高可信：Amazon Support 已人工确认商品未退回。"
            result.loc[mask, "Risk Diagnosis"] = "真正高风险"
            result.loc[mask, "Risk Level With Confidence"] = "真正高风险（高可信）"
            result.loc[mask, "Evidence Summary"] = "Amazon Support 已确认商品未退回。"
            result.loc[mask, "Risk Explanation"] = "Amazon Support 已确认商品未退回，属于明确异常订单，可作为优先处理线索。"
            result.loc[mask, "Risk Reasons"] = result.loc[mask, "Risk Explanation"]
            result.loc[mask, "Risk Factors"] = "Amazon Support 确认未退回 (+高可信)"
            result.loc[mask, "Suggested Action"] = "优先整理订单、退款和 Support 证据，按平台规则开 Case 或继续追踪。"
            result.loc[mask, "Conclusion Type"] = "已确认事实"
            result.loc[mask, "Conclusion Boundary"] = "基于 Amazon Support 人工确认，属于已确认事实。"
    return result


def build_top_risk_orders(
    orders_df: pd.DataFrame,
    sort_by: str = "Risk Score",
    limit: int = 10,
) -> pd.DataFrame:
    if orders_df.empty:
        return orders_df.copy()

    sort_options = {
        "ASIN": (["ASIN", "Risk Score"], [True, False]),
        "SKU": (["SKU", "Risk Score"], [True, False]),
        "Buyer": (["Buyer Key", "Risk Score"], [True, False]),
        "Refund Amount": (["Refund Amount", "Risk Score"], [False, False]),
        "Risk Score": (["Risk Score", "Refund Amount"], [False, False]),
    }
    columns, ascending = sort_options.get(sort_by, sort_options["Risk Score"])
    sortable = orders_df.copy()
    for column in columns:
        if column not in sortable.columns:
            sortable[column] = ""
    return sortable.sort_values(columns, ascending=ascending).head(limit).reset_index(drop=True)


def build_buyer_abuse_summary(scored_df: pd.DataFrame) -> pd.DataFrame:
    if scored_df.empty or "Buyer Key" not in scored_df.columns:
        return pd.DataFrame(
            columns=[
                "Buyer Key",
                "Buyer Email",
                "Buyer Name",
                "High Risk Orders",
                "Total Refund Amount",
                "Potential Return Abuse",
            ]
        )

    flagged = scored_df[scored_df["Potential Return Abuse"].eq("是")].copy()
    if flagged.empty:
        return pd.DataFrame(
            columns=[
                "Buyer Key",
                "Buyer Email",
                "Buyer Name",
                "High Risk Orders",
                "Total Refund Amount",
                "Potential Return Abuse",
            ]
        )

    for column in ["Buyer Email", "Buyer Name", "Refund Amount"]:
        if column not in flagged.columns:
            flagged[column] = "" if column != "Refund Amount" else 0.0
    summary = (
        flagged.groupby("Buyer Key", dropna=False)
        .agg(
            Buyer_Email=("Buyer Email", "first"),
            Buyer_Name=("Buyer Name", "first"),
            High_Risk_Orders=("Order ID", "nunique"),
            Total_Refund_Amount=("Refund Amount", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "Buyer_Email": "Buyer Email",
                "Buyer_Name": "Buyer Name",
                "High_Risk_Orders": "High Risk Orders",
                "Total_Refund_Amount": "Total Refund Amount",
            }
        )
    )
    summary["Potential Return Abuse"] = "是"
    return summary.sort_values(
        ["High Risk Orders", "Total Refund Amount"], ascending=[False, False]
    ).reset_index(drop=True)


def _score_order(row: pd.Series, overdue_days: int, high_amount_threshold: float) -> tuple[int, str, str, str, str, str, str, str, str]:
    reasons = []
    factors = []
    score = 0
    refund_date = row.get("Refund Date")
    days_since_refund = _number(row.get("Days Since Refund"))
    refund_amount = _number(row.get("Refund Amount"))
    warehouse_status = str(row.get("Amazon Warehouse Received", "")).strip()
    received = warehouse_status == "是"
    warehouse_unknown = warehouse_status in {"", "未知"}
    return_created = _has_value(row.get("Return Date"))
    ledger_event = _text(row.get("Ledger Event Type"))
    ledger_date = _text(row.get("Ledger Date"))
    matched_ledger = _text(row.get("Matched Ledger")) == "是"
    disposition = _text(row.get("Ledger Disposition")) or _text(row.get("Disposition / Sellable Status"))
    disposition_lower = disposition.lower()
    sellable_disposition = "sellable" in disposition_lower and "unsellable" not in disposition_lower
    unsellable_disposition = any(keyword in disposition_lower for keyword in ["unsellable", "damaged", "defective"])
    sellable = received and sellable_disposition
    overdue = pd.notna(days_since_refund) and int(days_since_refund) > overdue_days
    high_amount = pd.notna(refund_amount) and refund_amount >= high_amount_threshold
    inventory_evidence = received or matched_ledger or bool(ledger_event) or bool(ledger_date)
    sellable_or_unsellable_evidence = sellable_disposition or unsellable_disposition
    no_return_evidence = not return_created
    no_inventory_evidence = not inventory_evidence
    no_status_evidence = not sellable_or_unsellable_evidence

    if _has_value(refund_date):
        score += 0

    if sellable:
        reasons.append("该订单已发现 FBA 入仓记录，且商品状态为可售，当前退款未退货风险较低。")
        factors.append("仓库已收货且可售 (+0)")
        confidence, confidence_reason = _diagnostic_confidence(
            "High Confidence", row, return_created, inventory_evidence
        )
        return 5, "Low", " ".join(reasons), "; ".join(factors), confidence, confidence_reason, "正常/低风险", "FBA 入仓且可售证据明确。", "无需处理，保留记录。"

    if warehouse_unknown and return_created and sellable_disposition:
        reasons.append("退货报表显示商品已退回且为可售，当前未发现明显异常。")
        factors.append("退货记录显示可售 (+0)")
        confidence, confidence_reason = _diagnostic_confidence(
            "High Confidence", row, return_created, inventory_evidence
        )
        return 10, "Low", " ".join(reasons), "; ".join(factors), confidence, confidence_reason, "正常/低风险", "退货报表提供可售证据。", "无需作为退款未退货风险处理。"

    if warehouse_unknown and return_created and disposition:
        reasons.append(f"退货报表显示商品已退回，但状态为 {disposition}，建议关注是否存在产品质量或运输破损问题。")
        factors.append("退货已创建但商品不可售或状态异常 (+15)")
        confidence, confidence_reason = _diagnostic_confidence(
            "Medium Confidence", row, return_created, inventory_evidence
        )
        return 35, "Needs Review", " ".join(reasons), "; ".join(factors), confidence, confidence_reason, "需要人工确认", "退货报表已有退货状态证据，但需要确认商品状态。", "按产品质量或运输问题复核，不要按退款未退货直接索赔。"

    if overdue:
        score += 25
        factors.append(f"退款超过{overdue_days}天 (+25)")
        reasons.append(
            f"该订单已退款 {int(days_since_refund)} 天，超过观察窗口，建议进入人工复核队列。"
        )

    if return_created and not inventory_evidence:
        score += 20
        factors.append("退货报表有记录但未匹配明确库存流水 (+20)")
        reasons.append("系统已找到退货报表记录，但暂未匹配到明确的 FBA 入仓流水；这只能说明需要确认，不能直接判定商品未退回。")

    if no_return_evidence:
        score += 20
        factors.append("退货报表未匹配到订单 (+20)")
        reasons.append("系统暂未在退货报表中匹配到该订单的退货记录。")

    if no_inventory_evidence:
        score += 10
        factors.append("未匹配明确 FBA 库存流水（辅助证据）(+10)")
        reasons.append("系统暂未匹配到明确的 FBA 入仓或库存流动流水。Inventory Ledger 仅作为辅助证据，不能单独证明商品未退回。")

    if high_amount:
        score += 20
        factors.append(f"退款金额较高（≥ ${high_amount_threshold:.0f}）(+20)")
        reasons.append(
            f"该订单退款金额为 ${refund_amount:.2f}，金额较高，建议优先核查。"
        )

    if received and not sellable:
        score += 15
        factors.append("退货入仓但不可售 (+15)")
        reasons.append(f"FBA 已收到退货，但商品状态为 {disposition or '未知'}，建议检查退货原因和是否可索赔。")

    score = min(int(score), 100)
    data_incomplete = (
        (no_return_evidence or no_inventory_evidence)
        and (
            _text(row.get("Reimbursement Report Available")) != "是"
            or not any(_has_value(row.get(column)) for column in ["SKU", "Ledger SKU", "Ledger FNSKU"])
        )
    )
    if score >= 30:
        level = "Needs Review"
        confidence = "Low Confidence" if no_inventory_evidence and not return_created else "Medium Confidence"
        action = "需要人工确认；不能直接判定商品未退回，也不建议仅凭缺失流水开 Case。"
        diagnosis = "需要人工确认"
    elif data_incomplete:
        level = "Data Incomplete"
        confidence = "Low Confidence"
        action = "数据不完整，建议补充 Returns、Inventory Ledger、赔偿报表或 SKU/FNSKU 后再判断。"
        diagnosis = "数据不完整"
    else:
        level = "Low"
        confidence = "High Confidence" if return_created or inventory_evidence else "Low Confidence"
        action = "继续观察或保留记录。"
        diagnosis = "正常/低风险"
    confidence, confidence_reason = _diagnostic_confidence(
        confidence, row, return_created, inventory_evidence
    )
    evidence_summary = _evidence_summary(return_created, inventory_evidence, sellable_or_unsellable_evidence)
    return score, level, " ".join(reasons) or "未发现明显退款未退货风险。", "; ".join(factors) or "无明显风险因子 (+0)", confidence, confidence_reason, diagnosis, evidence_summary, action


def _flag_potential_buyer_abuse(
    scored: pd.DataFrame,
    overdue_days: int,
    high_amount_threshold: float,
) -> pd.DataFrame:
    scored["Buyer Key"] = scored.apply(_buyer_key, axis=1)
    high_risk_mask = _no_return_evidence_mask(scored) & _no_inventory_evidence_mask(scored) & (
        pd.to_numeric(scored["Days Since Refund"], errors="coerce") > overdue_days
    )
    buyer_stats = (
        scored[high_risk_mask & scored["Buyer Key"].ne("")]
        .groupby("Buyer Key", dropna=False)
        .agg(
            Buyer_High_Risk_Count=("Order ID", "nunique"),
            Buyer_Total_Refund_Amount=("Refund Amount", "sum"),
        )
        .reset_index()
    )
    scored = scored.merge(buyer_stats, on="Buyer Key", how="left")
    scored["Buyer High Risk Count"] = scored["Buyer_High_Risk_Count"].fillna(0).astype(int)
    scored["Buyer Total Refund Amount"] = scored["Buyer_Total_Refund_Amount"].fillna(0.0)
    scored["Potential Return Abuse"] = scored["Buyer High Risk Count"].ge(2).map(
        {True: "是", False: "否"}
    )
    abuse_mask = scored["Potential Return Abuse"].eq("是") & scored["Risk Level"].isin(["Needs Review", "Data Incomplete"])
    scored.loc[abuse_mask, "Risk Score"] = (scored.loc[abuse_mask, "Risk Score"] + 10).clip(upper=100)
    bump_mask = abuse_mask & (
        pd.to_numeric(scored["Refund Amount"], errors="coerce").fillna(0) >= high_amount_threshold
    )
    scored.loc[bump_mask, "Risk Level"] = "Needs Review"
    scored.loc[bump_mask, "Evidence Confidence"] = "Medium Confidence"
    scored.loc[bump_mask, "Risk Diagnosis"] = "需要人工确认"
    scored.loc[bump_mask, "Risk Level With Confidence"] = "需要人工确认（中可信）"
    scored.loc[bump_mask, "Diagnostic Confidence Reason"] = scored.loc[bump_mask, "Diagnostic Confidence Reason"].astype(str) + "；同一买家存在多次退款，异常概率上升。"
    scored.loc[bump_mask, "Risk Factors"] = scored.loc[bump_mask, "Risk Factors"].astype(str) + "; 同一买家存在多次异常退款 (+10)"
    scored.loc[bump_mask, "Risk Explanation"] = scored.loc[bump_mask, "Risk Explanation"].astype(str) + " 同一买家存在多次退款且缺少明确退货证据，因此异常概率上升。"
    scored.loc[bump_mask, "Risk Reasons"] = scored.loc[bump_mask, "Risk Explanation"]
    return scored.drop(columns=["Buyer_High_Risk_Count", "Buyer_Total_Refund_Amount"])


def _buyer_key(row: pd.Series) -> str:
    email = _text(row.get("Buyer Email")).lower()
    if email:
        return email
    return _text(row.get("Buyer Name")).lower()


def _ensure_columns(df: pd.DataFrame) -> None:
    defaults = {
        "Refund Date": pd.NaT,
        "Return Date": pd.NaT,
        "Days Since Refund": pd.NA,
        "Refund Amount": 0.0,
        "Amazon Warehouse Received": "未知",
        "Ledger Event Type": "",
        "Ledger Disposition": "",
        "Disposition / Sellable Status": "",
        "Buyer Name": "",
        "Buyer Email": "",
        "Order ID": "",
        "Matched Returns": "否",
        "Matched Ledger": "否",
        "Ledger Date": pd.NaT,
        "Evidence Confidence": "Low Confidence",
        "Diagnostic Confidence Reason": "",
        "Risk Diagnosis": "",
        "Risk Level With Confidence": "",
        "Evidence Summary": "",
        "Conclusion Type": "",
        "Conclusion Boundary": "",
        "Amazon Support Confirmation": "",
        "Suggested Action": "",
        "Reimbursement Report Available": "否",
        "Reimbursement Status": "",
        "Ledger Match Method": "",
        "SKU": "",
        "Ledger SKU": "",
        "Ledger FNSKU": "",
    }
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default


def _number(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def _has_value(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip() != ""


def _text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _diagnostic_confidence(
    base_confidence: str,
    row: pd.Series,
    return_created: bool,
    inventory_evidence: bool,
) -> tuple[str, str]:
    rank = {"Low Confidence": 1, "Medium Confidence": 2, "High Confidence": 3}.get(
        base_confidence, 1
    )
    limitations = []
    matched_ledger = _text(row.get("Matched Ledger")) == "是"
    ledger_match_method = _text(row.get("Ledger Match Method")).lower()
    ledger_only = inventory_evidence and not return_created
    fuzzy_match = "date window" in ledger_match_method or "时间窗口" in ledger_match_method
    reimbursement_available = _text(row.get("Reimbursement Report Available")) == "是"
    has_sku_or_fnsku = any(
        _has_value(row.get(column))
        for column in ["SKU", "Ledger SKU", "Ledger FNSKU"]
    )

    if ledger_only:
        rank -= 1
        limitations.append("仅依赖库存流水推断，未被退货报表交叉确认")
    elif not return_created:
        rank -= 1
        limitations.append("缺少退货入仓事件")

    if fuzzy_match:
        rank = min(rank, 1)
        limitations.append("仅通过时间窗口模糊匹配")

    if not reimbursement_available:
        rank -= 1
        limitations.append("没有赔偿报表")

    if not has_sku_or_fnsku:
        rank -= 1
        limitations.append("SKU/FNSKU 缺失")

    if not inventory_evidence and not return_created:
        rank = min(rank, 1)
        limitations.append("未发现明确入仓流水，仅根据退款时间和金额推断")

    rank = max(1, min(3, rank))
    confidence = {3: "High Confidence", 2: "Medium Confidence", 1: "Low Confidence"}[rank]
    label = _confidence_label(confidence)
    if limitations:
        unique_limitations = list(dict.fromkeys(limitations))
        return confidence, f"{label}：{'；'.join(unique_limitations)}。"
    if matched_ledger or return_created:
        return confidence, f"{label}：存在可交叉核查的退货或库存证据。"
    return confidence, f"{label}：证据较少，仅作为待核查线索。"


def _risk_level_with_confidence(level: str, confidence: str, diagnosis: str = "") -> str:
    label = diagnosis or _risk_level_label(level)
    return f"{label}（{_confidence_label(confidence)}）"


def _risk_level_label(level: str) -> str:
    return {
        "High": "真正高风险",
        "Needs Review": "需要人工确认",
        "Data Incomplete": "数据不完整",
        "Medium": "需要人工确认",
        "Low": "正常/低风险",
    }.get(level, level or "待确认")


def _confidence_label(confidence: str) -> str:
    return {
        "High Confidence": "高可信",
        "Medium Confidence": "中可信",
        "Low Confidence": "低可信",
    }.get(confidence, confidence or "低可信")


def _evidence_summary(
    return_created: bool,
    inventory_evidence: bool,
    status_evidence: bool,
) -> str:
    evidence = []
    if return_created:
        evidence.append("退货报表有记录")
    if inventory_evidence:
        evidence.append("存在 FBA 库存/入仓相关证据")
    if status_evidence:
        evidence.append("存在可售/不可售状态证据")
    if not evidence:
        return "当前仅能说明系统未匹配到明确证据，不代表商品未退回。"
    return "；".join(evidence)


def _conclusion_type(row: pd.Series) -> str:
    support_confirmation = _text(row.get("Amazon Support Confirmation"))
    if support_confirmation:
        return "已确认事实"
    diagnosis = _text(row.get("Risk Diagnosis"))
    return_created = _has_value(row.get("Return Date")) or _text(row.get("Matched Returns")) == "是"
    inventory_evidence = (
        _text(row.get("Amazon Warehouse Received")) == "是"
        or _text(row.get("Matched Ledger")) == "是"
        or _has_value(row.get("Ledger Event Type"))
        or _has_value(row.get("Ledger Date"))
    )
    if diagnosis == "正常/低风险" and (return_created or inventory_evidence):
        return "已确认事实"
    return "高概率推断"


def _conclusion_boundary(row: pd.Series) -> str:
    conclusion_type = _text(row.get("Conclusion Type")) or _conclusion_type(row)
    if conclusion_type == "已确认事实":
        return "基于已匹配报表记录、库存流水或 Amazon Support 人工确认，可作为当前已确认事实。"
    if conclusion_type == "AI猜测":
        return "仅为 AI 根据现有数据生成的假设，必须经人工或报表证据确认后再执行。"
    return "基于当前报表匹配结果的风险推断；只能说明系统暂未匹配到明确证据，不能表述为商品未退回。"


def _no_return_evidence_mask(df: pd.DataFrame) -> pd.Series:
    return_date = df["Return Date"] if "Return Date" in df.columns else pd.Series(pd.NaT, index=df.index)
    matched_returns = df["Matched Returns"] if "Matched Returns" in df.columns else pd.Series("否", index=df.index)
    return return_date.isna() & matched_returns.astype(str).ne("是")


def _no_inventory_evidence_mask(df: pd.DataFrame) -> pd.Series:
    received = df["Amazon Warehouse Received"] if "Amazon Warehouse Received" in df.columns else pd.Series("未知", index=df.index)
    matched_ledger = df["Matched Ledger"] if "Matched Ledger" in df.columns else pd.Series("否", index=df.index)
    ledger_event = df["Ledger Event Type"] if "Ledger Event Type" in df.columns else pd.Series("", index=df.index)
    ledger_date = df["Ledger Date"] if "Ledger Date" in df.columns else pd.Series(pd.NaT, index=df.index)
    return (
        received.astype(str).ne("是")
        & matched_ledger.astype(str).ne("是")
        & ledger_event.fillna("").astype(str).str.strip().eq("")
        & ledger_date.isna()
    )
