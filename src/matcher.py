from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional

import pandas as pd


@dataclass(frozen=True)
class ColumnMap:
    order_id: Optional[str] = None
    asin: Optional[str] = None
    sku: Optional[str] = None
    product_name: Optional[str] = None
    return_date: Optional[str] = None
    return_quantity: Optional[str] = None
    return_reason: Optional[str] = None
    disposition: Optional[str] = None
    refund_date: Optional[str] = None
    refund_amount: Optional[str] = None
    transaction_type: Optional[str] = None
    description: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    marketplace: Optional[str] = None
    ledger_date: Optional[str] = None
    event_type: Optional[str] = None
    reference_id: Optional[str] = None
    fnsku: Optional[str] = None
    msku: Optional[str] = None
    quantity: Optional[str] = None
    fulfillment_center_id: Optional[str] = None
    reason: Optional[str] = None
    reimbursement_date: Optional[str] = None


FIELD_ALIASES = {
    "order_id": [
        "amazonorderid",
        "amazonorder",
        "orderid",
        "ordernumber",
        "merchantorderid",
        "amazonorderidorderid",
        "订单编号",
        "订单号",
        "亚马逊订单编号",
        "亚马逊订单号",
    ],
    "asin": ["asin"],
    "sku": ["sku", "merchantsku", "sellersku", "msku", "merchantstockkeepingunit", "卖家sku", "商品sku"],
    "product_name": ["productname", "title", "producttitle", "itemname", "itemtitle", "商品详情", "商品名称", "商品名", "标题"],
    "return_date": ["returndate", "returnrequestdate", "returnreceiveddate", "date", "退货日期", "退货时间"],
    "return_quantity": ["returnquantity", "quantity", "qty", "退货数量", "数量"],
    "return_reason": ["returnreason", "reason", "customerreturnreason", "退货原因", "原因"],
    "disposition": [
        "disposition",
        "detaileddisposition",
        "sellablestatus",
        "returnstatus",
        "itemdisposition",
        "condition",
        "状态",
        "库存属性",
        "处置",
    ],
    "refund_date": ["refunddate", "posteddate", "transactiondate", "datetime", "date", "日期时间", "日期", "时间"],
    "refund_amount": [
        "refundamount",
        "amount",
        "total",
        "transactionamount",
        "totalamount",
        "amounttotal",
        "netamount",
        "商品销售额",
        "商品价格",
        "销售额",
        "结算金额",
        "金额",
        "总计",
        "合计",
        "退款金额",
    ],
    "transaction_type": ["transactiontype", "type", "eventtype", "amounttype", "交易类型", "类型"],
    "description": ["description", "details", "memo", "reason", "交易说明", "描述", "说明"],
    "buyer_name": ["buyername", "customername", "recipientname", "purchasername"],
    "buyer_email": ["buyeremail", "customeremail", "purchaseremail", "email"],
    "marketplace": ["marketplace", "marketplaceid", "商城", "站点", "市场"],
    "ledger_date": ["date", "snapshotdate", "eventdate", "日期"],
    "event_type": ["eventtype", "event", "transactiontype", "事件类型", "交易类型"],
    "reference_id": ["referenceid", "reference", "orderid", "amazonorderid", "引用编号", "参考编号"],
    "fnsku": ["fnsku", "fulfillmentnetworksku"],
    "msku": ["msku", "sku", "merchantsku", "sellersku", "卖家sku"],
    "quantity": ["quantity", "qty", "quantitychange", "changeinquantity"],
    "fulfillment_center_id": ["fulfillmentcenterid", "fulfillmentcenter", "fcenterid", "fc", "warehouse", "运营中心", "仓库"],
    "reason": ["reason", "eventreason", "原因"],
    "reimbursement_date": ["approvaldate", "reimbursementdate", "date", "批准日期", "赔偿日期"],
}

REPORT_FIELDS = {
    "returns": [
        "order_id",
        "asin",
        "sku",
        "product_name",
        "return_date",
        "return_quantity",
        "return_reason",
        "disposition",
    ],
    "payments": [
        "order_id",
        "asin",
        "sku",
        "product_name",
        "refund_date",
        "refund_amount",
        "transaction_type",
        "description",
        "buyer_name",
        "buyer_email",
        "marketplace",
    ],
    "inventory_ledger": [
        "ledger_date",
        "disposition",
        "event_type",
        "reference_id",
        "fnsku",
        "msku",
        "quantity",
        "fulfillment_center_id",
        "reason",
    ],
    "reimbursements": [
        "order_id",
        "asin",
        "sku",
        "fnsku",
        "refund_amount",
        "reason",
        "reference_id",
        "reimbursement_date",
    ],
}

REQUIRED_FIELDS = {
    "returns": ["order_id"],
    "payments": ["order_id", "refund_date", "refund_amount"],
    "inventory_ledger": [
        "disposition",
        "event_type",
        "fnsku",
        "quantity",
        "fulfillment_center_id",
        "reason",
    ],
    "reimbursements": [],
}


def normalize_column_name(value: object) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).strip().lower())


def normalize_order_id(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).replace("\u200b", "").replace("\ufeff", "").strip()
    if text.lower() in {"", "-", "--", "nan", "none", "null"}:
        return ""
    match = re.search(r"\d{3}-\d{7}-\d{7}", text)
    if match:
        return match.group(0)
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    if re.search(r"\s|/|：|:", text):
        return ""
    return text


def detect_columns(df: pd.DataFrame, report_type: str) -> ColumnMap:
    if report_type not in REPORT_FIELDS:
        raise ValueError(f"Unsupported report_type: {report_type}")

    normalized = _normalized_columns(df.columns)
    detected = {}
    for field in REPORT_FIELDS[report_type]:
        detected[field] = find_column(normalized, FIELD_ALIASES[field])

    missing = validate_required_columns(ColumnMap(**detected), report_type)
    if missing:
        readable = ", ".join(missing)
        raise ValueError(f"Missing required {report_type} column(s): {readable}")
    return ColumnMap(**detected)


def preview_column_matching(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    detected = detect_columns_lenient(df, report_type)
    rows = []
    for field in REPORT_FIELDS[report_type]:
        matched = getattr(detected, field)
        rows.append(
            {
                "Expected Field": field,
                "Matched Column": matched or "",
                "Status": "OK" if matched else ("Missing Required" if field in REQUIRED_FIELDS[report_type] else "Optional Missing"),
            }
        )
    return pd.DataFrame(rows)


def detect_columns_lenient(df: pd.DataFrame, report_type: str) -> ColumnMap:
    if report_type not in REPORT_FIELDS:
        raise ValueError(f"Unsupported report_type: {report_type}")
    normalized = _normalized_columns(df.columns)
    detected = {
        field: find_column(normalized, FIELD_ALIASES[field])
        for field in REPORT_FIELDS[report_type]
    }
    return ColumnMap(**detected)


def validate_required_columns(columns: ColumnMap, report_type: str) -> list[str]:
    return [
        field
        for field in REQUIRED_FIELDS[report_type]
        if getattr(columns, field, None) is None
    ]


def find_column(normalized_columns: dict[str, str], aliases: Iterable[str]) -> Optional[str]:
    for alias in aliases:
        if alias in normalized_columns:
            return normalized_columns[alias]

    for alias in aliases:
        for normalized, original in normalized_columns.items():
            if len(alias) > 3 and alias in normalized:
                return original

    alias_parts = [alias for alias in aliases if len(alias) > 3]
    for normalized, original in normalized_columns.items():
        if any(normalized in alias for alias in alias_parts):
            return original
    return None


def _normalized_columns(columns: Iterable[object]) -> dict[str, str]:
    normalized = {}
    for column in columns:
        key = normalize_column_name(column)
        if key and key not in normalized:
            normalized[key] = column
    return normalized
