from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analyzer import build_match_diagnostics, build_order_audit_table, build_order_lifecycle
from src.matcher import preview_column_matching, validate_required_columns, detect_columns_lenient
from src.risk import build_buyer_abuse_summary, build_top_risk_orders


REPORT_LABELS = {
    "returns": "FBA 买家退货报表 / 退货报表",
    "payments": "付款交易报表",
    "inventory_ledger": "库存流水报表",
    "reimbursements": "赔偿报表",
}


def render_data_preview(report_frames: dict[str, pd.DataFrame]) -> None:
    st.subheader("数据预览")
    if not report_frames:
        st.info("上传报表后会显示字段识别和前几行数据。")
        return

    for report_type, frame in report_frames.items():
        with st.expander(REPORT_LABELS.get(report_type, report_type), expanded=False):
            if frame is None or frame.empty:
                st.info("暂无数据。")
                continue

            st.caption(f"行数：{len(frame):,}，列数：{len(frame.columns):,}")
            matching = preview_column_matching(frame, report_type)
            columns = detect_columns_lenient(frame, report_type)
            missing = validate_required_columns(columns, report_type)

            if missing:
                st.warning(f"缺少必要字段：{', '.join(missing)}")
            else:
                st.success("必要字段已识别。")

            st.markdown("**字段匹配结果**")
            st.dataframe(matching, hide_index=True, use_container_width=True)
            with st.expander("展开查看原始数据", expanded=False):
                st.dataframe(frame.head(50), hide_index=True, use_container_width=True)


def render_match_diagnostics(analysis_df: pd.DataFrame) -> None:
    if analysis_df.empty:
        st.info("暂无订单数据。")
        return

    with st.expander("查看匹配汇总", expanded=False):
        diagnostics = build_match_diagnostics(analysis_df)
        st.dataframe(diagnostics, hide_index=True, use_container_width=True)

    st.markdown("**订单匹配结果（前 10 条）**")
    risk_orders = analysis_df[analysis_df["Risk Level"].isin(["High", "Needs Review", "Data Incomplete"])].copy()
    if risk_orders.empty:
        risk_orders = analysis_df.copy()
    visible = _localized_match_table(risk_orders)
    default_columns = [
        "订单号",
        "退款日期",
        "退款金额",
        "是否找到退货记录",
        "是否找到入仓记录",
        "是否存在异常",
        "风险等级",
        "诊断可信度",
        "风险原因",
        "建议动作",
    ]
    compact_visible = visible[[column for column in default_columns if column in visible.columns]]
    st.dataframe(
        compact_visible.head(10),
        hide_index=True,
        use_container_width=True,
        column_config={
            "退款金额": st.column_config.NumberColumn("退款金额", format="$%.2f"),
        },
    )
    if len(compact_visible) > 10:
        with st.expander("展开完整匹配结果", expanded=False):
            st.dataframe(
                compact_visible,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "退款金额": st.column_config.NumberColumn("退款金额", format="$%.2f"),
                },
            )

    st.markdown("**查看单个订单详情**")
    order_ids = risk_orders["Order ID"].dropna().astype(str).unique().tolist()
    if not order_ids:
        st.info("暂无可核查订单。")
        return
    selected_order = st.selectbox("选择订单查看匹配详情", order_ids, key="match_debug_order")
    row = analysis_df[analysis_df["Order ID"].astype(str).eq(selected_order)].iloc[0]
    _render_order_debug(row)


def render_order_audit_table(analysis_df: pd.DataFrame) -> None:
    st.subheader("逐订单核查表")
    if analysis_df.empty:
        st.info("暂无订单数据。")
        return

    audit = build_order_audit_table(analysis_df)
    if "风险等级" in audit.columns:
        audit = audit[audit["风险等级"].isin(["真正高风险", "需要人工确认", "数据不完整", "High", "Needs Review", "Data Incomplete"])]
        if audit.empty:
            audit = build_order_audit_table(analysis_df)
    st.dataframe(
        audit,
        hide_index=True,
        use_container_width=True,
        column_config={
            "退款金额": st.column_config.NumberColumn("退款金额", format="$%.2f"),
            "距离退款天数": st.column_config.NumberColumn("距离退款天数", format="%d"),
        },
    )


def render_order_lifecycle(analysis_df: pd.DataFrame) -> None:
    st.subheader("订单生命周期")
    st.caption("这个页面用来按步骤确认：退款后，商品是否完成退货、入仓、可售判断和赔偿核查。")
    if analysis_df.empty:
        st.info("暂无订单数据。")
        return

    order_ids = analysis_df["Order ID"].dropna().astype(str).unique().tolist()
    selected_order = st.selectbox("选择订单号", order_ids)
    row = analysis_df[analysis_df["Order ID"].astype(str).eq(selected_order)].iloc[0]
    lifecycle = build_order_lifecycle(row, overdue_days=60)

    simplified_steps = _simplify_lifecycle(lifecycle)
    cols = st.columns(len(simplified_steps))
    for col, step in zip(cols, simplified_steps):
        with col:
            st.markdown(_step_card(step["label"], step["status"]), unsafe_allow_html=True)


def _render_order_debug(row: pd.Series) -> None:
    with st.expander(f"订单 {row.get('Order ID', '')} 详情", expanded=True):
        col_returns, col_ledger, col_reimbursement = st.columns(3)
        with col_returns:
            st.markdown("**退货记录**")
            _render_fact_list(
                {
                    "匹配状态": _match_label(row.get("Matched Returns")),
                    "退货日期": row.get("Return Date", ""),
                    "退货原因": row.get("Return Reason", ""),
                    "退货数量": row.get("Return Quantity", ""),
                    "匹配方式": row.get("Return Match Method", ""),
                }
            )
        with col_ledger:
            st.markdown("**FBA 入仓记录**")
            _render_fact_list(
                {
                    "匹配状态": _match_label(row.get("Matched Ledger")),
                    "入仓记录日期": row.get("Ledger Date", ""),
                    "事件类型": row.get("Ledger Event Type", ""),
                    "商品状态": row.get("Ledger Disposition", ""),
                    "数量": row.get("Ledger Quantity", ""),
                    "参考编号": row.get("Reference ID", ""),
                    "FBA 仓库": row.get("Fulfillment Center ID", ""),
                    "匹配方式": row.get("Ledger Match Method", ""),
                }
            )
        with col_reimbursement:
            st.markdown("**赔偿状态**")
            _render_fact_list(
                {
                    "状态": row.get("Reimbursement Status", "未赔偿"),
                    "日期": row.get("Reimbursement Date", ""),
                    "金额": row.get("Reimbursement Amount", ""),
                    "原因": row.get("Reimbursement Reason", ""),
                }
            )

        st.markdown("**风险说明**")
        st.write(_operational_text(row.get("Risk Explanation", row.get("Risk Reasons", ""))))
        st.markdown("**高风险原因**")
        st.markdown(_risk_factors_as_bullets(row.get("Risk Factors", "")))
        st.markdown("**订单时间线**")
        lifecycle = _simplify_lifecycle(build_order_lifecycle(row, overdue_days=60))
        cols = st.columns(len(lifecycle))
        for col, step in zip(cols, lifecycle):
            with col:
                st.markdown(_step_card(step["label"], step["status"]), unsafe_allow_html=True)

        with st.expander("技术调试信息", expanded=False):
            st.markdown("**匹配日志**")
            st.code(str(row.get("Matching Logs", "") or "暂无匹配日志。"))
            st.markdown("**未匹配原因**")
            st.write(_operational_text(row.get("Unmatched Reason", "")) or "已匹配，无失败原因。")
            st.markdown("**库存流水匹配方法**")
            st.write(_operational_text(row.get("Ledger Match Method", "")) or "未匹配")
            st.markdown("**原始风险因子**")
            st.write(row.get("Risk Factors", ""))


def _match_label(value) -> str:
    return "已匹配" if str(value).strip() == "是" else "未匹配"


def _render_fact_list(items: dict[str, object]) -> None:
    clean_items = [
        {"项目": key, "结果": _clean_visible_value(_operational_text(value))}
        for key, value in items.items()
    ]
    st.dataframe(pd.DataFrame(clean_items), hide_index=True, use_container_width=True)


def render_top_risk_orders_dashboard(analysis_df: pd.DataFrame) -> None:
    st.subheader("高风险订单排序")
    if analysis_df.empty:
        st.info("暂无订单数据。")
        return

    col_sort, col_limit = st.columns(2)
    with col_sort:
        sort_by = st.selectbox(
            "排序维度",
            ["风险分数", "退款金额", "ASIN", "SKU", "买家"],
            index=0,
        )
    with col_limit:
        limit = st.number_input("显示数量", min_value=5, max_value=100, value=10, step=5)

    sort_key = {
        "风险分数": "Risk Score",
        "退款金额": "Refund Amount",
        "ASIN": "ASIN",
        "SKU": "SKU",
        "买家": "Buyer",
    }.get(sort_by, "Risk Score")
    top_orders = build_top_risk_orders(analysis_df, sort_by=sort_key, limit=int(limit))
    display_top_orders = _localize_order_columns(top_orders)
    st.dataframe(
        display_top_orders,
        hide_index=True,
        use_container_width=True,
        column_config={
            "退款金额": st.column_config.NumberColumn("退款金额", format="$%.2f"),
            "风险分数": st.column_config.NumberColumn("风险分数", format="%d"),
            "买家累计退款金额": st.column_config.NumberColumn(
                "买家累计退款金额", format="$%.2f"
            ),
        },
    )

    st.markdown("**高频异常买家**")
    buyer_summary = build_buyer_abuse_summary(analysis_df)
    if buyer_summary.empty:
        st.info("暂未识别到潜在退货滥用买家。")
    else:
        display_buyer_summary = buyer_summary.rename(
            columns={
                "Buyer Key": "买家标识",
                "Buyer Email": "买家邮箱",
                "Buyer Name": "买家姓名",
                "High Risk Orders": "高风险订单数",
                "Total Refund Amount": "累计退款金额",
                "Potential Return Abuse": "疑似退货滥用",
            }
        )
        st.dataframe(
            display_buyer_summary,
            hide_index=True,
            use_container_width=True,
            column_config={
                "累计退款金额": st.column_config.NumberColumn(
                    "累计退款金额", format="$%.2f"
                ),
            },
        )


def _status_icon(status: str) -> str:
    if status == "done":
        return "[完成]"
    if status == "missing":
        return "[缺失]"
    return "[未知]"


def _localized_match_table(df: pd.DataFrame) -> pd.DataFrame:
    columns = {
        "Order ID": "订单号",
        "Refund Date": "退款日期",
        "Refund Amount": "退款金额",
        "Matched Returns": "是否找到退货记录",
        "Matched Ledger": "是否找到入仓记录",
        "Operation Priority": "优先级",
        "Priority Action": "处理时效",
        "Operation Status": "处理状态",
        "Risk Diagnosis": "风险诊断",
        "Risk Level With Confidence": "风险等级",
        "Evidence Confidence": "诊断可信度",
        "Conclusion Type": "结论类型",
        "Conclusion Boundary": "结论边界",
        "Diagnostic Confidence Reason": "可信度说明",
        "Risk Explanation": "风险原因",
        "Suggested Action": "建议动作",
    }
    visible = pd.DataFrame()
    for source, target in columns.items():
        visible[target] = df[source] if source in df.columns else ""
    if visible["风险等级"].astype(str).str.strip().eq("").all() and "Risk Level" in df.columns:
        visible["风险等级"] = df["Risk Level"].map(
            {
                "High": "真正高风险",
                "Needs Review": "需要人工确认",
                "Data Incomplete": "数据不完整",
                "Medium": "需要人工确认",
                "Low": "正常/低风险",
            }
        ).fillna(df["Risk Level"])
    visible["是否找到退货记录"] = visible["是否找到退货记录"].map(_match_label)
    visible["是否找到入仓记录"] = visible["是否找到入仓记录"].map(_match_label)
    if "诊断可信度" in visible.columns:
        visible["诊断可信度"] = visible["诊断可信度"].map(
            {"High Confidence": "高可信", "Medium Confidence": "中可信", "Low Confidence": "低可信"}
        ).fillna(visible["诊断可信度"])
    visible["是否存在异常"] = visible["风险等级"].astype(str).apply(
        lambda value: "需人工确认" if value in {"真正高风险", "需要人工确认", "数据不完整", "High", "Needs Review", "Data Incomplete"} else "未发现明显异常"
    )
    for column in ["风险原因", "建议动作"]:
        if column in visible.columns:
            visible[column] = visible[column].apply(_operational_text)
    return visible.applymap(_clean_visible_value)


def _localize_order_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "Order ID": "订单号",
        "ASIN": "ASIN",
        "SKU": "SKU",
        "Product Name": "产品名称",
        "Refund Date": "退款日期",
        "Refund Amount": "退款金额",
        "Return Date": "退货日期",
        "Days Since Refund": "距离退款天数",
        "Return Reason": "退货原因",
        "Disposition / Sellable Status": "退货状态",
        "Marketplace": "站点",
        "Buyer Name": "买家姓名",
        "Buyer Email": "买家邮箱",
        "Matched Returns": "是否找到退货记录",
        "Matched Ledger": "是否找到入仓记录",
        "Reimbursement Status": "赔偿状态",
        "Operation Priority": "优先级",
        "Priority Score": "优先级分数",
        "Priority Action": "处理时效",
        "Priority Reasons": "优先级原因",
        "Operation Status": "处理状态",
        "Risk Diagnosis": "风险诊断",
        "Risk Level With Confidence": "风险等级",
        "Risk Score": "风险分数",
        "Evidence Confidence": "诊断可信度",
        "Diagnostic Confidence Reason": "可信度说明",
        "Risk Explanation": "风险说明",
        "Risk Factors": "风险原因",
        "Potential Return Abuse": "疑似退货滥用",
        "Buyer High Risk Count": "买家高风险次数",
        "Buyer Total Refund Amount": "买家累计退款金额",
        "Suggested Action": "建议动作",
    }
    localized = df.copy().rename(columns=rename_map)
    if "风险等级" in localized.columns:
        localized["风险等级"] = localized["风险等级"].map(
            {
                "High": "真正高风险",
                "Needs Review": "需要人工确认",
                "Data Incomplete": "数据不完整",
                "Medium": "需要人工确认",
                "Low": "正常/低风险",
            }
        ).fillna(localized["风险等级"])
    if "诊断可信度" in localized.columns:
        localized["诊断可信度"] = localized["诊断可信度"].map(
            {"High Confidence": "高可信", "Medium Confidence": "中可信", "Low Confidence": "低可信"}
        ).fillna(localized["诊断可信度"])
    return localized


def _risk_factors_as_bullets(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    if not text:
        return "- 暂无明显风险原因"
    bullets = []
    for part in text.split(";"):
        clean = part.strip()
        if not clean:
            continue
        clean = clean.split(" (+", 1)[0].strip()
        clean = _operational_text(clean)
        bullets.append(f"- {clean}")
    return "\n".join(bullets) if bullets else "- 暂无明显风险原因"


def _simplify_lifecycle(lifecycle: list[dict[str, str]]) -> list[dict[str, str]]:
    mapping = {
        "Refunded": "退款",
        "Return Requested": "买家退货",
        "Inventory Ledger Event": "FBA入仓",
        "Sellable / Unsellable": "可售/不可售",
        "Reimbursement": "是否赔偿",
    }
    simplified = []
    for step in lifecycle:
        if step["label"] in mapping:
            status = "完成" if step["status"] == "done" else "缺失"
            if step["label"] in {"Sellable / Unsellable", "Reimbursement"} and step["status"] != "done":
                status = "待确认"
            simplified.append({"label": mapping[step["label"]], "status": status})
    return simplified


def _step_card(label: str, status: str) -> str:
    tone = "ok" if status == "完成" else "danger" if status == "缺失" else "warning"
    return f"""
    <div style="border:1px solid rgba(190,200,212,.42);border-left:6px solid { _tone_color(tone) };border-radius:18px;padding:14px;background:#FFFFFF;min-height:90px;box-shadow:4px 4px 10px rgba(150,164,184,.28),-4px -4px 10px rgba(255,255,255,.88);">
      <div style="font-size:13px;color:#718096;font-weight:600;">{label}</div>
      <div style="font-size:22px;font-weight:700;color:#2D3748;margin-top:10px;">{status}</div>
    </div>
    """


def _tone_color(tone: str) -> str:
    if tone == "ok":
        return "#12b76a"
    if tone == "danger":
        return "#d92d20"
    return "#f79009"


def _operational_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    replacements = {
        "No Returns match": "未找到退货报表记录",
        "No matching inventory movement found": "未发现相关 FBA 仓库库存流水",
        "No Ledger Event": "系统暂未发现仓库入库流水",
        "No ledger event": "系统暂未发现仓库入库流水",
        "Inventory Ledger": "库存流水报表",
        "Ledger": "库存流水",
        "warehouse receiving event": "FBA 仓库入仓记录",
        "warehouse receiving": "FBA 仓库入仓",
        "return received": "退货入仓",
        "order_id exact match": "订单号精确匹配",
        "sku + date window match": "SKU 与退款日期窗口匹配",
        "fnsku + quantity": "FNSKU 与数量匹配",
        "Missing SKU/refund date for 库存流水 fallback match": "缺少 SKU 或退款日期，无法继续按库存流水辅助匹配",
        "SKU mismatch or 库存流水 has no matching FNSKU/MSKU": "SKU / FNSKU 与库存流水不一致，未能确认入仓",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _clean_visible_value(value: object) -> str:
    if pd.isna(value):
        return "待确认"
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat", "<na>"}:
        return "待确认"
    return text
