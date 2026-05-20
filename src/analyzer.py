from __future__ import annotations

import pandas as pd

from src.matcher import ColumnMap, detect_columns, normalize_order_id
from src.parser import parse_date_series


def prepare_ledger(ledger_df: pd.DataFrame) -> pd.DataFrame:
    columns = detect_columns(ledger_df, "inventory_ledger")
    prepared = pd.DataFrame()
    prepared["Reference ID"] = _optional_text(ledger_df, columns.reference_id).apply(normalize_order_id)
    prepared["Ledger Date"] = _optional_date(ledger_df, columns.ledger_date)
    prepared["Ledger Disposition"] = _optional_text(ledger_df, columns.disposition)
    prepared["Ledger Event Type"] = _optional_text(ledger_df, columns.event_type)
    prepared["Ledger FNSKU"] = _optional_text(ledger_df, columns.fnsku)
    prepared["Ledger SKU"] = _optional_text(ledger_df, columns.msku)
    prepared["Ledger Quantity"] = pd.to_numeric(
        _optional_text(ledger_df, columns.quantity), errors="coerce"
    ).fillna(0)
    prepared["Fulfillment Center ID"] = _optional_text(ledger_df, columns.fulfillment_center_id)
    prepared["Ledger Reason"] = _optional_text(ledger_df, columns.reason)
    prepared = prepared[prepared["Ledger Quantity"] != 0]
    return prepared.sort_values("Ledger Date")


def attach_ledger_receipts(analysis_df: pd.DataFrame, ledger_df: pd.DataFrame | None) -> pd.DataFrame:
    enriched = analysis_df.copy()
    for column in [
        "Amazon Warehouse Received",
        "Ledger Date",
        "Ledger Disposition",
        "Ledger Event Type",
        "Ledger FNSKU",
        "Ledger Quantity",
        "Fulfillment Center ID",
        "Ledger Reason",
        "Ledger SKU",
        "Ledger Match Method",
        "Matched Ledger",
        "Matching Logs",
        "Unmatched Reason",
    ]:
        if column not in enriched.columns:
            enriched[column] = pd.NA

    if ledger_df is None or ledger_df.empty:
        enriched["Amazon Warehouse Received"] = "未知"
        return enriched

    ledger = prepare_ledger(ledger_df)
    matched_rows = []
    for _, row in enriched.iterrows():
        match, method, logs, unmatched_reason = _find_ledger_match(row, ledger)
        combined = row.to_dict()
        if match is not None:
            combined.update(match.to_dict())
            combined["Amazon Warehouse Received"] = "是"
            combined["Matched Ledger"] = "是"
            combined["Ledger Match Method"] = method
            combined["Matching Logs"] = _append_log(combined.get("Matching Logs"), logs)
            combined["Unmatched Reason"] = _merge_reasons(combined.get("Unmatched Reason"), "")
        else:
            combined["Amazon Warehouse Received"] = "否"
            combined["Matched Ledger"] = "否"
            combined["Ledger Match Method"] = ""
            combined["Matching Logs"] = _append_log(combined.get("Matching Logs"), logs)
            combined["Unmatched Reason"] = _merge_reasons(combined.get("Unmatched Reason"), unmatched_reason)
        matched_rows.append(combined)
    enriched = pd.DataFrame(matched_rows)
    enriched = _apply_ledger_status(enriched)
    return enriched


def build_match_diagnostics(analysis_df: pd.DataFrame) -> pd.DataFrame:
    total = len(analysis_df)
    returns_match = _series_or_default(analysis_df, "Matched Returns", "否")
    ledger_match = _series_or_default(analysis_df, "Matched Ledger", "否")
    matched_returns = int(returns_match.eq("是").sum())
    matched_ledger = int(ledger_match.eq("是").sum())
    unmatched = int((returns_match.ne("是") & ledger_match.ne("是")).sum())
    no_returns = int(returns_match.ne("是").sum())
    no_ledger = int(ledger_match.ne("是").sum())
    return pd.DataFrame(
        [
            {"Metric": "退款订单总数", "Value": total, "Detail": ""},
            {"Metric": "成功匹配到退货报表的数量", "Value": matched_returns, "Detail": ""},
            {"Metric": "成功匹配到库存流水报表的数量", "Value": matched_ledger, "Detail": ""},
            {"Metric": "未匹配订单数量", "Value": unmatched, "Detail": "退货报表和库存流水报表都未匹配"},
            {"Metric": "未匹配退货报表数量", "Value": no_returns, "Detail": "未找到退货创建或退货接收记录"},
            {"Metric": "未匹配库存流水数量", "Value": no_ledger, "Detail": "未找到订单号或 SKU/FNSKU 附近库存流动"},
        ]
    )


def build_order_audit_table(analysis_df: pd.DataFrame) -> pd.DataFrame:
    columns = {
        "Order ID": "Order ID",
        "SKU": "SKU",
        "ASIN": "ASIN",
        "Product Name": "产品名称",
        "Refund Date": "退款日期",
        "Refund Amount": "退款金额",
        "Return Date": "退货日期",
        "Ledger Date": "入仓记录日期",
        "Return Reason": "退货原因",
        "Ledger Disposition": "商品状态",
        "Return Quantity": "退货数量",
        "Ledger Quantity": "入仓数量",
        "Ledger Event Type": "库存流水事件类型",
        "Reference ID": "参考编号",
        "Fulfillment Center ID": "FBA 仓库",
        "Matched Returns": "是否找到退货记录",
        "Matched Ledger": "是否找到入仓记录",
        "Reimbursement Status": "赔偿状态",
        "Return Match Method": "匹配方法",
        "Ledger Match Method": "库存流水匹配方法",
        "Matching Logs": "技术匹配日志",
        "Unmatched Reason": "未匹配原因",
        "Days Since Refund": "距离退款天数",
        "Risk Level": "风险等级",
        "Risk Score": "风险分数",
        "Risk Explanation": "风险说明",
        "Risk Factors": "风险原因",
        "Risk Reasons": "风险原因",
        "Suggested Action": "建议动作",
    }
    audit = pd.DataFrame()
    for source, target in columns.items():
        audit[target] = analysis_df[source] if source in analysis_df.columns else ""
    if "商品状态" in audit.columns and "Disposition / Sellable Status" in analysis_df.columns:
        audit["商品状态"] = audit["商品状态"].replace("", pd.NA).fillna(analysis_df["Disposition / Sellable Status"])
    if "风险等级" in audit.columns:
        audit["风险等级"] = audit["风险等级"].map(
            {"High": "高风险", "Medium": "中风险", "Low": "低风险"}
        ).fillna(audit["风险等级"])
    return audit


def build_order_lifecycle(order: pd.Series, overdue_days: int = 60) -> list[dict[str, str]]:
    order_created = _value_or_empty(order.get("Order Date"))
    refund_date = _value_or_empty(order.get("Refund Date"))
    return_date = _value_or_empty(order.get("Return Date"))
    ledger_date = _value_or_empty(order.get("Ledger Date"))
    disposition = _value_or_empty(order.get("Ledger Disposition")) or _value_or_empty(order.get("Disposition / Sellable Status"))
    reimbursement_status = _value_or_empty(order.get("Reimbursement Status"))
    days_since_refund = pd.to_numeric(pd.Series([order.get("Days Since Refund")]), errors="coerce").iloc[0]

    is_returned = bool(return_date)
    is_received = str(order.get("Amazon Warehouse Received", "")).strip() == "是"
    is_sellable = "sellable" in disposition.lower() and "unsellable" not in disposition.lower()
    is_unsellable = bool(disposition) and not is_sellable
    is_overdue = pd.notna(days_since_refund) and int(days_since_refund) > overdue_days

    return [
        {"label": "Order Created", "status": "done" if order_created else "missing", "detail": order_created or "当前报表未提供订单创建时间"},
        {"label": "Refunded", "status": "done" if refund_date else "missing", "detail": str(refund_date)},
        {"label": "Return Requested", "status": "done" if is_returned else "missing", "detail": str(return_date or "未找到 Returns 记录")},
        {"label": "Return Received", "status": "done" if is_returned or is_received else "missing", "detail": str(return_date or ledger_date or "未找到 return received")},
        {"label": "Inventory Ledger Event", "status": "done" if is_received else "missing", "detail": str(ledger_date or "库存流水未匹配")},
        {"label": "Sellable / Unsellable", "status": "done" if disposition else "missing", "detail": disposition or "未知"},
        {"label": "Reimbursement", "status": "done" if reimbursement_status == "已赔偿" else "missing", "detail": reimbursement_status or "未赔偿"},
    ]


def _apply_ledger_status(df: pd.DataFrame) -> pd.DataFrame:
    received = df["Amazon Warehouse Received"].eq("是")
    sellable = df["Ledger Disposition"].astype(str).str.lower().str.contains("sellable", na=False)
    unsellable = df["Ledger Disposition"].astype(str).str.lower().str.contains("unsellable|damaged|defective", regex=True, na=False)

    df.loc[received & sellable & ~unsellable, "Status"] = "亚马逊已收货且可售"
    df.loc[received & (unsellable | ~sellable), "Status"] = "亚马逊已收货但不可售"
    df.loc[received & sellable & ~unsellable, "Risk Level"] = "Low"
    df.loc[received & (unsellable | ~sellable), "Risk Level"] = "Medium"
    df.loc[received & sellable & ~unsellable, "Suggested Action"] = "库存流水显示已入仓可售，无需赔偿核查"
    df.loc[received & (unsellable | ~sellable), "Suggested Action"] = "库存流水显示已入仓但不可售，检查退货原因和商品状态"
    return df


def _find_ledger_match(row: pd.Series, ledger: pd.DataFrame) -> tuple[pd.Series | None, str, str, str]:
    logs = []
    order_id = normalize_order_id(row.get("Order ID"))
    if order_id and "Reference ID" in ledger.columns:
        direct = ledger[ledger["Reference ID"].astype(str).str.strip().eq(order_id)]
        logs.append("Attempt 1: order_id == reference-id -> " + ("Matched" if not direct.empty else "Failed"))
        if not direct.empty:
            return direct.iloc[-1], "order_id exact match", "\n".join(logs), ""
    else:
        logs.append("Attempt 1: order_id == reference-id -> Failed (missing order_id or reference-id)")

    refund_date = row.get("Refund Date")
    sku_values = {
        str(row.get("SKU") or "").strip(),
        str(row.get("FNSKU") or "").strip(),
    }
    sku_values.discard("")
    if not sku_values or pd.isna(refund_date):
        logs.append("Attempt 2: sku + refund_date +/-30 days -> Failed (missing SKU or refund date)")
        logs.append("Attempt 3: fnsku + quantity -> Failed (missing FNSKU/quantity context)")
        return None, "", "\n".join(logs), "Missing SKU/refund date for Ledger fallback match"

    candidates = ledger[
        ledger["Ledger SKU"].isin(sku_values) | ledger["Ledger FNSKU"].isin(sku_values)
    ].copy()
    if candidates.empty:
        logs.append("Attempt 2: sku + refund_date +/-30 days -> Failed (SKU/FNSKU mismatch)")
        logs.append("Attempt 3: fnsku + quantity -> Failed (SKU/FNSKU mismatch)")
        return None, "", "\n".join(logs), "SKU mismatch or Ledger has no matching FNSKU/MSKU"
    candidates["Date Distance"] = (
        pd.to_datetime(candidates["Ledger Date"], errors="coerce")
        - pd.to_datetime(refund_date, errors="coerce")
    ).abs().dt.days
    candidates = candidates[candidates["Date Distance"].le(30)]
    if candidates.empty:
        logs.append("Attempt 2: sku + refund_date +/-30 days -> Failed (date window too large)")
        logs.append("Attempt 3: fnsku + quantity -> Failed (date window too large)")
        return None, "", "\n".join(logs), "Date window too large"
    logs.append("Attempt 2: sku + refund_date +/-30 days -> Matched")
    logs.append("Attempt 3: fnsku + quantity -> Not needed")
    return candidates.sort_values("Date Distance").iloc[0], "sku + date window match", "\n".join(logs), ""


def _optional_text(df: pd.DataFrame, column: str | None) -> pd.Series:
    if column and column in df.columns:
        return df[column].fillna("").astype(str).str.strip()
    return pd.Series([""] * len(df), index=df.index, dtype="object")


def _optional_date(df: pd.DataFrame, column: str | None) -> pd.Series:
    if column and column in df.columns:
        return parse_date_series(df[column])
    return pd.Series([pd.NaT] * len(df), index=df.index)


def _value_or_empty(value) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _series_or_default(df: pd.DataFrame, column: str, default: str) -> pd.Series:
    if column in df.columns:
        return df[column].fillna(default).astype(str)
    return pd.Series([default] * len(df), index=df.index)


def _append_log(existing, new_log: str) -> str:
    existing_text = "" if pd.isna(existing) else str(existing).strip()
    if existing_text and new_log:
        return existing_text + "\n" + new_log
    return existing_text or new_log


def _merge_reasons(existing, new_reason: str) -> str:
    existing_text = "" if pd.isna(existing) else str(existing).strip()
    if existing_text and new_reason:
        return existing_text + "; " + new_reason
    return existing_text or new_reason
