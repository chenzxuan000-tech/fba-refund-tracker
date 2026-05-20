from __future__ import annotations

import re
from html import escape
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from src.ai_report import (
    PROVIDER_CONFIGS,
    build_report_context,
    generate_ai_report,
    get_provider_config,
)
from src.case_template import build_amazon_case_text, build_case_filename
from src.analysis import (
    analyze_refunds_returns,
    build_excel_export,
    build_reason_category_advice,
    build_return_reason_analysis,
    categorize_return_reason,
    detect_columns,
    read_report_file,
    summarize_return_reasons,
)
from src.dashboard import (
    render_data_preview,
    render_match_diagnostics,
    render_order_audit_table,
    render_order_lifecycle,
    render_top_risk_orders_dashboard,
)
from src.parser import read_report_files
from src.order_workflow import apply_order_statuses
from src.priority import apply_operation_priorities
from src.risk import apply_support_confirmation_overrides
from src.sample_data import load_sample_reports


st.set_page_config(
    page_title="FBA 退货退款追踪工具",
    page_icon="",
    layout="wide",
)


def main() -> None:
    _inject_styles()
    st.title("亚马逊 FBA 退货退款追踪工具")

    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <div class="sidebar-logo">FBA</div>
                <div>
                    <div class="sidebar-title">运营工作台</div>
                    <div class="sidebar-subtitle">退货退款风险分析</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        page = _render_sidebar_nav()
        st.divider()
        filter_state = _render_sidebar_filters()

    returns_file, payment_files, ledger_file, reimbursements_file = _render_uploads()
    has_uploaded_required = bool(returns_file) and bool(payment_files)
    if has_uploaded_required and st.session_state.get("use_sample_data"):
        st.session_state["use_sample_data"] = False
    use_sample_data = bool(st.session_state.get("use_sample_data")) and not has_uploaded_required

    if not use_sample_data and not has_uploaded_required:
        _render_empty_state()
        return

    try:
        if use_sample_data:
            report_frames = load_sample_reports()
            returns_df = report_frames["returns"]
            payments_df = report_frames["payments"]
            ledger_df = report_frames["inventory_ledger"]
            reimbursements_df = report_frames["reimbursements"]
            _render_sample_data_banner()
        else:
            returns_df = read_report_file(returns_file)
            payments_df = read_report_files(payment_files, source_column="Source File")
            ledger_df = read_report_file(ledger_file) if ledger_file else None
            reimbursements_df = read_report_file(reimbursements_file) if reimbursements_file else None
            report_frames = {
                "returns": returns_df,
                "payments": payments_df,
            }
            if ledger_df is not None:
                report_frames["inventory_ledger"] = ledger_df
            if reimbursements_df is not None:
                report_frames["reimbursements"] = reimbursements_df
        return_columns = detect_columns(returns_df, "returns")
        payment_columns = detect_columns(payments_df, "payments")
        analysis_df = analyze_refunds_returns(
            returns_df,
            payments_df,
            observation_window_days=filter_state["observation_window_days"],
            as_of=filter_state["as_of"],
            ledger_df=ledger_df,
            reimbursement_df=reimbursements_df,
        )
        analysis_df = apply_support_confirmation_overrides(
            analysis_df,
            st.session_state.get("support_confirmations", {}),
        )
        analysis_df = apply_order_statuses(
            analysis_df,
            st.session_state.get("order_operation_statuses", {}),
        )
        analysis_df = apply_operation_priorities(analysis_df)
        summary_df = summarize_return_reasons(returns_df)
    except Exception as exc:
        st.error(f"报表解析失败：{exc}")
        st.info("请确认上传的是卖家后台导出的退货报表和付款交易报表。")
        return

    visible_df = _apply_global_filters(analysis_df, filter_state)
    reason_analysis = build_return_reason_analysis(visible_df)
    advanced_mode = filter_state["advanced_mode"]

    if page == "总览":
        _render_metrics(visible_df)
        _render_dashboard(visible_df, summary_df, reason_analysis)
    elif page == "退货原因":
        _render_page_intro("退货原因", "找出高频退货原因，并把它转成产品、Listing 和售后动作。")
        _render_reason_analysis(summary_df, reason_analysis)
        with st.expander("展开 ASIN 优化建议", expanded=False):
            _render_recommendations(reason_analysis["asin_recommendations"])
    elif page == "风险订单":
        _render_page_intro("风险订单", "优先处理值得人工核查的退款订单，避免把匹配失败误当成最终结论。")
        risk_page_df = visible_df
        if filter_state["risk_level"] == "全部" and "Risk Level" in visible_df.columns:
            risk_page_df = visible_df[visible_df["Risk Level"].isin(["High", "Needs Review", "Data Incomplete"])]
        priority_filter = st.selectbox("处理优先级", ["P1", "P2", "P3", "全部"], index=0, key="risk_page_priority_filter")
        if priority_filter != "全部" and "Operation Priority" in risk_page_df.columns:
            risk_page_df = risk_page_df[risk_page_df["Operation Priority"].eq(priority_filter)]
        st.caption("默认只展示 P1 立即处理订单，可切换查看 P2 今日处理或 P3 观察订单。")
        _render_orders_table(risk_page_df)
        if advanced_mode:
            with st.expander("展开高级核查工具", expanded=False):
                render_top_risk_orders_dashboard(risk_page_df)
                render_order_lifecycle(risk_page_df)
                render_order_audit_table(risk_page_df)
    elif page == "匹配诊断":
        _render_page_intro("匹配诊断", "查看系统为每个退款订单找到了哪些退货、入仓和赔偿证据。")
        render_match_diagnostics(visible_df)
        if advanced_mode:
            with st.expander("展开技术详情", expanded=False):
                _render_detected_columns(return_columns, payment_columns)
                render_data_preview(report_frames)
    else:
        _render_page_intro("导出报告", "生成 AI 运营决策报告，并导出当前分析表格。")
        _render_ai_report(visible_df, summary_df, reason_analysis)
        _render_export_download(visible_df, summary_df, reason_analysis, report_frames)


def _render_sidebar_nav() -> str:
    nav_items = {
        "总览": "⌂  总览",
        "风险订单": "⚠  风险订单",
        "匹配诊断": "◎  匹配诊断",
        "退货原因": "↩  退货原因",
        "导出报告": "✦  导出报告",
    }
    selected = st.radio(
        "页面导航",
        options=list(nav_items.keys()),
        format_func=lambda item: nav_items[item],
        label_visibility="collapsed",
        key="main_nav",
    )
    return selected


def _render_sidebar_filters() -> dict[str, object]:
    st.markdown("### 全局筛选")
    observation_window_days = st.selectbox(
        "观察窗口",
        options=[30, 45, 60, 90],
        index=2,
        help="用于判断退款后多久仍需人工确认。系统只提示风险，不直接下最终结论。",
        key="global_observation_window",
    )
    as_of = st.date_input("统计截止日期", value=date.today(), key="global_as_of")
    risk_level = st.selectbox(
        "风险等级",
        options=["全部", "真正高风险", "需要人工确认", "数据不完整", "正常/低风险"],
        index=0,
        key="global_risk_level",
    )
    asin_filter = st.text_input("ASIN筛选", placeholder="输入 ASIN，可留空", key="global_asin_filter")
    sku_filter = st.text_input("SKU筛选", placeholder="输入 SKU，可留空", key="global_sku_filter")
    st.divider()
    advanced_mode = st.toggle(
        "高级模式",
        value=False,
        help="开启后显示字段识别、原始数据、匹配日志和完整核查工具。",
        key="advanced_mode",
    )
    return {
        "observation_window_days": int(observation_window_days),
        "as_of": as_of,
        "risk_level": risk_level,
        "asin_filter": asin_filter.strip(),
        "sku_filter": sku_filter.strip(),
        "advanced_mode": bool(advanced_mode),
    }


def _apply_global_filters(df: pd.DataFrame, filter_state: dict[str, object]) -> pd.DataFrame:
    if df.empty:
        return df
    filtered = df.copy()
    risk_map = {
        "真正高风险": "High",
        "需要人工确认": "Needs Review",
        "数据不完整": "Data Incomplete",
        "正常/低风险": "Low",
    }
    selected_risk = risk_map.get(str(filter_state.get("risk_level", "全部")))
    if selected_risk and "Risk Level" in filtered.columns:
        filtered = filtered[filtered["Risk Level"].astype(str).eq(selected_risk)]

    asin_filter = str(filter_state.get("asin_filter") or "").strip().lower()
    if asin_filter and "ASIN" in filtered.columns:
        filtered = filtered[filtered["ASIN"].fillna("").astype(str).str.lower().str.contains(asin_filter, regex=False)]

    sku_filter = str(filter_state.get("sku_filter") or "").strip().lower()
    if sku_filter and "SKU" in filtered.columns:
        filtered = filtered[filtered["SKU"].fillna("").astype(str).str.lower().str.contains(sku_filter, regex=False)]
    return filtered


def _render_page_intro(title: str, description: str) -> None:
    st.subheader(title)
    st.caption(description)


def _render_export_download(
    analysis_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
    report_frames: dict[str, pd.DataFrame],
) -> None:
    export_bytes = build_excel_export(
        analysis_df,
        summary_df,
        reason_analysis,
        ai_report_markdown=st.session_state.get("ai_report_markdown"),
        raw_frames=report_frames,
    )
    st.download_button(
        "导出分析表格",
        data=export_bytes,
        file_name=f"fba_return_refund_analysis_{date.today().isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


def _render_uploads():
    uploaded_returns = st.session_state.get("returns_file")
    uploaded_payments = st.session_state.get("payments_file") or []
    uploaded_ledger = st.session_state.get("ledger_file")
    uploaded_reimbursements = st.session_state.get("reimbursements_file")
    has_required_uploads = bool(uploaded_returns) and bool(uploaded_payments)
    _render_upload_status_cards(
        returns_count=1 if uploaded_returns else 0,
        payments_count=len(uploaded_payments),
        ledger_count=1 if uploaded_ledger else 0,
        reimbursements_count=1 if uploaded_reimbursements else 0,
    )

    with st.expander("重新上传 / 查看已上传文件", expanded=not has_required_uploads):
        col_returns, col_payments = st.columns(2)
        with col_returns:
            st.subheader("1. 退货报表")
            returns_file = st.file_uploader(
                "上传 FBA 买家退货报表 / 退货报表",
                type=["csv", "txt", "tsv", "xlsx", "xls"],
                key="returns_file",
                help="必传。用于确认买家是否创建退货、退货日期、退货原因和商品可售状态。",
            )
        with col_payments:
            st.subheader("2. 退款扣款报表")
            payments_file = st.file_uploader(
                "上传付款 / 交易报表（可多选）",
                type=["csv", "txt", "tsv", "xlsx", "xls"],
                key="payments_file",
                accept_multiple_files=True,
                help="必传。Seller Central 的 Payments / Transaction 报表，可多文件上传，用于识别退款扣款订单和退款金额。",
            )
        col_ledger, col_reimbursements = st.columns(2)
        with col_ledger:
            st.subheader("3. 库存流水报表")
            ledger_file = st.file_uploader(
                "上传库存流水报表（推荐，用于判断是否真正入仓）",
                type=["csv", "txt", "tsv", "xlsx", "xls"],
                key="ledger_file",
                help="推荐上传。Inventory Ledger 是辅助证据，用于匹配退货后的库存流动，但不会单独作为高风险结论。",
            )
        with col_reimbursements:
            st.subheader("4. 赔偿报表")
            reimbursements_file = st.file_uploader(
                "上传赔偿报表（备用）",
                type=["csv", "txt", "tsv", "xlsx", "xls"],
                key="reimbursements_file",
                help="可选。用于核对 Amazon 是否已经赔偿，避免重复开 Case。",
            )
    return returns_file, payments_file, ledger_file, reimbursements_file


def _render_upload_status_cards(
    returns_count: int,
    payments_count: int,
    ledger_count: int,
    reimbursements_count: int,
) -> None:
    items = [
        ("退货报表", returns_count, returns_count > 0),
        ("交易报表", payments_count, payments_count > 0),
        ("库存流水", ledger_count, ledger_count > 0),
        ("赔偿报表", reimbursements_count, reimbursements_count > 0),
    ]
    cards = []
    for label, count, is_ready in items:
        tone = "ready" if is_ready else "empty"
        icon = "✓" if is_ready else "待上传"
        cards.append(
            f'<div class="upload-status-card {tone}">'
            f'<span class="upload-status-icon">{icon}</span>'
            f"<span>{escape(label)}（{count}）</span>"
            "</div>"
        )
    st.markdown(
        f"<div class='upload-status-grid'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


def _render_empty_state() -> None:
    st.divider()
    _render_first_use_guide()
    st.markdown(
        """
        <div class="empty-state-card">
            <div>
                <div class="empty-state-title">还没有上传报表</div>
                <div class="empty-state-copy">
                    先上传退货报表和付款 / 交易报表。没有真实文件时，可以先用示例数据体验完整流程。
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col_sample, col_hint = st.columns([1, 2])
    with col_sample:
        if st.button("使用示例数据快速体验", type="primary", use_container_width=True):
            st.session_state["use_sample_data"] = True
            st.rerun()
    with col_hint:
        st.caption("示例数据会演示：已退回、待人工确认、库存流水辅助证据、赔偿核对和风险处理动作。")


def _render_sample_data_banner() -> None:
    st.markdown(
        """
        <div class="sample-data-banner">
            <div>
                <strong>当前正在使用示例数据</strong>
                <span>用于快速熟悉上传、风险判断、匹配诊断和处理订单流程。</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("退出示例数据，上传真实报表", use_container_width=False):
        st.session_state["use_sample_data"] = False
        st.rerun()


def _render_first_use_guide() -> None:
    guide_items = [
        (
            "1",
            "上传什么文件",
            "必传退货报表和付款 / 交易报表；推荐上传库存流水；赔偿报表可选。",
            "退货报表回答“买家有没有退货”，交易报表回答“哪些订单已退款”。",
        ),
        (
            "2",
            "文件从哪里下载",
            "在亚马逊卖家后台的报表菜单中下载：退货报表、付款/交易报表、库存流水报表和赔偿报表。",
            "不同站点的字段名可能不同，系统会尽量自动识别。",
        ),
        (
            "3",
            "怎么看风险",
            "先看 P1 / P2 / P3，再看风险等级和诊断可信度。",
            "系统是风险提示，不把“未匹配”直接当作“未退回”。",
        ),
        (
            "4",
            "如何处理订单",
            "进入风险订单页，点“处理订单”，复制 Case 文案或标记处理状态。",
            "处理后可标记为已开 Case、待观察、已确认正常或忽略。",
        ),
    ]
    cards = []
    for step, title, body, tooltip in guide_items:
        cards.append(
            "<div class='first-use-card' "
            f"title='{escape(tooltip)}'>"
            f"<div class='first-use-step'>{escape(step)}</div>"
            f"<div class='first-use-title'>{escape(title)}</div>"
            f"<div class='first-use-body'>{escape(body)}</div>"
            "</div>"
        )
    st.markdown(
        "<div class='first-use-header'>首次使用引导</div>"
        f"<div class='first-use-grid'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


def _render_detected_columns(return_columns, payment_columns) -> None:
    col_returns, col_payments = st.columns(2)
    with col_returns:
        st.markdown("**退货报表**")
        st.json(_column_map_to_dict(return_columns))
    with col_payments:
        st.markdown("**退款报表**")
        st.json(_column_map_to_dict(payment_columns))


def _render_metrics(df: pd.DataFrame) -> None:
    total_refunds = len(df)
    matched_returns = _series_or_default(df, "Matched Returns", "否")
    matched_ledger = _series_or_default(df, "Matched Ledger", "否")
    support_confirmation = _series_or_default(df, "Amazon Support Confirmation", "")
    support_returned = support_confirmation.isin(
        ["Amazon已确认退回", "Amazon确认可售", "Amazon确认不可售"]
    )
    confirmed_returned = int(
        (
            matched_returns.eq("是")
            | matched_ledger.eq("是")
            | support_returned
        ).sum()
    )
    unconfirmed = total_refunds - confirmed_returned
    overdue_unconfirmed = int(
        (
            matched_returns.ne("是")
            & matched_ledger.ne("是")
            & ~support_returned
            & (pd.to_numeric(df["Days Since Refund"], errors="coerce") > 60)
        ).sum()
    )
    suspicious_refund_amount = float(
        df.loc[df["Risk Level"].isin(["High", "Needs Review"]), "Refund Amount"].fillna(0).sum()
    )

    metrics = [
        ("退款订单数", f"{total_refunds:,}", "当前报表内识别到的退款订单", "neutral", "01"),
        ("已确认退回", f"{confirmed_returned:,}", "已匹配退货或 FBA 入仓记录", "ok", "02"),
        ("未确认退回", f"{unconfirmed:,}", "暂未确认商品回到 FBA", "warning", "03"),
        ("超60天待确认", f"{overdue_unconfirmed:,}", "不是最终判定，建议人工核查", "danger", "04"),
        ("待核查退款金额", f"${suspicious_refund_amount:,.2f}", "需要人工确认订单对应退款金额", "danger", "05"),
    ]
    cols = st.columns(5)
    for col, (label, value, help_text, tone, icon) in zip(cols, metrics):
        with col:
            st.markdown(
                f"""
                <div class="metric-card {tone}">
                    <div class="metric-top">
                        <span class="metric-icon">{icon}</span>
                        <span class="metric-label">{label}</span>
                    </div>
                    <div class="metric-value">{value}</div>
                    <div class="metric-help">{help_text}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_dashboard(
    analysis_df: pd.DataFrame,
    reason_summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
) -> None:
    _render_page_intro("总览", "快速判断当前筛选范围内，今天应该先处理哪些退款风险。")
    asin_summary = reason_analysis["asin_summary"]
    risk_orders = analysis_df[analysis_df["Risk Level"].isin(["High", "Needs Review", "Data Incomplete"])].copy()
    p1_count = int(analysis_df.get("Operation Priority", pd.Series(dtype=str)).eq("P1").sum())
    high_risk_count = int(analysis_df["Risk Level"].eq("High").sum())
    review_count = int(analysis_df["Risk Level"].isin(["High", "Needs Review"]).sum())
    suspicious_amount = float(analysis_df.loc[analysis_df["Risk Level"].isin(["High", "Needs Review"]), "Refund Amount"].fillna(0).sum())
    st.markdown('<div class="dashboard-section-heading">今日处理概览</div>', unsafe_allow_html=True)
    col_a, col_b = st.columns(2, gap="large")
    col_a.markdown(_mini_card("P1 立即处理", f"{p1_count:,}", f"待确认 {review_count:,} 单，真正高风险 {high_risk_count:,} 单", "danger"), unsafe_allow_html=True)
    col_b.markdown(_mini_card("待核查退款金额", f"${suspicious_amount:,.2f}", "需要人工确认订单对应退款金额", "warning"), unsafe_allow_html=True)

    st.markdown('<div class="dashboard-section-heading secondary">重点榜单</div>', unsafe_allow_html=True)
    col_orders, col_reasons, col_asins = st.columns(3, gap="large")
    with col_orders:
        st.markdown("**Top 5 待核查订单**")
        sort_columns = [column for column in ["Operation Priority", "Priority Score", "Risk Score", "Refund Amount"] if column in risk_orders.columns]
        if sort_columns:
            top_orders = risk_orders.sort_values(
                sort_columns,
                ascending=[column == "Operation Priority" for column in sort_columns],
            ).head(5)
        else:
            top_orders = risk_orders.sort_values(["Risk Score", "Refund Amount"], ascending=[False, False]).head(5)
        _render_top_orders_list(top_orders)
    with col_reasons:
        st.markdown("**Top 5 高频退货原因**")
        top_reasons = reason_summary_df.head(5).rename(
            columns={"Return Reason": "退货原因", "Return Count": "次数", "Reason Share": "占比"}
        )
        _render_soft_table(top_reasons[[column for column in ["退货原因", "次数", "占比"] if column in top_reasons.columns]])
    with col_asins:
        st.markdown("**Top 5 问题 ASIN**")
        top_asins = asin_summary.sort_values(["未退回订单数", "总退款金额"], ascending=[False, False]).head(5)
        _render_soft_table(
            top_asins[[column for column in ["ASIN", "SKU", "退款订单数", "未退回订单数", "总退款金额"] if column in top_asins.columns]],
            currency_columns={"总退款金额"},
        )


def _series_or_default(df: pd.DataFrame, column: str, default: str) -> pd.Series:
    if column in df.columns:
        return df[column].fillna(default).astype(str)
    return pd.Series([default] * len(df), index=df.index)


def _render_top_orders_list(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("暂无需要优先处理的风险订单。")
        return

    items = []
    for _, row in df.iterrows():
        order_id = _safe_text(row.get("Order ID"))
        amount = pd.to_numeric(pd.Series([row.get("Refund Amount")]), errors="coerce").fillna(0).iloc[0]
        days = pd.to_numeric(pd.Series([row.get("Days Since Refund")]), errors="coerce").iloc[0]
        days_text = "待确认" if pd.isna(days) else f"{int(days)}天"
        risk_level = _safe_text(row.get("Risk Level")) or "Low"
        tone = _risk_card_tone(risk_level)
        level_label = _risk_display_label(row)
        confidence_reason = _safe_text(row.get("Diagnostic Confidence Reason")) or "证据可信度待确认。"
        operation_status = _safe_text(row.get("Operation Status")) or "待处理"
        priority_label = _safe_text(row.get("Operation Priority")) or "P3"
        items.append(
            f"<div class='top-order-item {tone}'>"
            f"<div class='top-order-main'>"
            f"<div class='top-order-row'>"
            f"<div class='top-order-id'>{escape(order_id)}</div>"
            f"<div class='top-order-amount'>${float(amount):,.2f}</div>"
            f"</div>"
            f"<div class='top-order-meta'>{escape(priority_label)} · 退款 {escape(days_text)} · {escape(operation_status)}</div>"
            f"</div>"
            f"<span class='top-order-pill {tone}' title='{escape(confidence_reason)}'>{escape(level_label)}</span>"
            f"</div>"
        )
    st.markdown(f"<div class='top-order-list'>{''.join(items)}</div>", unsafe_allow_html=True)


def _render_orders_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.success("当前筛选条件下没有可疑订单。")
        return

    _render_risk_priority_cards(df)
    with st.expander("查看全部风险订单", expanded=False):
        _render_soft_table(_display_orders(df, compact=False), currency_columns={"退款金额"})
    with st.expander("查看详情字段", expanded=False):
        _render_soft_table(_display_orders(df, compact=False, include_details=True), currency_columns={"退款金额"})

def _render_ai_report(
    analysis_df: pd.DataFrame,
    reason_summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
) -> None:
    st.subheader("AI 运营分析报告")
    st.caption("API Key 仅用于本次请求，不会写入本地文件。报告基于当前上传报表和分析结果生成。")

    provider_labels = {
        config.label: name for name, config in PROVIDER_CONFIGS.items()
    }
    col_provider, col_model = st.columns(2)
    with col_provider:
        provider_options = list(provider_labels.keys())
        default_provider_index = provider_options.index("DeepSeek") if "DeepSeek" in provider_options else 0
        selected_label = st.selectbox("AI 服务商", provider_options, index=default_provider_index)
    provider_key = provider_labels[selected_label]
    provider_config = get_provider_config(provider_key)

    with col_model:
        model = st.text_input(
            "模型",
            value=provider_config.default_model,
            key=f"ai_model_{provider_config.name}",
        )

    api_key = st.text_input(
        f"{provider_config.label} API Key",
        type="password",
        placeholder="sk-...",
    )

    with st.expander("高级设置"):
        endpoint = st.text_input(
            "接口地址",
            value=provider_config.endpoint,
            help="默认使用兼容 Chat Completions 的接口。如使用代理或自建网关，可在这里替换。",
        )

    if st.button("生成 AI 分析报告", type="primary", use_container_width=True):
        if not api_key.strip():
            st.warning("请先输入 API Key。")
            return
        context = build_report_context(analysis_df, reason_summary_df, reason_analysis)
        try:
            with st.spinner("AI 正在生成运营分析报告..."):
                report = generate_ai_report(
                    provider_config=provider_config,
                    api_key=api_key,
                    model=model,
                    context=context,
                    endpoint_override=endpoint,
                )
            st.session_state["ai_report_markdown"] = report
        except Exception as exc:
            st.error(f"AI 报告生成失败：{exc}")
            return

    report = st.session_state.get("ai_report_markdown")
    if report:
        _render_ai_decision_center(report, analysis_df, reason_summary_df, reason_analysis)
        st.download_button(
            "下载 AI 报告 Markdown",
            data=report.encode("utf-8"),
            file_name=f"fba_ai_operations_report_{date.today().isoformat()}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        st.info("点击上方按钮后，会生成包含风险总结、重点 ASIN、疑似未退货订单、Listing 和售后建议的中文报告。")


def _render_asin_summary(df: pd.DataFrame) -> None:
    st.subheader("ASIN / SKU 退款退货统计")
    if df.empty:
        st.info("暂无 ASIN / SKU 汇总数据。")
        return

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "总退款金额": st.column_config.NumberColumn("总退款金额", format="$%.2f"),
        },
    )


def _render_reason_analysis(
    reason_summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
) -> None:
    category_summary = reason_analysis["reason_category_summary"]
    distribution = reason_analysis["asin_reason_distribution"]
    advice = build_reason_category_advice()

    col_category, col_reason = st.columns([4, 6])
    with col_category:
        st.markdown("**原因分类占比**")
        if category_summary.empty:
            st.info("暂无原因分类数据。")
        else:
            display_category = category_summary.rename(
                columns={
                    "Reason Category": "原因分类",
                    "Reason Count": "订单数",
                    "Refund Amount": "退款金额",
                    "Reason Share": "占比",
                }
            )
            _render_soft_table(display_category)
            _render_horizontal_bar_chart(
                category_summary,
                label_column="Reason Category",
                value_column="Reason Share",
                value_title="占比",
            )

    with col_reason:
        st.markdown("**Top 10 退货原因明细**")
        if reason_summary_df.empty:
            st.info("退货报表中暂未识别到退货原因。")
        else:
            display_reasons = reason_summary_df.rename(
                columns={
                    "Return Reason": "退货原因",
                    "Reason Category": "原因分类",
                    "Return Count": "次数",
                    "Reason Share": "占比",
                }
            )
            _render_soft_table(display_reasons.head(10))
            if len(display_reasons) > 10:
                with st.expander("查看全部退货原因明细", expanded=False):
                    _render_soft_table(display_reasons)

    st.markdown("**原因分类对应建议**")
    active_categories = set(category_summary.get("Reason Category", pd.Series(dtype=str)).astype(str))
    advice_to_show = advice[advice["原因分类"].isin(active_categories)] if active_categories else advice
    st.dataframe(advice_to_show, hide_index=True, use_container_width=True)

    with st.expander("查看每个 ASIN 的退货原因分布", expanded=False):
        display_distribution = distribution.rename(
            columns={
                "Reason Category": "原因分类",
                "Reason Count": "订单数",
                "Refund Amount": "退款金额",
                "Reason Share": "占比",
            }
        )
        _render_soft_table(display_distribution)


def _render_recommendations(df: pd.DataFrame) -> None:
    st.subheader("ASIN 优化建议")
    if df.empty:
        st.info("暂无优化建议。")
        return

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "总退款金额": st.column_config.NumberColumn("总退款金额", format="$%.2f"),
        },
    )


def _column_map_to_dict(column_map) -> dict[str, str]:
    return {
        key: value
        for key, value in column_map.__dict__.items()
        if value
    }


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #E9EEF5;
            --card: #F7F9FC;
            --card-strong: #FFFFFF;
            --table-bg: #FFFFFF;
            --shadow-dark: rgba(150, 164, 184, 0.22);
            --shadow-light: rgba(255, 255, 255, 0.72);
            --accent: #667eea;
            --danger: #E76F51;
            --warning: #F4A261;
            --success: #52B788;
            --low: #64748B;
            --text: #2D3748;
            --muted: #718096;
            --soft-border: rgba(190, 200, 212, 0.42);
            --soft-shadow: 4px 4px 10px var(--shadow-dark), -4px -4px 10px var(--shadow-light);
            --soft-shadow-hover: 5px 5px 12px rgba(150,164,184,0.26), -5px -5px 12px rgba(255,255,255,0.78);
            --inset-shadow: inset 3px 3px 7px rgba(150,164,184,0.22), inset -3px -3px 7px rgba(255,255,255,0.72);
        }

        html, body, [data-testid="stAppViewContainer"], .stApp {
            background: var(--bg);
            color: var(--text);
        }

        [data-testid="stHeader"] {
            background: rgba(233, 238, 245, 0.82);
            backdrop-filter: blur(12px);
        }

        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"],
        [data-testid="stBaseButton-header"],
        [data-testid="stDeployButton"],
        #MainMenu {
            display: none !important;
            visibility: hidden !important;
        }

        .main .block-container {
            max-width: 1400px;
            padding-top: 1.7rem;
            padding-bottom: 4rem;
            padding-left: 1.5rem;
            padding-right: 1.5rem;
        }

        h1, h2, h3, h4, h5, h6, p, label, span {
            color: var(--text);
        }

        h1, h2, h3 {
            font-weight: 700 !important;
            letter-spacing: 0 !important;
        }

        h1 { font-size: 2.35rem !important; }
        h2 { font-size: 1.65rem !important; }
        h3 { font-size: 1.3rem !important; }

        p, li, label, span {
            font-weight: 400;
        }

        h1 a, h2 a, h3 a, h4 a, h5 a, h6 a,
        .stMarkdown a[href^="#"],
        [data-testid="stMarkdownContainer"] a[href^="#"] {
            display: none !important;
            visibility: hidden !important;
            width: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        div[data-testid="stCaptionContainer"] p,
        [data-testid="stMarkdownContainer"] p {
            color: var(--muted);
            font-weight: 300;
        }

        [data-testid="stSidebar"] {
            background: #EEF3F9;
            border-right: 1px solid rgba(255,255,255,0.56);
        }

        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            gap: 0.55rem;
        }

        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            font-size: 0.95rem;
            letter-spacing: 0.02em;
        }

        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 6px 16px;
        }

        .sidebar-logo {
            width: 42px;
            height: 42px;
            border-radius: 15px;
            background: var(--card-strong);
            color: var(--accent);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            font-weight: 700;
            box-shadow: var(--soft-shadow);
        }

        .sidebar-title {
            font-size: 15px;
            font-weight: 700;
            color: var(--text);
        }

        .sidebar-subtitle {
            color: var(--muted);
            font-size: 12px;
            font-weight: 300;
            margin-top: 2px;
        }

        [data-testid="stSidebar"] [role="radiogroup"] {
            gap: 0.35rem;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 14px;
            padding: 10px 12px;
            margin: 2px 0;
            transition: all 140ms ease;
            border: 1px solid transparent;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: rgba(255,255,255,0.58);
            border-color: rgba(190,200,212,0.36);
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: var(--card-strong);
            color: var(--accent);
            box-shadow: var(--soft-shadow);
            border-color: rgba(102,126,234,0.18);
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {
            color: var(--accent) !important;
            font-weight: 700;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.6rem;
            background: var(--bg);
            padding: 0.35rem;
            border-radius: 18px;
            box-shadow: var(--inset-shadow);
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 14px;
            color: var(--muted);
            height: 42px;
            padding: 0 18px;
            transition: all 160ms ease;
        }

        .stTabs [aria-selected="true"] {
            background: var(--card);
            color: var(--accent);
            box-shadow: 5px 5px 10px var(--shadow-dark), -5px -5px 10px var(--shadow-light);
        }

        div[data-testid="stExpander"] {
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            border-radius: 18px;
            box-shadow: none;
            overflow: hidden;
        }

        div[data-testid="stExpander"] details summary {
            color: var(--text);
            font-weight: 650;
        }

        [data-testid="stFileUploader"] section {
            background: var(--card);
            border: 1px dashed rgba(102, 126, 234, 0.32);
            border-radius: 18px;
            box-shadow: var(--soft-shadow);
            transition: transform 160ms ease, box-shadow 160ms ease;
        }

        [data-testid="stFileUploader"] section:hover {
            transform: translateY(-1px);
            box-shadow: var(--soft-shadow-hover);
        }

        [data-testid="stFileUploaderDropzone"] > div > div {
            font-size: 0 !important;
        }

        [data-testid="stFileUploaderDropzone"] > div > div::before {
            content: "拖拽文件到这里";
            display: block;
            color: var(--text);
            font-size: 16px;
            font-weight: 700;
            line-height: 1.4;
        }

        [data-testid="stFileUploaderDropzone"] > div > div::after {
            content: "";
            display: none;
            color: var(--muted);
            font-size: 13px;
            font-weight: 500;
            margin-top: 6px;
            line-height: 1.4;
        }

        [data-testid="stFileUploaderDropzone"] small,
        [data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderFileSize"],
        [data-testid="stFileUploaderDropzone"] div:has(> small) {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        [data-testid="stFileUploaderDropzone"] button {
            font-size: 0 !important;
        }

        [data-testid="stFileUploaderDropzone"] button::after {
            content: "选择文件";
            font-size: 16px;
            color: var(--text);
        }

        .stSelectbox div[data-baseweb="select"] > div,
        .stTextInput input,
        .stDateInput input,
        .stNumberInput input,
        textarea {
            background: var(--bg) !important;
            border: 0 !important;
            border-radius: 14px !important;
            box-shadow: var(--inset-shadow) !important;
            color: var(--text) !important;
        }

        .stCheckbox [data-testid="stWidgetLabel"] {
            color: var(--text);
        }

        .stButton > button,
        .stDownloadButton > button {
            border: 0;
            border-radius: 16px;
            background: var(--card);
            color: var(--text);
            box-shadow: var(--soft-shadow);
            transition: transform 140ms ease, box-shadow 140ms ease, color 140ms ease;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover {
            transform: translateY(-1px);
            color: var(--accent);
            box-shadow: var(--soft-shadow-hover);
        }

        .stButton > button:active,
        .stDownloadButton > button:active {
            transform: translateY(0);
            box-shadow: var(--inset-shadow);
        }

        .stButton > button[kind="primary"] {
            background: var(--accent);
            color: #fff;
            box-shadow: 4px 4px 10px rgba(102,126,234,0.28), -4px -4px 10px rgba(255,255,255,0.88);
        }

        .stButton > button[kind="primary"] *,
        .stButton > button[kind="primary"]:hover *,
        .stButton > button[kind="primary"]:active * {
            color: #ffffff !important;
        }

        .metric-card {
            background: var(--card);
            border: 1px solid var(--soft-border);
            border-radius: 18px;
            box-shadow: var(--soft-shadow);
            transition: transform 160ms ease, box-shadow 160ms ease;
        }

        .metric-card:hover {
            transform: translateY(-2px);
            box-shadow: var(--soft-shadow-hover);
        }

        .mini-card, .risk-order-card, .ai-report-card {
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            border-radius: 18px;
            box-shadow: none;
            transition: transform 160ms ease, border-color 160ms ease;
        }

        .mini-card:hover, .risk-order-card:hover, .ai-report-card:hover {
            transform: translateY(-1px);
            border-color: rgba(102, 126, 234, 0.24);
        }

        .metric-card {
            min-height: 138px;
            padding: 18px 18px 16px;
            position: relative;
            overflow: hidden;
        }

        .metric-card::after {
            content: "";
            position: absolute;
            inset: auto 16px 14px auto;
            width: 38px;
            height: 38px;
            border-radius: 14px;
            opacity: 0.12;
        }

        .metric-card.danger::after { background: var(--danger); }
        .metric-card.warning::after { background: var(--warning); }
        .metric-card.ok::after { background: var(--success); }
        .metric-card.neutral::after { background: var(--accent); }

        .metric-card.danger {
            background: var(--card-strong);
            border-color: rgba(231, 111, 81, 0.26);
            box-shadow: 4px 4px 12px rgba(231,111,81,0.10), -4px -4px 10px rgba(255,255,255,0.78);
        }

        .metric-top {
            display: flex;
            align-items: center;
            gap: 9px;
            margin-bottom: 12px;
        }

        .metric-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border-radius: 12px;
            background: var(--bg);
            box-shadow: inset 2px 2px 5px var(--shadow-dark), inset -2px -2px 5px var(--shadow-light);
            color: var(--accent);
            font-size: 12px;
            font-weight: 700;
        }

        .metric-card.danger .metric-icon { color: var(--danger); }
        .metric-card.warning .metric-icon { color: var(--warning); }
        .metric-card.ok .metric-icon { color: var(--success); }

        .metric-label, .mini-label {
            color: var(--muted);
            font-size: 13px;
            font-weight: 600;
        }

        .metric-value, .mini-value {
            color: var(--text);
            font-size: 30px;
            font-weight: 700;
            line-height: 1.08;
            letter-spacing: 0;
        }

        .mini-value {
            min-height: 38px;
            max-width: 100%;
            display: flex;
            align-items: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            font-size: clamp(22px, 2.1vw, 30px);
        }

        .mini-value.long {
            font-size: clamp(18px, 1.55vw, 24px);
            line-height: 1.15;
        }

        .metric-card.danger .metric-value {
            color: #B94128;
            font-size: 36px;
        }

        .metric-card.warning .metric-value {
            color: #A85F12;
        }

        .metric-help, .mini-help {
            color: var(--muted);
            font-size: 12px;
            margin-top: 10px;
            line-height: 1.45;
        }

        .dashboard-section-heading {
            color: var(--text);
            font-size: 16px;
            font-weight: 700;
            margin: 0.45rem 0 0.9rem;
        }

        .dashboard-section-heading.secondary {
            margin-top: 2rem;
            padding-top: 0.35rem;
            border-top: 1px solid rgba(148, 163, 184, 0.22);
        }

        .mini-card {
            min-height: 104px;
            padding: 16px 18px;
            border-left: 6px solid rgba(102, 126, 234, 0.55);
            background: var(--card-strong);
        }

        .mini-card.danger { border-left-color: var(--danger); background: #FFF7F3; }
        .mini-card.warning { border-left-color: var(--warning); background: #FFF9ED; }
        .mini-card.ok { border-left-color: var(--success); }

        .risk-task-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            gap: 16px;
            margin: 0.35rem 0 0.75rem;
        }

        .risk-task-title {
            font-size: 18px;
            font-weight: 700;
            color: var(--text);
        }

        .risk-task-subtitle {
            color: var(--muted);
            font-size: 12px;
            font-weight: 300;
            margin-top: 4px;
        }

        .risk-order-card {
            padding: 14px 16px;
            border-left: 6px solid var(--danger);
            background: #FFF7F3;
            margin-bottom: 8px;
            min-height: 154px;
        }

        .risk-order-card.high {
            border-color: var(--danger);
            background: #FFF7F3;
        }

        .risk-order-card.medium {
            border-color: var(--warning);
            background: #FFF9ED;
        }

        .risk-order-card.low {
            border-color: var(--low);
            background: #F8FAFC;
        }

        .risk-order-top {
            display: flex;
            justify-content: space-between;
            gap: 8px;
            align-items: flex-start;
            margin-bottom: 6px;
        }

        .risk-order-id {
            font-size: 14px;
            font-weight: 700;
            color: var(--text);
            line-height: 1.35;
            word-break: break-word;
        }

        .risk-order-meta {
            color: var(--muted);
            font-size: 12px;
            margin-top: 2px;
        }

        .risk-card-meta-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 4px;
        }

        .risk-pill {
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 12px;
            font-weight: 700;
            white-space: nowrap;
            background: rgba(231, 111, 81, 0.14);
            color: #B94128;
        }

        .risk-pill.medium {
            background: rgba(244, 162, 97, 0.18);
            color: #8A4B0E;
        }

        .risk-pill.low {
            background: rgba(148, 163, 184, 0.16);
            color: #475569;
        }

        .risk-tag-stack {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 5px;
        }

        .confidence-pill {
            border-radius: 999px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
            cursor: help;
        }

        .confidence-pill.high {
            background: rgba(82, 183, 136, 0.16);
            color: #2D7D55;
        }

        .confidence-pill.medium {
            background: rgba(244, 162, 97, 0.18);
            color: #8A4B0E;
        }

        .confidence-pill.low {
            background: rgba(231, 111, 81, 0.13);
            color: #B94128;
        }

        .operation-pill {
            border-radius: 999px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
            color: #475569;
            background: rgba(148, 163, 184, 0.16);
        }

        .operation-pill.review,
        .operation-pill.watch {
            background: rgba(244, 162, 97, 0.18);
            color: #8A4B0E;
        }

        .operation-pill.case {
            background: rgba(102, 126, 234, 0.16);
            color: #4F5FC8;
        }

        .operation-pill.done {
            background: rgba(82, 183, 136, 0.16);
            color: #2D7D55;
        }

        .operation-pill.ignore {
            background: rgba(148, 163, 184, 0.18);
            color: #64748B;
        }

        .risk-priority {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            color: var(--muted);
            font-size: 11px;
            font-weight: 600;
            margin-bottom: 4px;
        }

        .risk-amount-row {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 10px;
            margin: 8px 0 6px;
        }

        .risk-amount {
            color: var(--text);
            font-size: 24px;
            font-weight: 700;
            line-height: 1;
        }

        .risk-days {
            color: var(--muted);
            font-size: 12px;
            white-space: nowrap;
        }

        .risk-order-reason {
            margin-top: 6px;
            color: #9F3D28;
            font-size: 13px;
            line-height: 1.38;
            min-height: 36px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .risk-order-action {
            margin-top: 6px;
            color: var(--text);
            font-size: 12px;
            line-height: 1.35;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .top-order-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin-top: 8px;
        }

        .top-order-item {
            display: grid;
            grid-template-columns: minmax(0, 1fr);
            gap: 9px;
            align-items: start;
            padding: 13px 14px;
            border-radius: 16px;
            border: 1px solid rgba(190, 200, 212, 0.42);
            background: #FFFFFF;
            border-left: 5px solid var(--low);
        }

        .top-order-item.high {
            border-left-color: var(--danger);
            background: #FFF7F3;
        }

        .top-order-item.medium {
            border-left-color: var(--warning);
            background: #FFF9ED;
        }

        .top-order-main {
            min-width: 0;
        }

        .top-order-row {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 12px;
            min-width: 0;
        }

        .top-order-id {
            color: var(--text);
            font-size: 13px;
            font-weight: 700;
            line-height: 1.3;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .top-order-meta {
            color: var(--muted);
            font-size: 12px;
            margin-top: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .top-order-amount {
            color: var(--text);
            font-size: 14px;
            font-weight: 700;
            white-space: nowrap;
        }

        .top-order-pill {
            justify-self: start;
            max-width: 100%;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            cursor: help;
            background: rgba(148, 163, 184, 0.16);
            color: #475569;
        }

        .top-order-pill.high {
            background: rgba(231, 111, 81, 0.14);
            color: #B94128;
        }

        .top-order-pill.medium {
            background: rgba(244, 162, 97, 0.18);
            color: #8A4B0E;
        }

        .ai-report-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 18px;
            margin: 18px 0 24px;
        }

        .ai-report-card {
            padding: 18px 20px;
            min-height: 150px;
            background: var(--card-strong);
        }

        .ai-report-card h4 {
            margin: 0 0 10px;
            color: var(--text);
            font-size: 16px;
            font-weight: 700;
        }

        .ai-report-card .report-body {
            color: var(--muted);
            font-size: 13px;
            line-height: 1.65;
            white-space: pre-wrap;
        }

        .ai-report-hero {
            margin: 0.8rem 0 1rem;
            padding: 20px 22px;
            border-radius: 18px;
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            box-shadow: none;
        }

        .ai-report-eyebrow {
            color: var(--accent);
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 8px;
        }

        .ai-report-hero-title {
            color: var(--text);
            font-size: 22px;
            font-weight: 700;
            line-height: 1.25;
        }

        .decision-line {
            display: grid;
            grid-template-columns: 72px 1fr;
            gap: 10px;
            align-items: start;
            margin: 8px 0;
        }

        .decision-line span {
            color: var(--accent);
            font-size: 12px;
            font-weight: 700;
            padding-top: 2px;
        }

        .decision-line p {
            margin: 0;
            color: var(--text);
            font-size: 13px;
            line-height: 1.55;
        }

        .upload-status-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            margin: 0.5rem 0 0.9rem;
        }

        .upload-status-card {
            display: flex;
            align-items: center;
            gap: 10px;
            min-height: 54px;
            padding: 12px 14px;
            border-radius: 16px;
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            color: var(--text);
            font-size: 14px;
            font-weight: 600;
            box-shadow: var(--soft-shadow);
        }

        .upload-status-card.empty {
            background: var(--card);
            color: var(--muted);
            box-shadow: none;
        }

        .upload-status-icon {
            min-width: 30px;
            height: 30px;
            padding: 0 8px;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(34, 197, 94, 0.12);
            color: #15803d;
            font-size: 12px;
            font-weight: 700;
        }

        .upload-status-card.empty .upload-status-icon {
            background: rgba(113, 128, 150, 0.12);
            color: var(--muted);
        }

        .first-use-header {
            color: var(--text);
            font-size: 1.15rem;
            font-weight: 700;
            margin: 0.4rem 0 0.8rem;
        }

        .first-use-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin-bottom: 1rem;
        }

        .first-use-card {
            position: relative;
            min-height: 150px;
            padding: 18px 18px 16px;
            border-radius: 18px;
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            box-shadow: var(--soft-shadow);
            cursor: help;
            transition: transform 140ms ease, box-shadow 140ms ease;
        }

        .first-use-card:hover {
            transform: translateY(-2px);
            box-shadow: var(--soft-shadow-hover);
        }

        .first-use-step {
            width: 30px;
            height: 30px;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            background: var(--accent);
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 12px;
        }

        .first-use-title {
            color: var(--text);
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 8px;
        }

        .first-use-body {
            color: var(--muted);
            font-size: 13px;
            line-height: 1.65;
            font-weight: 400;
        }

        .empty-state-card,
        .sample-data-banner {
            border-radius: 18px;
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            box-shadow: none;
            padding: 18px 20px;
            margin: 0.9rem 0 0.75rem;
        }

        .empty-state-title {
            color: var(--text);
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .empty-state-copy,
        .sample-data-banner span {
            color: var(--muted);
            font-size: 14px;
            line-height: 1.6;
        }

        .sample-data-banner {
            background: #F8FAFF;
            border-color: rgba(102,126,234,0.24);
        }

        .sample-data-banner strong {
            display: block;
            color: var(--accent);
            font-size: 15px;
            margin-bottom: 4px;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 18px;
            border: 1px solid var(--soft-border);
            background: var(--card-strong);
            box-shadow: none;
        }

        [data-testid="stVerticalBlockBorderWrapper"] h4 {
            color: var(--text);
            font-size: 16px;
            margin-bottom: 0.65rem;
        }

        [data-testid="stVerticalBlockBorderWrapper"] p,
        [data-testid="stVerticalBlockBorderWrapper"] li {
            color: var(--text);
            font-size: 14px;
            line-height: 1.68;
        }

        [data-testid="stDataFrame"] {
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid rgba(190, 200, 212, 0.45);
            box-shadow: none;
            background: var(--table-bg);
        }

        [data-testid="stDataFrame"] div {
            font-size: 13px;
        }

        .soft-table-wrap {
            width: 100%;
            overflow-x: auto;
            border-radius: 16px;
            border: 1px solid rgba(190, 200, 212, 0.45);
            box-shadow: none;
            background: var(--table-bg);
            margin: 0.35rem 0 1rem;
        }

        table.soft-table {
            width: auto;
            table-layout: auto;
            border-collapse: collapse;
            background: var(--table-bg);
        }

        table.soft-table th,
        table.soft-table td {
            text-align: center !important;
            vertical-align: middle;
            padding: 12px 12px;
            border-bottom: 1px solid rgba(190, 200, 212, 0.38);
            border-right: 0;
            color: var(--text);
            font-size: 13px;
            line-height: 1.35;
            white-space: nowrap;
        }

        table.soft-table col.col-seq { width: 56px; }
        table.soft-table col.col-order { width: 168px; }
        table.soft-table col.col-count { width: 78px; }
        table.soft-table col.col-percent { width: 72px; }
        table.soft-table col.col-money { width: 128px; }
        table.soft-table col.col-date { width: 118px; }
        table.soft-table col.col-status { width: 108px; }
        table.soft-table col.col-action { width: 180px; }

        table.soft-table th.col-percent,
        table.soft-table td.col-percent {
            width: 84px;
            min-width: 64px;
            max-width: 92px;
        }

        table.soft-table th.col-count,
        table.soft-table td.col-count {
            width: 78px;
            min-width: 64px;
            max-width: 90px;
        }

        table.soft-table th.col-money,
        table.soft-table td.col-money {
            width: 128px;
            min-width: 112px;
            max-width: 150px;
        }

        table.soft-table th.col-text,
        table.soft-table td.col-text {
            text-align: center !important;
        }

        table.soft-table th.col-order,
        table.soft-table td.col-order,
        table.soft-table th.col-action,
        table.soft-table td.col-action {
            white-space: normal;
            word-break: keep-all;
            overflow-wrap: anywhere;
        }

        table.soft-table th {
            position: sticky;
            top: 0;
            z-index: 1;
            color: var(--muted);
            font-weight: 700;
            background: #F8FAFC;
            font-size: 13px;
        }

        table.soft-table tr:hover td {
            background: rgba(102,126,234,0.06);
        }

        table.soft-table tr.risk-high-row td {
            background: #FFF7F3;
        }

        table.soft-table tr.risk-medium-row td {
            background: #FFF9ED;
        }

        table.soft-table td.confidence-high-cell {
            color: #2D7D55;
            font-weight: 700;
            cursor: help;
        }

        table.soft-table td.confidence-medium-cell {
            color: #8A4B0E;
            font-weight: 700;
            cursor: help;
        }

        table.soft-table td.confidence-low-cell {
            color: #B94128;
            font-weight: 700;
            cursor: help;
        }

        table.soft-table td:last-child:not(.col-percent):not(.col-count):not(.col-money):not(.col-date):not(.col-status) {
            white-space: normal;
            min-width: 260px;
        }

        div[data-testid="stAlert"] {
            border-radius: 16px;
            border: 1px solid var(--soft-border);
            box-shadow: none;
        }

        hr {
            border-color: rgba(113, 128, 150, 0.18);
        }

        @media (max-width: 1100px) {
            .upload-status-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .first-use-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 720px) {
            .main .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }
            .upload-status-grid {
                grid-template-columns: 1fr;
            }
            .first-use-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _mini_card(label: str, value: str, help_text: str, tone: str = "ok") -> str:
    value_class = "mini-value long" if len(str(value)) > 14 else "mini-value"
    return f"""
    <div class="mini-card {tone}">
        <div class="mini-label">{escape(str(label))}</div>
        <div class="{value_class}" title="{escape(str(value))}">{escape(str(value))}</div>
        <div class="mini-help">{escape(str(help_text))}</div>
    </div>
    """


def _render_horizontal_bar_chart(
    df: pd.DataFrame,
    label_column: str,
    value_column: str,
    value_title: str,
) -> None:
    if df.empty or label_column not in df.columns or value_column not in df.columns:
        st.info("暂无图表数据。")
        return
    chart_df = df[[label_column, value_column]].copy()
    chart_df[label_column] = chart_df[label_column].fillna("其他").astype(str)
    chart_df[value_column] = pd.to_numeric(chart_df[value_column], errors="coerce").fillna(0)
    chart = (
        alt.Chart(chart_df)
        .mark_bar(cornerRadiusEnd=6, color="#667eea")
        .encode(
            y=alt.Y(
                f"{label_column}:N",
                sort="-x",
                title=None,
                axis=alt.Axis(labelAngle=0, labelLimit=180, labelColor="#718096"),
            ),
            x=alt.X(
                f"{value_column}:Q",
                title=value_title,
                axis=alt.Axis(format=".0%", labelColor="#718096", gridColor="#D6DEE8"),
            ),
            tooltip=[
                alt.Tooltip(f"{label_column}:N", title="原因分类"),
                alt.Tooltip(f"{value_column}:Q", title=value_title, format=".1%"),
            ],
        )
        .properties(height=max(260, min(520, len(chart_df) * 36)))
        .configure_view(strokeWidth=0)
        .configure_axis(domain=False, tickColor="#D6DEE8", titleColor="#718096")
    )
    st.altair_chart(chart, use_container_width=True)


def _render_risk_priority_cards(df: pd.DataFrame) -> None:
    risk_df = df.copy()
    if risk_df.empty:
        return
    sort_columns = [column for column in ["Operation Priority", "Priority Score", "Risk Score", "Refund Amount", "Days Since Refund"] if column in risk_df.columns]
    if sort_columns:
        ascending = [True if column == "Operation Priority" else False for column in sort_columns]
        risk_df = risk_df.sort_values(sort_columns, ascending=ascending)
    top_cards = risk_df.head(3)
    if top_cards.empty:
        return

    st.markdown(
        """
        <div class="risk-task-header">
            <div>
                <div class="risk-task-title">今日优先处理</div>
                <div class="risk-task-subtitle">按风险分数、退款金额和退款天数排序，只展示最需要先处理的订单。</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(len(top_cards))
    for priority, (col, (_, row)) in enumerate(zip(cols, top_cards.iterrows()), start=1):
        with col:
            order_id = _safe_text(row.get("Order ID"))
            amount = pd.to_numeric(pd.Series([row.get("Refund Amount")]), errors="coerce").fillna(0).iloc[0]
            days = pd.to_numeric(pd.Series([row.get("Days Since Refund")]), errors="coerce").iloc[0]
            days_text = "天数待确认" if pd.isna(days) else f"{int(days)} 天"
            action = _shorten_text(_safe_text(row.get("Suggested Action")) or "人工核查订单状态", 20)
            risk_reason = _core_risk_reason(row)
            risk_level = _safe_text(row.get("Risk Level")) or "Low"
            level_label = _risk_display_label(row)
            confidence = _safe_text(row.get("Evidence Confidence")) or "Low Confidence"
            confidence_label = _confidence_label(confidence)
            confidence_reason = _safe_text(row.get("Diagnostic Confidence Reason")) or "证据可信度待确认。"
            operation_status = _safe_text(row.get("Operation Status")) or "待处理"
            operation_tone = _operation_status_tone(operation_status)
            priority_label = _safe_text(row.get("Operation Priority")) or "P3"
            priority_action = _safe_text(row.get("Priority Action")) or "观察即可"
            tone = _risk_card_tone(risk_level)
            st.markdown(
                f'<div class="risk-order-card {tone}">'
                '<div class="risk-card-meta-row">'
                f'<div class="risk-priority">⚑ {escape(priority_label)} · {escape(priority_action)}</div>'
                f'<span class="operation-pill {operation_tone}">状态：{escape(operation_status)}</span>'
                '</div>'
                '<div class="risk-order-top">'
                '<div>'
                f'<div class="risk-order-id">{escape(order_id)}</div>'
                '</div>'
                '<div class="risk-tag-stack">'
                f'<span class="risk-pill {tone}">{escape(level_label)}</span>'
                f'<span class="confidence-pill {confidence.lower().split()[0]}" title="{escape(confidence_reason)}">{escape(confidence_label)}</span>'
                '</div>'
                '</div>'
                '<div class="risk-amount-row">'
                f'<div class="risk-amount">${amount:,.2f}</div>'
                f'<div class="risk-days">退款 {escape(days_text)}</div>'
                '</div>'
                f'<div class="risk-order-reason">{escape(risk_reason)}</div>'
                f'<div class="risk-order-action">建议：{escape(action)}</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            _render_order_action_menu(row, order_id)

    if st.session_state.get("operation_clipboard_text"):
        label = st.session_state.get("operation_clipboard_label", "内容")
        with st.expander(f"已生成可复制{label}", expanded=True):
            st.code(st.session_state["operation_clipboard_text"], language="text")


def _render_order_action_menu(row: pd.Series, order_id: str) -> None:
    label = "处理订单 ▼"
    if hasattr(st, "popover"):
        with st.popover(label, use_container_width=True):
            _render_order_action_buttons(row, order_id)
    else:
        with st.expander(label, expanded=False):
            _render_order_action_buttons(row, order_id)


def _render_order_action_buttons(row: pd.Series, order_id: str) -> None:
    if st.button("复制订单号", key=f"copy_order_{order_id}", use_container_width=True):
        st.session_state["operation_clipboard_text"] = order_id
        st.session_state["operation_clipboard_label"] = "订单号"
    if st.button("复制开 Case 文案", key=f"copy_case_{order_id}", use_container_width=True):
        st.session_state["operation_clipboard_text"] = build_amazon_case_text(row)
        st.session_state["operation_clipboard_label"] = "开 Case 文案"
    case_text = build_amazon_case_text(row)
    st.download_button(
        "下载 Case txt",
        data=case_text,
        file_name=build_case_filename(row),
        mime="text/plain",
        key=f"download_case_{order_id}",
        use_container_width=True,
    )
    st.divider()
    st.caption("运营处理状态")
    if st.button("标记已处理", key=f"handled_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "已核查")
    if st.button("忽略", key=f"ignore_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "已忽略")
    if st.button("加入人工复核", key=f"review_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "人工复核")
    if st.button("待观察", key=f"watch_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "待观察")
    if st.button("已开Case", key=f"case_opened_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "已开Case")
    if st.button("已确认正常", key=f"normal_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "已确认正常")
    if st.button("恢复待处理", key=f"pending_{order_id}", use_container_width=True):
        _set_order_operation_status(order_id, "待处理")
    st.divider()
    st.caption("Amazon Support 核查结果")
    if st.button("Amazon已确认退回", key=f"support_returned_{order_id}", use_container_width=True):
        _set_support_confirmation(order_id, "Amazon已确认退回")
    if st.button("Amazon确认可售", key=f"support_sellable_{order_id}", use_container_width=True):
        _set_support_confirmation(order_id, "Amazon确认可售")
    if st.button("Amazon确认不可售", key=f"support_unsellable_{order_id}", use_container_width=True):
        _set_support_confirmation(order_id, "Amazon确认不可售")
    if st.button("Amazon确认未退回", key=f"support_not_returned_{order_id}", use_container_width=True):
        _set_support_confirmation(order_id, "Amazon确认未退回")
    if st.button("Amazon拒绝赔偿", key=f"support_rejected_{order_id}", use_container_width=True):
        _set_support_confirmation(order_id, "Amazon拒绝赔偿")
    if st.button("清除人工确认", key=f"support_clear_{order_id}", use_container_width=True):
        confirmations = dict(st.session_state.get("support_confirmations", {}))
        confirmations.pop(order_id, None)
        st.session_state["support_confirmations"] = confirmations
        st.rerun()


def _set_order_operation_status(order_id: str, status: str) -> None:
    statuses = dict(st.session_state.get("order_operation_statuses", {}))
    statuses[order_id] = status
    st.session_state["order_operation_statuses"] = statuses
    st.rerun()


def _set_support_confirmation(order_id: str, status: str) -> None:
    confirmations = dict(st.session_state.get("support_confirmations", {}))
    confirmations[order_id] = status
    st.session_state["support_confirmations"] = confirmations
    st.rerun()


def _core_risk_reason(row: pd.Series) -> str:
    days = pd.to_numeric(pd.Series([row.get("Days Since Refund")]), errors="coerce").iloc[0]
    days_part = "" if pd.isna(days) else f"退款{int(days)}天"
    matched_returns = _safe_text(row.get("Matched Returns")) == "是"
    matched_ledger = _safe_text(row.get("Matched Ledger")) == "是"
    disposition = _safe_text(row.get("Ledger Disposition") or row.get("Disposition / Sellable Status"))
    if not matched_returns and not matched_ledger:
        return f"{days_part}，系统暂未匹配到退货或FBA入仓证据".strip("，")
    if matched_returns and not matched_ledger:
        return f"{days_part}，有退货记录，FBA入仓流水待确认".strip("，")
    if matched_ledger and "unsellable" in disposition.lower():
        return f"{days_part}，已入仓但商品不可售".strip("，")
    if matched_ledger:
        return f"{days_part}，已发现FBA入仓记录".strip("，")
    return _shorten_text(_safe_text(row.get("Risk Explanation")) or "需要人工核查退货状态", 32)


def _risk_level_label(level: str) -> str:
    return {
        "High": "真正高风险",
        "Needs Review": "需要人工确认",
        "Data Incomplete": "数据不完整",
        "Medium": "需要人工确认",
        "Low": "正常/低风险",
    }.get(level, level or "待确认")


def _risk_display_label(row: pd.Series) -> str:
    display = _safe_text(row.get("Risk Level With Confidence"))
    if display:
        return display
    return f"{_risk_level_label(_safe_text(row.get('Risk Level')))}（{_confidence_label(_safe_text(row.get('Evidence Confidence')))}）"


def _confidence_label(confidence: str) -> str:
    return {
        "High Confidence": "高可信",
        "Medium Confidence": "中可信",
        "Low Confidence": "低可信",
    }.get(confidence, "低可信")


def _risk_card_tone(level: str) -> str:
    if level == "High":
        return "high"
    if level in {"Needs Review", "Medium"}:
        return "medium"
    return "low"


def _operation_status_tone(status: str) -> str:
    if status in {"人工复核"}:
        return "review"
    if status in {"待观察"}:
        return "watch"
    if status in {"已开Case"}:
        return "case"
    if status in {"已核查", "已确认正常"}:
        return "done"
    if status in {"已忽略"}:
        return "ignore"
    return "pending"


def _add_to_session_set(key: str, value: str) -> None:
    current = set(st.session_state.get(key, set()))
    current.add(value)
    st.session_state[key] = current


def _shorten_text(text: str, max_length: int) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "…"


def _render_ai_decision_center(
    report: str,
    analysis_df: pd.DataFrame,
    reason_summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
) -> None:
    sections = _split_report_sections(report)
    section_map = {title: _clean_ai_markdown(body) for title, body in sections}
    review_orders = analysis_df[analysis_df.get("Risk Level", pd.Series(dtype=str)).isin(["High", "Needs Review"])]
    true_high = analysis_df[analysis_df.get("Risk Level", pd.Series(dtype=str)).eq("High")]
    suspicious_amount = float(review_orders.get("Refund Amount", pd.Series(dtype=float)).fillna(0).sum())
    top_asin = _top_asin_label(reason_analysis.get("asin_summary", pd.DataFrame()))
    top_reason = _top_reason_label(reason_summary_df)

    st.markdown("### 运营决策中心")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.markdown(_mini_card("待确认订单", f"{len(review_orders):,}", f"真正高风险 {len(true_high):,} 单", "danger"), unsafe_allow_html=True)
    col_b.markdown(_mini_card("重点 ASIN", top_asin, "按未退回和退款金额排序", "warning"), unsafe_allow_html=True)
    col_c.markdown(_mini_card("待核查退款金额", f"${suspicious_amount:,.2f}", "待确认订单对应金额", "danger"), unsafe_allow_html=True)
    col_d.markdown(_mini_card("高频退货原因", top_reason, "当前样本最高频原因", "ok"), unsafe_allow_html=True)

    st.markdown("#### 第一屏：风险与关注对象")
    first_cols = st.columns(2)
    with first_cols[0]:
        _render_report_panel("风险总结", _find_report_section(section_map, ["整体", "风险"]))
    with first_cols[1]:
        _render_report_panel("重点 ASIN", _find_report_section(section_map, ["ASIN", "重点"]))

    st.markdown("#### 第二屏：问题来源")
    second_cols = st.columns(3)
    with second_cols[0]:
        _render_report_panel("产品问题", _find_report_section(section_map, ["产品本身", "产品问题", "质量"]))
    with second_cols[1]:
        _render_report_panel("Listing 问题", _find_report_section(section_map, ["Listing", "图片", "标题", "五点", "A+"]))
    with second_cols[2]:
        _render_report_panel("高频退货原因", _find_report_section(section_map, ["主要退货原因", "退货原因"]))

    st.markdown("#### 第三屏：建议动作")
    third_cols = st.columns(3)
    with third_cols[0]:
        _render_report_panel("建议动作", _find_report_section(section_map, ["优化建议", "建议动作"]))
    with third_cols[1]:
        _render_report_panel("开 Case 建议", _find_report_section(section_map, ["开 Case", "核查"]))
    with third_cols[2]:
        _render_report_panel("索赔建议", _find_report_section(section_map, ["赔偿", "索赔", "客服", "售后"]))

    with st.expander("查看完整 AI 报告原文", expanded=False):
        st.markdown(_clean_ai_markdown(report))


def _render_report_panel(title: str, body: str) -> None:
    display_body = _shorten_report_body(body, max_lines=5)
    lines = [
        _strip_inline_markdown(line.lstrip("- ").strip())
        for line in display_body.splitlines()
        if line.strip()
    ]
    fields = {
        "问题": lines[0] if len(lines) >= 1 else "当前数据不足以形成明确判断。",
        "原因": lines[1] if len(lines) >= 2 else "需要结合退货报表、交易报表和人工核查结果继续确认。",
        "影响": lines[2] if len(lines) >= 3 else "影响范围暂不明确。",
        "建议动作": "；".join(lines[3:5]) if len(lines) >= 4 else "先处理高金额、长周期、证据不足的订单。",
    }
    with st.container(border=True):
        st.markdown(f"#### {title}")
        for label, text in fields.items():
            st.markdown(
                f"<div class='decision-line'><span>{escape(label)}</span><p>{escape(text)}</p></div>",
                unsafe_allow_html=True,
            )


def _find_report_section(section_map: dict[str, str], keywords: list[str]) -> str:
    for title, body in section_map.items():
        title_text = title.lower()
        if any(keyword.lower() in title_text for keyword in keywords):
            return body
    for title, body in section_map.items():
        content = f"{title}\n{body}".lower()
        if any(keyword.lower() in content for keyword in keywords):
            return body
    return ""


def _shorten_report_body(body: str, max_lines: int = 6) -> str:
    lines = [line.strip() for line in _clean_ai_markdown(body).splitlines() if line.strip()]
    if not lines:
        return ""
    simplified = []
    for line in lines:
        if line.startswith("#"):
            continue
        simplified.append(_strip_inline_markdown(line))
        if len(simplified) >= max_lines:
            break
    return "\n".join(simplified)


def _top_asin_label(df: pd.DataFrame) -> str:
    if df is None or df.empty or "ASIN" not in df.columns:
        return "暂无"
    sorted_df = df.copy()
    sort_columns = [column for column in ["未退回订单数", "总退款金额", "退款订单数"] if column in sorted_df.columns]
    if sort_columns:
        sorted_df = sorted_df.sort_values(sort_columns, ascending=[False] * len(sort_columns))
    value = _safe_text(sorted_df.iloc[0].get("ASIN"))
    return value or "暂无"


def _top_reason_label(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "暂无"
    first_row = df.iloc[0]
    category = _safe_text(first_row.get("Reason Category")) or _safe_text(first_row.get("原因分类"))
    raw_reason = _safe_text(first_row.get("Return Reason")) or _safe_text(first_row.get("退货原因"))
    if category and category != "其他":
        return _shorten_text(category, 10)
    if raw_reason:
        categorized = categorize_return_reason(raw_reason)
        if categorized and categorized != "其他":
            return _shorten_text(categorized, 10)
        return _shorten_text(_humanize_reason_code(raw_reason), 10)
    return "暂无"


def _humanize_reason_code(value: str) -> str:
    text = value.strip()
    code_map = {
        "QUALITY_UNACCEPTABLE": "产品质量问题",
        "NOT_COMPATIBLE": "尺寸/适配问题",
        "MISSING_PARTS": "缺件问题",
        "ORDERED_WRONG_ITEM": "买错/下错单",
        "UNWANTED_ITEM": "不想要了",
        "UNDELIVERABLE_UNKNOWN": "配送/无法送达",
        "DAMAGED_BY_CARRIER": "配送破损",
        "DEFECTIVE": "产品故障",
    }
    upper = text.upper()
    if upper in code_map:
        return code_map[upper]
    if "_" in text and text.upper() == text:
        return text.replace("_", " ").title()
    return text


def _render_ai_report_cards(report: str) -> None:
    sections = _split_report_sections(report)
    if not sections:
        st.markdown(_clean_ai_markdown(report))
        return

    cleaned_sections = [
        (title, _clean_ai_markdown(body))
        for title, body in sections
        if _clean_ai_markdown(body).strip()
    ]
    if not cleaned_sections:
        st.info("AI 报告内容为空，请重新生成。")
        return

    summary_title, summary_body = cleaned_sections[0]
    summary_title = _strip_inline_markdown(summary_title)
    st.markdown(
        f"""
        <div class="ai-report-hero">
            <div class="ai-report-eyebrow">AI 运营分析报告</div>
            <div class="ai-report-hero-title">{escape(summary_title)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(summary_body)

    remaining = cleaned_sections[1:]
    for index in range(0, len(remaining), 2):
        cols = st.columns(2)
        for col, section in zip(cols, remaining[index : index + 2]):
            title, body = section
            with col:
                with st.container(border=True):
                    st.markdown(f"#### {_strip_inline_markdown(title)}")
                    st.markdown(body)


def _split_report_sections(report: str) -> list[tuple[str, str]]:
    report = _strip_ai_noise(report)
    lines = report.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_body: list[str] = []
    for line in lines:
        heading = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*$", line)
        numbered_heading = re.match(r"^\s*\d+[.、]\s*(.+?)\s*$", line)
        if heading or numbered_heading:
            if current_title or current_body:
                sections.append((current_title or "分析摘要", current_body))
            current_title = _strip_inline_markdown((heading or numbered_heading).group(1).strip())
            current_body = []
        else:
            current_body.append(line.strip())
    if current_title or current_body:
        sections.append((current_title or "分析摘要", current_body))
    cleaned = []
    for title, body_lines in sections:
        body = "\n".join(line for line in body_lines if line).strip()
        if body:
            cleaned.append((title, body))
    return cleaned


def _strip_ai_noise(report: str) -> str:
    text = report.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```(?:json|python|text|markdown|md)?[\s\S]*?```", "", text, flags=re.IGNORECASE)
    noisy_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            noisy_lines.append(line)
            continue
        if stripped in {"{", "}", "[", "]"}:
            continue
        if re.match(r'^["\']?[A-Za-z_][\w\s-]*["\']?\s*:\s*[\{\["\d-]', stripped):
            continue
        if stripped.startswith(("分析数据 JSON", "JSON：", "JSON:", "```")):
            continue
        noisy_lines.append(line)
    return "\n".join(noisy_lines).strip()


def _clean_ai_markdown(markdown_text: str) -> str:
    text = _strip_ai_noise(markdown_text)
    cleaned_lines = []
    previous_blank = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        if stripped.startswith("|") and stripped.endswith("|"):
            cleaned_lines.append(_strip_inline_markdown(stripped))
            continue
        if re.match(r"^[*-]\s+", stripped):
            cleaned_lines.append(_strip_inline_markdown(stripped))
            continue
        numbered = re.match(r"^\d+[.、]\s+(.+)$", stripped)
        if numbered:
            cleaned_lines.append(f"- {_strip_inline_markdown(numbered.group(1).strip())}")
            continue
        cleaned_lines.append(_strip_inline_markdown(stripped))
    return "\n".join(cleaned_lines).strip()


def _strip_inline_markdown(text: str) -> str:
    clean = str(text)
    clean = re.sub(r"\*\*(.*?)\*\*", r"\1", clean)
    clean = re.sub(r"__(.*?)__", r"\1", clean)
    clean = re.sub(r"`([^`]*)`", r"\1", clean)
    clean = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", clean)
    clean = clean.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _safe_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _display_orders(
    df: pd.DataFrame,
    compact: bool = False,
    include_details: bool = False,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    columns = {
        "Order ID": "订单号",
        "ASIN": "ASIN",
        "SKU": "SKU",
        "Refund Date": "退款日期",
        "Refund Amount": "退款金额",
        "Matched Returns": "是否找到退货记录",
        "Matched Ledger": "是否找到入仓记录",
        "Days Since Refund": "距离退款天数",
        "Operation Priority": "优先级",
        "Priority Action": "处理时效",
        "Operation Status": "处理状态",
        "Risk Diagnosis": "风险诊断",
        "Risk Level With Confidence": "风险等级",
        "Evidence Confidence": "诊断可信度",
        "Conclusion Type": "结论类型",
        "Risk Explanation": "风险说明",
        "Suggested Action": "建议动作",
    }
    if compact:
        wanted = ["Order ID", "Refund Amount", "Days Since Refund", "Risk Level With Confidence"]
    elif include_details:
        wanted = list(columns.keys()) + [
            "Product Name",
            "Return Date",
            "Ledger Date",
            "Return Reason",
            "Ledger Disposition",
            "Priority Score",
            "Priority Reasons",
            "Evidence Summary",
            "Conclusion Type",
            "Conclusion Boundary",
            "Diagnostic Confidence Reason",
            "Amazon Support Confirmation",
            "Risk Factors",
            "Unmatched Reason",
        ]
        columns.update(
            {
                "Product Name": "产品名称",
                "Return Date": "退货日期",
                "Ledger Date": "入仓记录日期",
                "Return Reason": "退货原因",
                "Ledger Disposition": "商品状态",
                "Priority Score": "优先级分数",
                "Priority Reasons": "优先级原因",
                "Evidence Summary": "证据摘要",
                "Conclusion Type": "结论类型",
                "Conclusion Boundary": "结论边界",
                "Diagnostic Confidence Reason": "可信度说明",
                "Amazon Support Confirmation": "Amazon Support确认",
                "Risk Factors": "高风险原因",
                "Unmatched Reason": "未匹配原因",
            }
        )
    else:
        wanted = list(columns.keys())
    visible = df[[column for column in wanted if column in df.columns]].copy()
    visible = visible.rename(columns=columns)
    if not compact:
        visible.insert(0, "序号", range(1, len(visible) + 1))
    for identifier in ["ASIN", "SKU"]:
        if identifier in visible.columns:
            visible[identifier] = visible[identifier].apply(
                lambda value: "" if _is_blank_display_value(value) else value
            )
            if not compact and visible[identifier].astype(str).str.strip().eq("").all():
                visible = visible.drop(columns=[identifier])
    if "风险等级" in visible.columns and "Risk Level With Confidence" not in visible.columns:
        visible["风险等级"] = visible["风险等级"].map(
            {
                "High": "真正高风险",
                "Needs Review": "需要人工确认",
                "Data Incomplete": "数据不完整",
                "Medium": "需要人工确认",
                "Low": "正常/低风险",
            }
        ).fillna(visible["风险等级"])
    if "诊断可信度" in visible.columns:
        visible["诊断可信度"] = visible["诊断可信度"].map(
            {
                "High Confidence": "高",
                "Medium Confidence": "中",
                "Low Confidence": "低",
            }
        ).fillna(visible["诊断可信度"])
    if "高风险原因" in visible.columns:
        visible["高风险原因"] = visible["高风险原因"].apply(_format_risk_factors_inline)
    return visible


def _center_dataframe(df: pd.DataFrame):
    if df.empty:
        return df
    return (
        df.style.set_properties(**{"text-align": "center"})
        .set_table_styles(
            [
                {"selector": "th", "props": [("text-align", "center")]},
                {"selector": "td", "props": [("text-align", "center")]},
            ]
        )
    )


def _render_soft_table(
    df: pd.DataFrame,
    currency_columns: set[str] | None = None,
    max_rows: int | None = None,
) -> None:
    if df.empty:
        st.info("暂无数据。")
        return
    currency_columns = currency_columns or set()
    table_df = df.head(max_rows).copy() if max_rows else df.copy()
    column_classes = {
        column: _table_column_class(column, currency_columns)
        for column in table_df.columns
    }
    colgroup = "".join(
        f'<col class="{column_classes[column]}">' for column in table_df.columns
    )
    headers = "".join(
        f'<th class="{column_classes[column]}">{escape(str(column))}</th>'
        for column in table_df.columns
    )
    rows = []
    for _, row in table_df.iterrows():
        row_class = _table_row_class(row)
        cells = []
        confidence_title = escape(str(row.get("可信度说明", "") or ""))
        for column, value in row.items():
            title = f' title="{confidence_title}"' if confidence_title and column in {"风险等级", "诊断可信度"} else ""
            value_class = _table_cell_value_class(column, value)
            cell_class = f"{column_classes[column]} {value_class}".strip()
            cells.append(
                f'<td class="{cell_class}"{title}>{escape(_format_table_cell(value, column, currency_columns))}</td>'
            )
        cells = "".join(cells)
        rows.append(f"<tr class=\"{row_class}\">{cells}</tr>")
    st.markdown(
        f"""
        <div class="soft-table-wrap">
            <table class="soft-table">
                <colgroup>{colgroup}</colgroup>
                <thead><tr>{headers}</tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _table_column_class(column: str, currency_columns: set[str]) -> str:
    text = str(column).lower()
    if str(column) == "序号":
        return "col-seq"
    if str(column) in {"订单号", "Order ID"} or "order id" in text:
        return "col-order"
    if "建议动作" in text or "风险说明" in text or "证据摘要" in text or "未匹配原因" in text:
        return "col-action"
    if _is_percent_column(column):
        return "col-percent"
    if str(column) in currency_columns or _is_amount_column(column):
        return "col-money"
    if "次数" in text or "订单数" in text or "数量" in text or "count" in text or "quantity" in text:
        return "col-count"
    if "日期" in text or "date" in text:
        return "col-date"
    if "风险等级" in text or "状态" in text or "是否" in text:
        return "col-status"
    return "col-text"


def _table_cell_value_class(column: str, value: object) -> str:
    if str(column) not in {"诊断可信度", "风险等级"}:
        return ""
    text = str(value)
    if "低可信" in text or text == "低":
        return "confidence-low-cell"
    if "中可信" in text or text == "中":
        return "confidence-medium-cell"
    if "高可信" in text or text == "高":
        return "confidence-high-cell"
    return ""


def _table_row_class(row: pd.Series) -> str:
    values = {str(value).strip() for value in row.tolist()}
    if "真正高风险" in values or "High" in values:
        return "risk-high-row"
    if "需要人工确认" in values or "数据不完整" in values or "Medium" in values or "Needs Review" in values:
        return "risk-medium-row"
    return ""


def _format_table_cell(value: object, column: str, currency_columns: set[str]) -> str:
    if pd.isna(value):
        return ""
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if _is_percent_column(column) and pd.notna(number):
        return f"{float(number) * 100:.2f}%"
    if column in currency_columns:
        if pd.notna(number):
            return f"${float(number):,.2f}"
    if _is_amount_column(column) and pd.notna(number):
        return f"{float(number):,.2f}"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except TypeError:
            pass
    return str(value)


def _is_percent_column(column: str) -> bool:
    text = str(column).lower()
    return "占比" in text or "share" in text or "rate" in text or "率" in text


def _is_amount_column(column: str) -> bool:
    text = str(column).lower()
    return "金额" in text or "amount" in text


def _is_blank_display_value(value: object) -> bool:
    if pd.isna(value):
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "-", "--", "<na>"}


def _format_risk_factors_inline(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    if not text:
        return ""
    factors = []
    for part in text.split(";"):
        clean = part.strip()
        if not clean:
            continue
        clean = clean.split(" (+", 1)[0].strip()
        factors.append(clean)
    return "；".join(factors)


if __name__ == "__main__":
    main()
