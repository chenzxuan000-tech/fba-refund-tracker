from __future__ import annotations

import re
from html import escape
from datetime import date, datetime

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


FILTER_PRESETS = {
    "高风险检查": {
        "global_observation_window": 60,
        "global_risk_level": "需要人工确认",
        "global_asin_filter": "",
        "global_sku_filter": "",
        "advanced_mode": False,
    },
    "产品问题分析": {
        "global_observation_window": 90,
        "global_risk_level": "全部",
        "global_asin_filter": "",
        "global_sku_filter": "",
        "advanced_mode": False,
    },
    "Case处理模式": {
        "global_observation_window": 60,
        "global_risk_level": "真正高风险",
        "global_asin_filter": "",
        "global_sku_filter": "",
        "advanced_mode": False,
    },
}


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
        st.markdown('<div class="sidebar-section-title">导航</div>', unsafe_allow_html=True)
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
        _render_page_intro("总览", "快速判断当前筛选范围内，今天应该先处理哪些退款风险。")
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
        st.markdown(
            """
            <div class="workbench-toolbar">
                <div>
                    <strong>运营任务队列</strong>
                    <span>默认只看 P1，避免被长表格分散注意力。</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        priority_filter = st.selectbox("处理优先级", ["P1", "P2", "P3", "全部"], index=0, key="risk_page_priority_filter")
        if priority_filter != "全部" and "Operation Priority" in risk_page_df.columns:
            risk_page_df = risk_page_df[risk_page_df["Operation Priority"].eq(priority_filter)]
        _render_orders_table(risk_page_df)
        if advanced_mode:
            with st.expander("展开高级核查工具", expanded=False):
                render_top_risk_orders_dashboard(risk_page_df)
                render_order_lifecycle(risk_page_df)
                render_order_audit_table(risk_page_df)
    elif page == "匹配诊断":
        _render_page_intro(
            "匹配诊断",
            "查看每个退款订单是否匹配到退货、FBA 入仓和赔偿证据；这里只展示运营可理解的结论，原始字段和匹配日志放在技术详情里。",
        )
        render_match_diagnostics(visible_df)
        if advanced_mode:
            with st.expander("展开技术详情", expanded=False):
                _render_detected_columns(return_columns, payment_columns)
                render_data_preview(report_frames)
    else:
        _render_page_intro("AI运营分析报告", "生成 AI 运营决策报告，并导出当前分析表格。")
        _render_ai_report(visible_df, summary_df, reason_analysis)
        _render_export_download(visible_df, summary_df, reason_analysis, report_frames)


def _render_sidebar_nav() -> str:
    nav_items = {
        "总览": "⌂  总览",
        "风险订单": "⚠  风险订单",
        "匹配诊断": "◎  匹配诊断",
        "退货原因": "↩  退货原因",
        "AI运营分析报告": "✦  AI运营分析报告",
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
    st.markdown('<div class="sidebar-section-title">全局筛选</div>', unsafe_allow_html=True)
    _ensure_filter_defaults()
    _render_filter_summary()
    with st.expander("展开筛选", expanded=False):
        custom_presets = st.session_state.get("custom_filter_presets", {})
        preset_options = ["不套用"] + list(FILTER_PRESETS.keys()) + list(custom_presets.keys())
        selected_preset = st.selectbox(
            "筛选方案",
            options=preset_options,
            key="filter_preset_select",
            help="常用运营场景可以直接套用，也可以保存当前筛选。",
        )
        col_apply, col_reset = st.columns(2)
        if col_apply.button("套用方案", width="stretch"):
            _apply_filter_preset(selected_preset)
            st.rerun()
        if col_reset.button("重置筛选", width="stretch"):
            _reset_filters()
            st.rerun()

        observation_window_days = st.selectbox(
            "观察窗口",
            options=[30, 45, 60, 90],
            help="用于判断退款后多久仍需人工确认。系统只提示风险，不直接下最终结论。",
            key="global_observation_window",
        )
        as_of = st.date_input("统计截止日期", key="global_as_of")
        risk_level = st.selectbox(
            "风险等级",
            options=["全部", "真正高风险", "需要人工确认", "数据不完整", "正常/低风险"],
            key="global_risk_level",
        )
        asin_filter = st.text_input("ASIN筛选", placeholder="输入 ASIN，可留空", key="global_asin_filter")
        sku_filter = st.text_input("SKU筛选", placeholder="输入 SKU，可留空", key="global_sku_filter")
        advanced_mode = st.toggle(
            "高级模式",
            help="开启后显示字段识别、原始数据、匹配日志和完整核查工具。",
            key="advanced_mode",
        )

        preset_name = st.text_input("保存筛选方案", placeholder="例如：加拿大站高金额退款", key="filter_preset_name")
        if st.button("保存当前筛选", width="stretch"):
            _save_current_filter_preset(preset_name)
            st.rerun()
    return {
        "observation_window_days": int(st.session_state.get("global_observation_window", observation_window_days)),
        "as_of": st.session_state.get("global_as_of", as_of),
        "risk_level": str(st.session_state.get("global_risk_level", risk_level)),
        "asin_filter": str(st.session_state.get("global_asin_filter", asin_filter)).strip(),
        "sku_filter": str(st.session_state.get("global_sku_filter", sku_filter)).strip(),
        "advanced_mode": bool(st.session_state.get("advanced_mode", advanced_mode)),
    }


def _ensure_filter_defaults() -> None:
    defaults = {
        "global_observation_window": 60,
        "global_as_of": date.today(),
        "global_risk_level": "全部",
        "global_asin_filter": "",
        "global_sku_filter": "",
        "advanced_mode": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _render_filter_summary() -> None:
    observation = st.session_state.get("global_observation_window", 60)
    risk = st.session_state.get("global_risk_level", "全部")
    asin = str(st.session_state.get("global_asin_filter", "") or "").strip()
    sku = str(st.session_state.get("global_sku_filter", "") or "").strip()
    details = [f"{observation}天", str(risk)]
    if asin:
        details.append(f"ASIN: {asin}")
    if sku:
        details.append(f"SKU: {sku}")
    st.markdown(
        f"""
        <div class="filter-summary">
            <span>当前筛选</span>
            <p>{escape(" · ".join(details))}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _apply_filter_preset(name: str) -> None:
    if name == "不套用":
        return
    presets = {**FILTER_PRESETS, **st.session_state.get("custom_filter_presets", {})}
    preset = presets.get(name, {})
    for key, value in preset.items():
        st.session_state[key] = value


def _reset_filters() -> None:
    for key, value in {
        "global_observation_window": 60,
        "global_as_of": date.today(),
        "global_risk_level": "全部",
        "global_asin_filter": "",
        "global_sku_filter": "",
        "advanced_mode": False,
    }.items():
        st.session_state[key] = value


def _save_current_filter_preset(name: str) -> None:
    clean_name = str(name or "").strip()
    if not clean_name:
        st.warning("请先输入筛选方案名称。")
        return
    custom_presets = dict(st.session_state.get("custom_filter_presets", {}))
    custom_presets[clean_name] = {
        "global_observation_window": st.session_state.get("global_observation_window", 60),
        "global_as_of": st.session_state.get("global_as_of", date.today()),
        "global_risk_level": st.session_state.get("global_risk_level", "全部"),
        "global_asin_filter": st.session_state.get("global_asin_filter", ""),
        "global_sku_filter": st.session_state.get("global_sku_filter", ""),
        "advanced_mode": st.session_state.get("advanced_mode", False),
    }
    st.session_state["custom_filter_presets"] = custom_presets


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
        width="stretch",
    )


def _render_uploads():
    uploaded_returns = st.session_state.get("returns_file")
    uploaded_payments = st.session_state.get("payments_file") or []
    uploaded_ledger = st.session_state.get("ledger_file")
    uploaded_reimbursements = st.session_state.get("reimbursements_file")
    has_required_uploads = bool(uploaded_returns) and bool(uploaded_payments)
    upload_infos = _build_upload_status_infos(
        returns_file=uploaded_returns,
        payment_files=uploaded_payments,
        ledger_file=uploaded_ledger,
        reimbursements_file=uploaded_reimbursements,
    )
    _render_upload_status_cards(
        upload_infos,
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


def _build_upload_status_infos(
    returns_file,
    payment_files,
    ledger_file,
    reimbursements_file,
) -> list[dict[str, str | int | bool]]:
    items = [
        ("returns", "退货报表", [returns_file] if returns_file else []),
        ("payments", "交易报表", list(payment_files or [])),
        ("ledger", "库存流水", [ledger_file] if ledger_file else []),
        ("reimbursements", "赔偿报表", [reimbursements_file] if reimbursements_file else []),
    ]
    infos: list[dict[str, str | int | bool]] = []
    for key, label, files in items:
        names = [getattr(file, "name", "") for file in files if file]
        signature = "|".join(names)
        time_key = f"upload_time_{key}"
        signature_key = f"upload_signature_{key}"
        if signature and st.session_state.get(signature_key) != signature:
            st.session_state[signature_key] = signature
            st.session_state[time_key] = datetime.now().strftime("%Y-%m-%d %H:%M")
        elif not signature:
            st.session_state.pop(signature_key, None)
            st.session_state.pop(time_key, None)

        infos.append(
            {
                "label": label,
                "count": len(names),
                "ready": bool(names),
                "file_names": "、".join(names[:2]) + (" 等" if len(names) > 2 else ""),
                "uploaded_at": st.session_state.get(time_key, ""),
            }
        )
    return infos


def _render_upload_status_cards(upload_infos: list[dict[str, str | int | bool]]) -> None:
    cards = []
    for item in upload_infos:
        label = str(item["label"])
        count = int(item["count"])
        is_ready = bool(item["ready"])
        file_names = str(item.get("file_names") or "等待上传")
        uploaded_at = str(item.get("uploaded_at") or "未上传")
        tone = "ready" if is_ready else "empty"
        icon = "✓" if is_ready else "待"
        status = "已上传" if is_ready else "待上传"
        cards.append(
            f'<div class="upload-status-card {tone}">'
            f'<span class="upload-status-icon">{escape(icon)}</span>'
            '<div class="upload-status-body">'
            f"<div class='upload-status-title'>{escape(label)}（{count}）<span>{escape(status)}</span></div>"
            f"<div class='upload-status-file' title='{escape(file_names)}'>{escape(file_names)}</div>"
            f"<div class='upload-status-time'>上传时间：{escape(uploaded_at)}</div>"
            "</div>"
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
        if st.button("使用示例数据快速体验", type="primary", width="stretch"):
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
    if st.button("退出示例数据，上传真实报表", width="content"):
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


def _metric_value_class(value: str) -> str:
    value_len = len(str(value))
    if value_len >= 12:
        return "metric-value tight"
    if value_len >= 9:
        return "metric-value compact"
    return "metric-value"


def _metric_help_class(text: str) -> str:
    text_len = len(str(text))
    if text_len >= 18:
        return "metric-help tight"
    if text_len >= 14:
        return "metric-help compact"
    return "metric-help"


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
        ("退款订单数", f"{total_refunds:,}", "筛选范围退款订单", "当前样本", "neutral", "01"),
        ("已确认退回", f"{confirmed_returned:,}", "已匹配退货/入仓", "确认事实", "ok", "02"),
        ("未确认退回", f"{unconfirmed:,}", "缺少明确回仓证据", "需确认", "warning", "03"),
        ("超60天待确认", f"{overdue_unconfirmed:,}", "建议人工核查", "优先关注", "danger", "04"),
        ("待核查退款金额", f"${suspicious_refund_amount:,.2f}", "待确认订单金额", "资金影响", "danger", "05"),
    ]
    cards = []
    for label, value, help_text, trend, tone, icon in metrics:
        value_class = _metric_value_class(value)
        help_class = _metric_help_class(help_text)
        cards.append(
            "<div class='metric-card {tone}'>"
            "<div class='metric-top'>"
            "<span class='metric-icon'>{icon}</span>"
            "<span class='metric-label'>{label}</span>"
            "</div>"
            "<div class='{value_class}' title='{value}'>{value}</div>"
            "<div class='metric-trend'>{trend}</div>"
            "<div class='{help_class}' title='{help_text}'>{help_text}</div>"
            "</div>".format(
                tone=escape(tone),
                icon=escape(icon),
                label=escape(label),
                value_class=escape(value_class),
                value=escape(value),
                trend=escape(trend),
                help_class=escape(help_class),
                help_text=escape(help_text),
            )
        )
    st.markdown(f"<div class='metric-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def _render_dashboard(
    analysis_df: pd.DataFrame,
    reason_summary_df: pd.DataFrame,
    reason_analysis: dict[str, pd.DataFrame],
) -> None:
    asin_summary = reason_analysis["asin_summary"]
    risk_orders = analysis_df[analysis_df["Risk Level"].isin(["High", "Needs Review", "Data Incomplete"])].copy()
    p1_count = int(analysis_df.get("Operation Priority", pd.Series(dtype=str)).eq("P1").sum())
    high_risk_count = int(analysis_df["Risk Level"].eq("High").sum())
    review_count = int(analysis_df["Risk Level"].isin(["High", "Needs Review"]).sum())
    suspicious_amount = float(analysis_df.loc[analysis_df["Risk Level"].isin(["High", "Needs Review"]), "Refund Amount"].fillna(0).sum())
    _render_confidence_notice(analysis_df)
    st.markdown('<div class="dashboard-section-heading">今日处理概览</div>', unsafe_allow_html=True)
    col_a, col_b = st.columns(2, gap="large")
    col_a.markdown(_mini_card("P1 立即处理", f"{p1_count:,}", f"待确认 {review_count:,} 单，真正高风险 {high_risk_count:,} 单", "danger"), unsafe_allow_html=True)
    col_b.markdown(_mini_card("待核查退款金额", f"${suspicious_amount:,.2f}", "需要人工确认订单对应退款金额", "warning"), unsafe_allow_html=True)
    _render_overview_next_actions(p1_count, review_count, suspicious_amount)
    _render_decision_actions(analysis_df, reason_summary_df, asin_summary)

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


def _render_decision_actions(
    analysis_df: pd.DataFrame,
    reason_summary_df: pd.DataFrame,
    asin_summary: pd.DataFrame,
) -> None:
    priority_series = _series_or_default(analysis_df, "Operation Priority", "")
    risk_series = _series_or_default(analysis_df, "Risk Level", "")
    case_candidates = analysis_df[
        priority_series.isin(["P1", "P2"]) & risk_series.isin(["High", "Needs Review"])
    ].copy()
    if not case_candidates.empty:
        sort_columns = [
            column for column in ["Operation Priority", "Refund Amount", "Days Since Refund"]
            if column in case_candidates.columns
        ]
        if sort_columns:
            case_candidates = case_candidates.sort_values(
                sort_columns,
                ascending=[True, False, False][: len(sort_columns)],
            )
        case_order = _safe_text(case_candidates.iloc[0].get("Order ID"))
    else:
        case_order = "暂无"

    top_asin = _top_asin_label(asin_summary)
    top_reason = _top_reason_label(reason_summary_df)
    action_items = [
        ("今日优先处理", f"{int(priority_series.eq('P1').sum())} 个 P1 订单"),
        ("建议先开 Case", case_order),
        ("当前最危险 ASIN", top_asin),
        ("主要退货原因", top_reason),
    ]
    cards = []
    for label, value in action_items:
        cards.append(
            "<div class='decision-action-card'>"
            f"<span>{escape(label)}</span>"
            f"<strong title='{escape(value)}'>{escape(_shorten_text(value, 28))}</strong>"
            "</div>"
        )
    st.markdown(
        f"<div class='decision-action-grid'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


def _render_confidence_notice(df: pd.DataFrame) -> None:
    if df.empty:
        return
    confidence = _series_or_default(df, "Evidence Confidence", "Low Confidence")
    high = int(confidence.eq("High Confidence").sum())
    medium = int(confidence.eq("Medium Confidence").sum())
    low = int(confidence.eq("Low Confidence").sum())
    st.markdown(
        f"""
        <div class="confidence-notice">
            <div>
                <div class="confidence-title">系统可信度提示</div>
                <div class="confidence-copy">本工具只做运营核查优先级提示，不把“未匹配”直接判定为“未退回”。</div>
            </div>
            <div class="confidence-stats">
                <span>高可信 {high}</span>
                <span>中可信 {medium}</span>
                <span>低可信 {low}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_overview_next_actions(p1_count: int, review_count: int, suspicious_amount: float) -> None:
    if p1_count > 0:
        title = "下一步动作"
        body = f"先处理 {p1_count} 个 P1 订单，再核查待确认金额 ${suspicious_amount:,.2f}。"
        tone = "warning"
    elif review_count > 0:
        title = "下一步动作"
        body = f"当前有 {review_count} 个订单需要人工确认，建议按退款金额从高到低抽查。"
        tone = "neutral"
    else:
        title = "今日无高优先级风险"
        body = "当前筛选范围内暂无需要立即处理的订单，建议查看退货原因和 Listing 优化建议。"
        tone = "ok"
    st.markdown(
        f"""
        <div class="next-action-card {tone}">
            <span>{escape(title)}</span>
            <p>{escape(body)}</p>
        </div>
        """,
        unsafe_allow_html=True,
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
        st.markdown(
            """
            <div class="success-empty-state">
                <div class="success-icon">✓</div>
                <div>
                    <div class="success-title">今日无高优先级风险订单</div>
                    <div class="success-copy">当前筛选条件下没有需要立即处理的订单。可以切换 P2 / P3，或查看退货原因判断产品与 Listing 优化方向。</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    _render_risk_priority_cards(df)
    with st.expander("查看全部风险订单", expanded=False):
        _render_soft_table(_display_orders(df, compact=False), currency_columns={"退款金额"})

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

    if st.button("生成 AI 分析报告", type="primary", width="stretch"):
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
            width="stretch",
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
        width="stretch",
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

    _render_reason_next_action(category_summary)
    _render_reason_insight_summary(category_summary, distribution, reason_summary_df)
    col_category, col_reason = st.columns([5, 5], gap="large")
    with col_category:
        st.markdown("**退货原因趋势图**")
        if category_summary.empty:
            st.info("暂无原因分类数据。")
        else:
            _render_horizontal_bar_chart(
                category_summary,
                label_column="Reason Category",
                value_column="Reason Share",
                value_title="占比",
            )
            display_category = category_summary.rename(
                columns={
                    "Reason Category": "原因分类",
                    "Reason Count": "订单数",
                    "Refund Amount": "退款金额",
                    "Reason Share": "占比",
                }
            )
            with st.expander("查看原因分类占比表", expanded=False):
                _render_soft_table(display_category)

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

    st.markdown("**下一步优化建议**")
    active_categories = set(category_summary.get("Reason Category", pd.Series(dtype=str)).astype(str))
    advice_to_show = advice[advice["原因分类"].isin(active_categories)] if active_categories else advice
    _render_reason_advice_cards(advice_to_show.head(6))

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


def _render_reason_next_action(category_summary: pd.DataFrame) -> None:
    if category_summary.empty:
        return
    top = category_summary.sort_values("Reason Count", ascending=False).iloc[0]
    category = _safe_text(top.get("Reason Category")) or "退货原因"
    count = int(pd.to_numeric(pd.Series([top.get("Reason Count")]), errors="coerce").fillna(0).iloc[0])
    share = pd.to_numeric(pd.Series([top.get("Reason Share")]), errors="coerce").fillna(0).iloc[0]
    st.markdown(
        f"""
        <div class="next-action-card neutral">
            <span>下一步动作</span>
            <p>优先复盘「{escape(category)}」相关订单：当前 {count} 单，占比 {float(share) * 100:.2f}%。先看差评、退货留言和 Listing 表达是否一致。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_reason_insight_summary(
    category_summary: pd.DataFrame,
    distribution: pd.DataFrame,
    reason_summary_df: pd.DataFrame,
) -> None:
    if category_summary.empty and distribution.empty and reason_summary_df.empty:
        return
    insights = []
    if not category_summary.empty:
        sorted_categories = category_summary.sort_values("Reason Count", ascending=False)
        top = sorted_categories.iloc[0]
        category = _safe_text(top.get("Reason Category")) or "退货原因"
        share = pd.to_numeric(pd.Series([top.get("Reason Share")]), errors="coerce").fillna(0).iloc[0]
        focus = _reason_focus_action(category)
        insights.append(("主要问题", f"{category}占比 {float(share) * 100:.2f}%，{focus}"))
    if not distribution.empty:
        sort_columns = [column for column in ["Reason Count", "Refund Amount"] if column in distribution.columns]
        sorted_distribution = (
            distribution.sort_values(sort_columns, ascending=[False, False][: len(sort_columns)])
            if sort_columns
            else distribution
        )
        row = sorted_distribution.iloc[0]
        asin = _safe_text(row.get("ASIN")) or "某 ASIN"
        sku = _safe_text(row.get("SKU")) or "对应 SKU"
        reason = _safe_text(row.get("Reason Category")) or "退货原因"
        insights.append(("异常对象", f"{asin} / {sku} 的「{reason}」更集中，建议先抽样查看退货留言。"))
    if not reason_summary_df.empty:
        top_reason = _top_reason_label(reason_summary_df)
        insights.append(("Listing 线索", f"高频原因集中在「{top_reason}」，优先检查主图、标题、尺寸说明和五点描述是否造成预期偏差。"))
    cards = []
    for label, text in insights[:3]:
        cards.append(
            "<div class='insight-card'>"
            f"<span>{escape(label)}</span>"
            f"<p>{escape(_shorten_text(text, 110))}</p>"
            "</div>"
        )
    st.markdown(
        "<div class='insight-section-title'>AI 洞察摘要</div>"
        f"<div class='insight-grid'>{''.join(cards)}</div>",
        unsafe_allow_html=True,
    )


def _reason_focus_action(category: str) -> str:
    if any(keyword in category for keyword in ["质量", "故障", "缺件"]):
        return "更偏向产品或批次问题"
    if any(keyword in category for keyword in ["尺寸", "适配", "Listing", "信息"]):
        return "更偏向信息表达或规格误解"
    if any(keyword in category for keyword in ["配送", "破损", "无法送达"]):
        return "更偏向履约或包装链路"
    return "建议结合订单备注继续确认"


def _render_reason_advice_cards(advice_df: pd.DataFrame) -> None:
    if advice_df.empty:
        st.info("暂无建议。")
        return
    cards = []
    for _, row in advice_df.iterrows():
        category = _safe_text(row.get("原因分类"))
        possible = _safe_text(row.get("可能原因"))
        action = _safe_text(row.get("建议优化动作"))
        cards.append(
            "<div class='reason-advice-card'>"
            f"<div class='reason-advice-title'>{escape(category)}</div>"
            f"<div class='reason-advice-copy'><span>可能原因</span>{escape(_shorten_text(possible, 70))}</div>"
            f"<div class='reason-advice-copy'><span>建议动作</span>{escape(_shorten_text(action, 88))}</div>"
            "</div>"
        )
    st.markdown(f"<div class='reason-advice-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)


def _render_recommendations(df: pd.DataFrame) -> None:
    st.subheader("ASIN 优化建议")
    if df.empty:
        st.info("暂无优化建议。")
        return

    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
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
            --bg: #EEF3F8;
            --bg-soft: #F4F7FB;
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
            --radius-sm: 12px;
            --radius-md: 16px;
            --radius-lg: 20px;
            --space-section: 1.6rem;
        }

        html, body, [data-testid="stAppViewContainer"], .stApp {
            background:
                radial-gradient(circle at 20% 0%, rgba(255,255,255,0.68), rgba(255,255,255,0) 24rem),
                linear-gradient(180deg, #F3F7FC 0%, var(--bg) 32%, #E9EEF5 100%);
            color: var(--text);
        }

        [data-testid="stHeader"] {
            background: rgba(233, 238, 245, 0.82);
            backdrop-filter: blur(12px);
            z-index: 9998;
        }

        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"],
        [data-testid="stDeployButton"],
        [data-testid="stAppDeployButton"],
        #MainMenu {
            display: none !important;
            visibility: hidden !important;
        }

        [data-testid="stToolbar"] {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
        }

        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stExpandSidebarButton"],
        [data-testid="stSidebarCollapseButton"] {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            z-index: 9999 !important;
        }

        [data-testid="stExpandSidebarButton"],
        [data-testid="collapsedControl"] {
            position: fixed !important;
            top: 14px !important;
            left: 14px !important;
            width: 42px !important;
            height: 42px !important;
            align-items: center !important;
            justify-content: center !important;
            border-radius: 14px !important;
            background: rgba(255,255,255,0.84) !important;
            border: 1px solid rgba(190,200,212,0.52) !important;
            box-shadow: 4px 4px 12px rgba(150,164,184,0.22) !important;
        }

        [data-testid="stExpandSidebarButton"] svg,
        [data-testid="stExpandSidebarButton"] button,
        [data-testid="collapsedControl"] button {
            display: inline-flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            color: var(--text) !important;
        }

        .main .block-container {
            max-width: 1440px;
            margin-left: auto;
            margin-right: auto;
            padding-top: 1.55rem;
            padding-bottom: 4rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }

        h1, h2, h3, h4, h5, h6, p, label, span {
            color: var(--text);
        }

        h1, h2, h3 {
            font-weight: 700 !important;
            letter-spacing: 0 !important;
        }

        h1 { font-size: clamp(2.25rem, 3vw, 2.5rem) !important; margin-bottom: 0.65rem !important; }
        h2 { font-size: 1.5rem !important; }
        h3 { font-size: 1.25rem !important; }

        p, li, label, span {
            font-weight: 400;
        }

        [data-testid="stVerticalBlock"] {
            gap: 0.92rem;
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
            padding: 10px 6px 20px;
        }

        .sidebar-section-title {
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.04em;
            margin: 0.35rem 0 0.45rem;
        }

        .filter-summary {
            border-radius: var(--radius-md);
            background: rgba(255,255,255,0.62);
            border: 1px solid rgba(190,200,212,0.30);
            padding: 11px 12px;
            margin: 0.2rem 0 0.65rem;
        }

        .filter-summary span {
            color: var(--muted);
            font-size: 11px;
            font-weight: 700;
        }

        .filter-summary p {
            color: var(--text);
            font-size: 12px;
            line-height: 1.45;
            margin: 4px 0 0;
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
            border-radius: var(--radius-md);
            min-height: 38px;
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

        .metric-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 22px;
            align-items: stretch;
            margin: 1.05rem 0 1.65rem;
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
            min-height: 150px;
            padding: 18px 20px 17px;
            position: relative;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            background:
                linear-gradient(145deg, rgba(255,255,255,0.78), rgba(247,249,252,0.94));
        }

        .metric-card > * {
            position: relative;
            z-index: 1;
        }

        .metric-card::after {
            content: "";
            position: absolute;
            inset: 0 0 auto auto;
            width: 88px;
            height: 88px;
            border-radius: 0 18px 0 70px;
            opacity: 0.12;
            background: var(--accent);
        }

        .metric-card.danger::after { background: var(--danger); }
        .metric-card.warning::after { background: var(--warning); }
        .metric-card.ok::after { background: var(--success); }
        .metric-card.neutral::after { background: var(--accent); }

        .metric-card.danger {
            background: var(--card-strong);
            border-color: rgba(231, 111, 81, 0.18);
            box-shadow: 4px 4px 10px rgba(231,111,81,0.08), -4px -4px 10px rgba(255,255,255,0.72);
        }

        .metric-top {
            display: flex;
            align-items: center;
            gap: 9px;
            margin-bottom: 14px;
            min-width: 0;
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
            font-size: 12px;
            font-weight: 600;
        }

        .metric-label {
            min-width: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .metric-value, .mini-value {
            color: var(--text);
            font-size: 34px;
            font-weight: 700;
            line-height: 1.08;
            letter-spacing: 0;
            max-width: 100%;
            white-space: nowrap;
            overflow: hidden;
        }

        .metric-value.compact {
            font-size: clamp(27px, 1.8vw, 32px);
            line-height: 1.05;
        }

        .metric-value.tight {
            font-size: clamp(23px, 1.55vw, 28px);
            line-height: 1.05;
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
            font-size: 38px;
        }

        .metric-card.danger .metric-value.compact {
            font-size: clamp(27px, 1.8vw, 32px);
        }

        .metric-card.danger .metric-value.tight {
            font-size: clamp(23px, 1.55vw, 28px);
        }

        .metric-card.warning .metric-value {
            color: #A85F12;
        }

        .metric-trend {
            display: inline-flex;
            width: fit-content;
            margin-top: 9px;
            padding: 4px 9px;
            border-radius: 999px;
            background: rgba(102,126,234,0.08);
            color: var(--accent);
            font-size: 11px;
            font-weight: 700;
        }

        .metric-card.danger .metric-trend {
            color: #B94128;
            background: rgba(231,111,81,0.10);
        }

        .metric-card.warning .metric-trend {
            color: #8A4B0E;
            background: rgba(244,162,97,0.14);
        }

        .metric-card.ok .metric-trend {
            color: #2D7D55;
            background: rgba(82,183,136,0.14);
        }

        .metric-help, .mini-help {
            color: var(--muted);
            font-size: 12px;
            margin-top: 10px;
            line-height: 1.45;
        }

        .metric-help {
            max-width: 100%;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .metric-help.compact {
            font-size: 11px;
        }

        .metric-help.tight {
            font-size: 10px;
        }

        @media (max-width: 1320px) {
            .metric-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }

        @media (max-width: 860px) {
            .metric-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 560px) {
            .metric-grid {
                grid-template-columns: 1fr;
            }
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

        .confidence-notice,
        .next-action-card,
        .workbench-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            border-radius: 18px;
            background: var(--card-strong);
            border: 1px solid rgba(190,200,212,0.34);
            padding: 14px 16px;
            margin: 1.05rem 0 1.25rem;
        }

        .confidence-title,
        .next-action-card span,
        .workbench-toolbar strong {
            color: var(--text);
            font-size: 14px;
            font-weight: 700;
        }

        .confidence-copy,
        .next-action-card p,
        .workbench-toolbar span {
            color: var(--muted);
            font-size: 12px;
            margin: 3px 0 0;
            line-height: 1.45;
        }

        .confidence-stats {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }

        .confidence-stats span {
            border-radius: 999px;
            padding: 5px 9px;
            background: #F1F5F9;
            color: #64748B;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
        }

        .next-action-card {
            justify-content: flex-start;
            border-left: 4px solid rgba(102,126,234,0.42);
        }

        .next-action-card.ok {
            border-left-color: var(--success);
            background: #F3FBF6;
        }

        .next-action-card.warning {
            border-left-color: var(--warning);
            background: #FFF9ED;
        }

        .decision-action-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin: 1rem 0 1.8rem;
        }

        .decision-action-card {
            min-height: 92px;
            padding: 15px 16px;
            border-radius: var(--radius-lg);
            background: var(--card-strong);
            border: 1px solid rgba(190,200,212,0.32);
        }

        .decision-action-card span {
            display: block;
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            margin-bottom: 9px;
        }

        .decision-action-card strong {
            display: block;
            color: var(--text);
            font-size: 17px;
            font-weight: 700;
            line-height: 1.28;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .mini-card {
            min-height: 104px;
            padding: 16px 18px;
            border-left: 4px solid rgba(102, 126, 234, 0.42);
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
            border-left: 4px solid rgba(231,111,81,0.58);
            background: #FFF9F6;
            margin-bottom: 8px;
            min-height: 168px;
        }

        .risk-order-card.high {
            border-color: rgba(231,111,81,0.58);
            background: #FFF9F6;
        }

        .risk-order-card.medium {
            border-color: rgba(244,162,97,0.62);
            background: #FFFBF2;
        }

        .risk-order-card.low {
            border-color: rgba(100,116,139,0.32);
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
            background: rgba(231, 111, 81, 0.10);
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

        .evidence-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 10px;
        }

        .evidence-chip-row span {
            border-radius: 999px;
            padding: 4px 8px;
            background: rgba(100,116,139,0.08);
            color: #64748B;
            font-size: 10.5px;
            font-weight: 650;
            white-space: nowrap;
        }

        .risk-workflow-meta {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 5px 8px;
            margin-top: 10px;
            padding-top: 9px;
            border-top: 1px solid rgba(148,163,184,0.16);
        }

        .risk-workflow-meta span {
            color: var(--muted);
            font-size: 10.5px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .risk-order-card + [data-testid="stHorizontalBlock"] {
            margin-top: -0.15rem;
            margin-bottom: 0.85rem;
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

        .ai-section-header {
            margin: 1.45rem 0 0.75rem;
        }

        .ai-section-header h3 {
            margin: 0;
            color: var(--text);
            font-size: 22px;
            font-weight: 700;
            line-height: 1.25;
        }

        .ai-section-header p {
            margin: 7px 0 0;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.5;
        }

        .decision-line {
            display: grid;
            grid-template-columns: 44px 1fr;
            gap: 9px;
            align-items: start;
            margin: 7px 0;
        }

        .decision-line span {
            color: var(--accent);
            font-size: 11px;
            font-weight: 700;
            padding-top: 1px;
        }

        .decision-line p {
            margin: 0;
            color: var(--text);
            font-size: 12px;
            line-height: 1.5;
        }

        .report-panel {
            min-height: 232px;
            height: 100%;
            padding: 18px 18px 16px;
            border-radius: 20px;
            border: 1px solid rgba(190,200,212,0.28);
            background: var(--card-strong);
            box-shadow: 0 10px 28px rgba(30, 41, 59, 0.045);
            position: relative;
            overflow: hidden;
        }

        .report-panel::before {
            content: "";
            position: absolute;
            inset: 0 auto 0 0;
            width: 3px;
            background: rgba(102,126,234,0.42);
        }

        .report-panel.summary::before { background: rgba(231,111,81,0.54); }
        .report-panel.focus::before { background: rgba(244,162,97,0.58); }
        .report-panel.action::before { background: rgba(82,183,136,0.52); }

        .report-panel h4 {
            margin: 0 0 10px;
            color: var(--text);
            font-size: 16px;
            font-weight: 700;
        }

        .report-highlight {
            border-radius: 14px;
            background: rgba(102,126,234,0.055);
            color: var(--text);
            font-size: 13px;
            font-weight: 600;
            line-height: 1.5;
            padding: 9px 11px;
            margin-bottom: 12px;
        }

        .report-panel.summary .report-highlight {
            background: rgba(231,111,81,0.06);
        }

        .report-panel.focus .report-highlight {
            background: rgba(244,162,97,0.08);
        }

        .report-panel.action .report-highlight {
            background: rgba(82,183,136,0.07);
        }

        .report-confidence {
            margin-top: 11px;
            padding-top: 9px;
            border-top: 1px solid rgba(148,163,184,0.16);
            color: var(--muted);
            font-size: 10.5px;
            line-height: 1.45;
        }

        .management-summary {
            border-radius: 22px;
            padding: 18px 20px;
            margin: 0.9rem 0 1rem;
            background: linear-gradient(135deg, #FFFFFF 0%, #F7F9FC 100%);
            border: 1px solid rgba(190,200,212,0.34);
        }

        .management-summary span {
            display: block;
            color: var(--accent);
            font-size: 12px;
            font-weight: 800;
            margin-bottom: 8px;
        }

        .management-summary p {
            color: var(--text);
            font-size: 17px;
            font-weight: 700;
            line-height: 1.5;
            margin: 0;
        }

        .management-summary.warning {
            border-left: 4px solid var(--warning);
        }

        .management-summary.ok {
            border-left: 4px solid var(--success);
        }

        .upload-status-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            margin: 0.5rem 0 0.9rem;
        }

        .upload-status-card {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            min-height: 92px;
            padding: 14px 14px;
            border-radius: 16px;
            background: var(--card-strong);
            border: 1px solid var(--soft-border);
            color: var(--text);
            font-size: 14px;
            font-weight: 600;
            box-shadow: 3px 3px 8px rgba(150,164,184,0.14), -3px -3px 8px rgba(255,255,255,0.7);
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

        .upload-status-body {
            min-width: 0;
            flex: 1;
        }

        .upload-status-title {
            color: var(--text);
            font-size: 13px;
            font-weight: 700;
            line-height: 1.35;
            display: flex;
            align-items: center;
            gap: 7px;
            flex-wrap: wrap;
        }

        .upload-status-title span {
            color: var(--success);
            background: rgba(82,183,136,0.12);
            border-radius: 999px;
            padding: 2px 7px;
            font-size: 10px;
            font-weight: 700;
        }

        .upload-status-file,
        .upload-status-time {
            color: var(--muted);
            font-size: 11px;
            line-height: 1.35;
            margin-top: 5px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .upload-status-card.empty .upload-status-title span {
            color: var(--muted);
            background: rgba(113,128,150,0.12);
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

        .success-empty-state {
            display: flex;
            gap: 14px;
            align-items: center;
            border-radius: 20px;
            background: #F3FBF6;
            border: 1px solid rgba(82,183,136,0.18);
            padding: 18px 20px;
            margin: 1rem 0;
        }

        .success-icon {
            width: 38px;
            height: 38px;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(82,183,136,0.14);
            color: #2D7D55;
            font-weight: 800;
        }

        .success-title {
            color: var(--text);
            font-size: 16px;
            font-weight: 700;
        }

        .success-copy {
            color: var(--muted);
            font-size: 13px;
            line-height: 1.55;
            margin-top: 3px;
        }

        .reason-advice-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
            margin: 0.7rem 0 1.1rem;
        }

        .reason-advice-card {
            border-radius: 18px;
            background: var(--card-strong);
            border: 1px solid rgba(190,200,212,0.36);
            padding: 15px 16px;
        }

        .reason-advice-title {
            color: var(--text);
            font-size: 15px;
            font-weight: 700;
            margin-bottom: 8px;
        }

        .reason-advice-copy {
            color: var(--text);
            font-size: 12px;
            line-height: 1.55;
            margin-top: 7px;
        }

        .reason-advice-copy span {
            display: block;
            color: var(--accent);
            font-size: 11px;
            font-weight: 700;
            margin-bottom: 2px;
        }

        .insight-section-title {
            color: var(--text);
            font-size: 18px;
            font-weight: 700;
            margin: 1.25rem 0 0.75rem;
        }

        .insight-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
            margin-bottom: 1.35rem;
        }

        .insight-card {
            border-radius: var(--radius-lg);
            background: var(--card-strong);
            border: 1px solid rgba(190,200,212,0.32);
            padding: 16px 17px;
            min-height: 118px;
        }

        .insight-card span {
            color: var(--accent);
            font-size: 12px;
            font-weight: 800;
        }

        .insight-card p {
            color: var(--text);
            font-size: 14px;
            line-height: 1.58;
            margin: 8px 0 0;
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
            width: fit-content;
            max-width: 100%;
            overflow-x: auto;
            border-radius: 16px;
            border: 1px solid rgba(190, 200, 212, 0.32);
            box-shadow: none;
            background: var(--table-bg);
            margin: 0.9rem 0 1.6rem;
        }

        table.soft-table {
            width: max-content;
            min-width: max-content;
            table-layout: auto;
            border-collapse: collapse;
            background: var(--table-bg);
            border-right: 1px solid rgba(190, 200, 212, 0.38);
        }

        table.soft-table th,
        table.soft-table td {
            text-align: center !important;
            vertical-align: middle;
            padding: 14px 14px;
            border-bottom: 1px solid rgba(190, 200, 212, 0.30);
            border-right: 1px solid rgba(190, 200, 212, 0.18);
            color: var(--text);
            font-size: 13px;
            line-height: 1.45;
            white-space: nowrap;
        }

        table.soft-table th:last-child,
        table.soft-table td:last-child {
            border-right: 1px solid rgba(190, 200, 212, 0.42);
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
            background: #FBFCFE;
            font-size: 13px;
        }

        table.soft-table tr:hover td {
            background: rgba(102,126,234,0.045);
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
            .reason-advice-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .decision-action-grid,
            .insight-grid {
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
            .reason-advice-grid {
                grid-template-columns: 1fr;
            }
            .decision-action-grid,
            .insight-grid {
                grid-template-columns: 1fr;
            }
            .confidence-notice,
            .next-action-card,
            .workbench-toolbar {
                align-items: flex-start;
                flex-direction: column;
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
            workflow_meta = _get_order_workflow_meta(order_id)
            last_updated = workflow_meta.get("last_updated") or "尚未处理"
            owner = workflow_meta.get("owner") or "当前运营"
            support_confirmation = _safe_text(row.get("Amazon Support Confirmation")) or "未人工确认"
            case_status = "已开 Case" if operation_status == "已开Case" else "未开 Case"
            priority_label = _safe_text(row.get("Operation Priority")) or "P3"
            priority_action = _safe_text(row.get("Priority Action")) or "观察即可"
            evidence_html = _render_evidence_chips(row)
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
                f'{evidence_html}'
                '<div class="risk-workflow-meta">'
                f'<span>负责人：{escape(owner)}</span>'
                f'<span>{escape(case_status)}</span>'
                f'<span>人工确认：{escape(support_confirmation)}</span>'
                f'<span>最后处理：{escape(last_updated)}</span>'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            _render_quick_order_actions(row, order_id)

    if st.session_state.get("operation_clipboard_text"):
        label = st.session_state.get("operation_clipboard_label", "内容")
        with st.expander(f"已生成可复制{label}", expanded=True):
            st.code(st.session_state["operation_clipboard_text"], language="text")


def _render_evidence_chips(row: pd.Series) -> str:
    days = pd.to_numeric(pd.Series([row.get("Days Since Refund")]), errors="coerce").iloc[0]
    days_text = "退款天数待确认" if pd.isna(days) else f"已退款 {int(days)} 天"
    returns = "退货记录：已匹配" if _safe_text(row.get("Matched Returns")) == "是" else "退货记录：待确认"
    ledger = "入仓流水：已匹配" if _safe_text(row.get("Matched Ledger")) == "是" else "入仓流水：辅助待确认"
    reimbursement = _safe_text(row.get("Reimbursement Status")) or "赔偿记录：待确认"
    chips = [days_text, returns, ledger, reimbursement]
    return "<div class='evidence-chip-row'>" + "".join(
        f"<span>{escape(_shorten_text(chip, 18))}</span>" for chip in chips
    ) + "</div>"


def _render_quick_order_actions(row: pd.Series, order_id: str) -> None:
    col_copy, col_case, col_done, col_watch, col_more = st.columns([1, 1, 1, 1, 0.9])
    if col_copy.button("复制ID", key=f"quick_copy_order_{order_id}", width="stretch"):
        st.session_state["operation_clipboard_text"] = order_id
        st.session_state["operation_clipboard_label"] = "订单号"
    if col_case.button("复制文案", key=f"quick_copy_case_{order_id}", width="stretch"):
        st.session_state["operation_clipboard_text"] = build_amazon_case_text(row)
        st.session_state["operation_clipboard_label"] = "Case 文案"
    if col_done.button("已处理", key=f"quick_done_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "已核查")
    if col_watch.button("待跟进", key=f"quick_watch_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "待观察")
    with col_more:
        _render_order_action_menu(row, order_id)


def _render_order_action_menu(row: pd.Series, order_id: str) -> None:
    label = "更多"
    if hasattr(st, "popover"):
        with st.popover(label, width="stretch"):
            _render_order_action_buttons(row, order_id)
    else:
        with st.expander(label, expanded=False):
            _render_order_action_buttons(row, order_id)


def _render_order_action_buttons(row: pd.Series, order_id: str) -> None:
    st.caption("运营处理状态")
    if st.button("标记已处理", key=f"handled_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "已核查")
    if st.button("忽略", key=f"ignore_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "已忽略")
    if st.button("加入人工复核", key=f"review_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "人工复核")
    if st.button("待观察", key=f"watch_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "待观察")
    if st.button("已开Case", key=f"case_opened_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "已开Case")
    if st.button("已确认正常", key=f"normal_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "已确认正常")
    if st.button("恢复待处理", key=f"pending_{order_id}", width="stretch"):
        _set_order_operation_status(order_id, "待处理")
    st.divider()
    st.caption("Amazon Support 核查结果")
    if st.button("Amazon已确认退回", key=f"support_returned_{order_id}", width="stretch"):
        _set_support_confirmation(order_id, "Amazon已确认退回")
    if st.button("Amazon确认可售", key=f"support_sellable_{order_id}", width="stretch"):
        _set_support_confirmation(order_id, "Amazon确认可售")
    if st.button("Amazon确认不可售", key=f"support_unsellable_{order_id}", width="stretch"):
        _set_support_confirmation(order_id, "Amazon确认不可售")
    if st.button("Amazon确认未退回", key=f"support_not_returned_{order_id}", width="stretch"):
        _set_support_confirmation(order_id, "Amazon确认未退回")
    if st.button("Amazon拒绝赔偿", key=f"support_rejected_{order_id}", width="stretch"):
        _set_support_confirmation(order_id, "Amazon拒绝赔偿")
    if st.button("清除人工确认", key=f"support_clear_{order_id}", width="stretch"):
        confirmations = dict(st.session_state.get("support_confirmations", {}))
        confirmations.pop(order_id, None)
        st.session_state["support_confirmations"] = confirmations
        st.rerun()


def _set_order_operation_status(order_id: str, status: str) -> None:
    statuses = dict(st.session_state.get("order_operation_statuses", {}))
    statuses[order_id] = status
    st.session_state["order_operation_statuses"] = statuses
    meta = dict(st.session_state.get("order_operation_meta", {}))
    meta[order_id] = {
        **meta.get(order_id, {}),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "owner": meta.get(order_id, {}).get("owner", "当前运营"),
    }
    st.session_state["order_operation_meta"] = meta
    st.rerun()


def _set_support_confirmation(order_id: str, status: str) -> None:
    confirmations = dict(st.session_state.get("support_confirmations", {}))
    confirmations[order_id] = status
    st.session_state["support_confirmations"] = confirmations
    meta = dict(st.session_state.get("order_operation_meta", {}))
    meta[order_id] = {
        **meta.get(order_id, {}),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "owner": meta.get(order_id, {}).get("owner", "当前运营"),
    }
    st.session_state["order_operation_meta"] = meta
    st.rerun()


def _get_order_workflow_meta(order_id: str) -> dict[str, str]:
    meta = st.session_state.get("order_operation_meta", {})
    if not isinstance(meta, dict):
        return {}
    value = meta.get(order_id, {})
    return value if isinstance(value, dict) else {}


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
    _render_management_one_liner(analysis_df, top_reason, top_asin, suspicious_amount)
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.markdown(_mini_card("待确认订单", f"{len(review_orders):,}", f"真正高风险 {len(true_high):,} 单", "danger"), unsafe_allow_html=True)
    col_b.markdown(_mini_card("重点 ASIN", top_asin, "按未退回和退款金额排序", "warning"), unsafe_allow_html=True)
    col_c.markdown(_mini_card("待核查退款金额", f"${suspicious_amount:,.2f}", "待确认订单对应金额", "danger"), unsafe_allow_html=True)
    col_d.markdown(_mini_card("高频退货原因", top_reason, "当前样本最高频原因", "ok"), unsafe_allow_html=True)

    _render_ai_section_header("风险摘要", "先判断当前风险是否需要立即处理，以及重点从哪个 ASIN 切入。")
    first_cols = st.columns([1.08, 0.92], gap="large")
    with first_cols[0]:
        _render_report_panel("风险总结", _find_report_section(section_map, ["整体", "风险"]), "summary")
    with first_cols[1]:
        _render_report_panel("重点 ASIN", _find_report_section(section_map, ["ASIN", "重点"]), "focus")

    _render_ai_section_header("主要问题", "把退货风险拆成产品、Listing 和退货原因三条线，便于分工处理。")
    second_cols = st.columns(3, gap="large")
    with second_cols[0]:
        _render_report_panel("产品问题", _find_report_section(section_map, ["产品本身", "产品问题", "质量"]), "issue")
    with second_cols[1]:
        _render_report_panel("Listing 问题", _find_report_section(section_map, ["Listing", "图片", "标题", "五点", "A+"]), "issue")
    with second_cols[2]:
        _render_report_panel("高频退货原因", _find_report_section(section_map, ["主要退货原因", "退货原因"]), "issue")

    _render_ai_section_header("建议动作", "只保留可以直接执行的下一步，避免报告变成泛泛建议。")
    third_cols = st.columns(3, gap="large")
    with third_cols[0]:
        _render_report_panel("建议动作", _find_report_section(section_map, ["优化建议", "建议动作"]), "action")
    with third_cols[1]:
        _render_report_panel("开 Case 建议", _find_report_section(section_map, ["开 Case", "核查"]), "action")
    with third_cols[2]:
        _render_report_panel("索赔建议", _find_report_section(section_map, ["赔偿", "索赔", "客服", "售后"]), "action")

    with st.expander("查看完整 AI 报告原文", expanded=False):
        st.markdown(_clean_ai_markdown(report))


def _render_management_one_liner(
    analysis_df: pd.DataFrame,
    top_reason: str,
    top_asin: str,
    suspicious_amount: float,
) -> None:
    high_count = int(_series_or_default(analysis_df, "Risk Level", "").eq("High").sum())
    if high_count > 0:
        sentence = f"当前需重点控制 {high_count} 个真正高风险订单，待核查金额 ${suspicious_amount:,.2f}，优先从 {top_asin} 和「{top_reason}」切入。"
        tone = "warning"
    elif suspicious_amount > 0:
        sentence = f"当前主要任务不是直接索赔，而是人工确认待核查金额 ${suspicious_amount:,.2f} 对应订单的证据链。"
        tone = "neutral"
    else:
        sentence = f"当前没有明显高风险金额，建议把重点放在「{top_reason}」相关产品和 Listing 优化。"
        tone = "ok"
    st.markdown(
        f"""
        <div class="management-summary {tone}">
            <span>管理层一句话总结</span>
            <p>{escape(sentence)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_ai_section_header(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="ai-section-header">
            <h3>{escape(title)}</h3>
            <p>{escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_report_panel(title: str, body: str, variant: str = "issue") -> None:
    lines = _extract_report_points(body, max_lines=5)
    headline = lines[0] if len(lines) >= 1 else "当前数据不足以形成明确判断。"
    fields = [
        ("依据", lines[1] if len(lines) >= 2 else "需要结合退货报表、交易报表和人工核查结果继续确认。"),
        ("影响", lines[2] if len(lines) >= 3 else "影响范围暂不明确。"),
        ("下一步", lines[3] if len(lines) >= 4 else "先处理高金额、长周期、证据不足的订单。"),
    ]
    body_html = "".join(
        f"<div class='decision-line'><span>{escape(label)}</span><p>{escape(_concise_complete_text(text, 72))}</p></div>"
        for label, text in fields
    )
    st.markdown(
        f"""
        <div class="report-panel {escape(variant)}">
            <h4>{escape(title)}</h4>
            <div class="report-highlight">{escape(_concise_complete_text(headline, 82))}</div>
            {body_html}
            <div class="report-confidence">AI 辅助结论，需结合报表证据与人工核查。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _strip_report_label(text: str) -> str:
    return re.sub(r"^(问题|原因|影响|建议动作|行动|结论|判断|数据摘要|分析|建议)[:：]\s*", "", text).strip()


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


def _extract_report_points(body: str, max_lines: int = 6) -> list[str]:
    lines = [line.strip() for line in _clean_ai_markdown(body).splitlines() if line.strip()]
    if not lines:
        return []
    simplified: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        clean = _strip_report_label(_strip_inline_markdown(line.lstrip("- ").strip()))
        clean = _concise_complete_text(clean, 88)
        if clean and clean not in simplified:
            simplified.append(clean)
        if len(simplified) >= max_lines:
            break
    return simplified


def _concise_complete_text(text: str, max_length: int = 80) -> str:
    clean = _strip_inline_markdown(text)
    clean = re.sub(r"^\s*[•\-]\s*", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" ：:;；，,。")
    if not clean:
        return ""
    if len(clean) <= max_length:
        return _ensure_sentence_end(clean)

    sentences = re.split(r"(?<=[。！？!?])\s*", clean)
    for sentence in sentences:
        sentence = sentence.strip(" ：:;；，,")
        if sentence and len(sentence) <= max_length:
            return _ensure_sentence_end(sentence)

    clauses = re.split(r"[；;。！？!?]", clean)
    for clause in clauses:
        clause = clause.strip(" ：:，,")
        if 8 <= len(clause) <= max_length:
            return _ensure_sentence_end(clause)

    parts = [part.strip() for part in re.split(r"[，,、]", clean) if part.strip()]
    selected: list[str] = []
    for part in parts:
        candidate = "，".join(selected + [part])
        if len(candidate) > max_length:
            break
        selected.append(part)
    if selected:
        return _ensure_sentence_end("，".join(selected))

    return _ensure_sentence_end(clean[:max_length].rstrip(" ：:，,；;"))


def _ensure_sentence_end(text: str) -> str:
    clean = text.strip()
    if not clean:
        return ""
    if clean.endswith(("。", "！", "？", ".", "!", "?")):
        return clean
    return f"{clean}。"


def _top_asin_label(df: pd.DataFrame) -> str:
    if df is None or df.empty or "ASIN" not in df.columns:
        return "暂无"
    sorted_df = df.copy()
    sort_columns = [column for column in ["未退回订单数", "总退款金额", "退款订单数"] if column in sorted_df.columns]
    if sort_columns:
        sorted_df = sorted_df.sort_values(sort_columns, ascending=[False] * len(sort_columns))
    asin_values = sorted_df["ASIN"].fillna("").astype(str).str.strip()
    asin_values = asin_values[
        ~(asin_values.eq("") | asin_values.str.lower().isin({"nan", "none", "null", "-", "--", "<na>"}))
    ]
    if asin_values.empty:
        return "暂无明确 ASIN"
    return asin_values.iloc[0]


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
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
    clean = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", clean)
    clean = clean.replace("**", "").replace("__", "").replace("*", "")
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
