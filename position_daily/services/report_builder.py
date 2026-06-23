import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .charts import build_industry_charts, build_wind_charts
from .config import STRATEGY_TAG
from .industry_analysis import analyze_industry
from .position import load_position
from .stock_contribution import analyze_stock_contribution
from .style_analysis import analyze_style
from .wind_analysis import (
    analyze_citics_industry,
    analyze_consensus,
    analyze_theme,
    analyze_valuation,
)

logger = logging.getLogger("position_daily.report")

PROFIT_COL_LABEL = "累计盈亏"
INDUSTRY_PCT_COLS = ["w_chg_pct", "indus_pct_chg", "excess_pct"]
INDUSTRY_WEIGHT_COLS = ["weight"]
INDUSTRY_TABLE_COLS = [
    "indus_code",
    "indus_name",
    "stock_count",
    "weight",
    "w_chg_pct",
    "indus_pct_chg",
    "excess_pct",
    "profit",
]

STYLE_PCT_COLS = ["w_chg_pct", "index_pct_chg", "excess_pct"]
THEME_PCT_COLS = ["w_chg_pct"]
STOCK_PCT_COLS = ["weight", "change_pct", "daily_contrib_pct"]
STOCK_NUM_COLS = ["daily_contrib", "profit", "market_value"]


def _pct(x, digits=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{float(x):.{digits}f}%"


def _num(x, digits=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{float(x):,.{digits}f}"


def _df_to_records(df: pd.DataFrame, pct_cols=None, weight_cols=None) -> list[dict]:
    if df is None or df.empty:
        return []
    pct_cols = pct_cols or []
    weight_cols = weight_cols or []
    rows = []
    for _, r in df.iterrows():
        item = {}
        for c in df.columns:
            v = r[c]
            if c in weight_cols and pd.notna(v):
                item[c] = f"{float(v) * 100:.2f}%"
            elif c in pct_cols and pd.notna(v):
                item[c] = f"{float(v):.2f}%"
            elif c in pct_cols:
                item[c] = "—"
            elif isinstance(v, float):
                item[c] = round(v, 4) if abs(v) < 1000 else round(v, 2)
            else:
                item[c] = v
        rows.append(item)
    return rows


def _sort_val(v, key: str):
    if pd.isna(v):
        return ""
    if key in ("indus_code", "indus_name", "theme_code", "theme_name", "bucket", "code", "name", "bench_code"):
        return str(v)
    if key == "stock_count":
        return int(v)
    return float(v)


def _build_sortable_table(
    df: pd.DataFrame,
    columns: list[tuple[str, str]],
    *,
    pct_cols=None,
    weight_cols=None,
    default_sort: str = "weight",
) -> tuple[list[dict], list[tuple[str, str]]]:
    if df is None or df.empty:
        return [], columns
    keys = [k for k, _ in columns]
    rows = []
    for _, r in df.iterrows():
        display = _df_to_records(
            pd.DataFrame([r]),
            pct_cols=pct_cols,
            weight_cols=weight_cols,
        )[0]
        sort = {k: _sort_val(r[k], k) for k in keys if k in r.index}
        rows.append({"display": display, "sort": sort, "default_sort": default_sort})
    return rows, columns


def _build_industry_all(industry_df: pd.DataFrame) -> list[dict]:
    rows, _ = _build_sortable_table(
        industry_df,
        [(k, l) for k, l in zip(INDUSTRY_TABLE_COLS, ["代码", "行业", "只数", "权重", "持仓涨跌", "行业涨跌", "超额", PROFIT_COL_LABEL])],
        pct_cols=INDUSTRY_PCT_COLS,
        weight_cols=INDUSTRY_WEIGHT_COLS,
    )
    return rows


@dataclass
class DailyReportContext:
    trade_date: str
    strategy_tag: str
    generated_at: str
    summary: dict
    meta: dict
    industry_all: list[dict] = field(default_factory=list)
    industry_count: int = 0
    industry_columns: list[tuple[str, str]] = field(default_factory=list)
    top_good: list[dict] = field(default_factory=list)
    top_bad: list[dict] = field(default_factory=list)
    unmapped_industry: list[dict] = field(default_factory=list)
    # Wind 扩展
    valuation: dict = field(default_factory=dict)
    moneyflow: dict = field(default_factory=dict)
    consensus: dict = field(default_factory=dict)
    citics_all: list[dict] = field(default_factory=list)
    citics_count: int = 0
    citics_columns: list[tuple[str, str]] = field(default_factory=list)
    citics_top_good: list[dict] = field(default_factory=list)
    citics_top_bad: list[dict] = field(default_factory=list)
    unmapped_citics: list[dict] = field(default_factory=list)
    theme_all: list[dict] = field(default_factory=list)
    theme_count: int = 0
    theme_columns: list[tuple[str, str]] = field(default_factory=list)
    style_buckets: list[dict] = field(default_factory=list)
    style_columns: list[tuple[str, str]] = field(default_factory=list)
    mv_style: list[dict] = field(default_factory=list)
    mv_style_columns: list[tuple[str, str]] = field(default_factory=list)
    stock_top_gain: list[dict] = field(default_factory=list)
    stock_top_loss: list[dict] = field(default_factory=list)
    stock_contrib_columns: list[tuple[str, str]] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    chart_industry_weight: str = ""
    chart_industry_excess: str = ""
    chart_citics_weight: str = ""
    chart_citics_excess: str = ""
    chart_theme_weight: str = ""
    chart_style_weight: str = ""
    error: str | None = None
    debug_log: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def build_daily_report(trade_date: str, *, strategy_tag: str | None = None) -> DailyReportContext:
    tag = (strategy_tag or STRATEGY_TAG).strip()
    t0 = time.perf_counter()
    ctx = DailyReportContext(
        trade_date=trade_date,
        strategy_tag=tag,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        summary={},
        meta={},
    )

    def _log(msg: str):
        ctx.debug_log.append(msg)
        logger.info("[%s] %s", trade_date, msg)

    try:
        _log("加载持仓...")
        t1 = time.perf_counter()
        pos_df, summary, meta = load_position(trade_date, strategy_tag=tag)
        _log(f"持仓 {len(pos_df)} 只 ({(time.perf_counter()-t1)*1000:.0f}ms)")

        _log("分析申万二级 rq_daily_indusSWL2_price...")
        t1 = time.perf_counter()
        industry_df, top_good, top_bad, unmapped_ind, ind_quality = analyze_industry(
            pos_df, trade_date
        )
        _log(f"行业 {len(industry_df)} 个 ({(time.perf_counter()-t1)*1000:.0f}ms)")

        ctx.summary = summary
        ctx.meta = meta
        ctx.quality = dict(ind_quality)
        ctx.industry_count = len(industry_df)
        ctx.industry_columns = [
            ("indus_code", "代码"),
            ("indus_name", "行业"),
            ("stock_count", "只数"),
            ("weight", "权重"),
            ("w_chg_pct", "持仓涨跌"),
            ("indus_pct_chg", "行业涨跌"),
            ("excess_pct", "超额"),
            ("profit", PROFIT_COL_LABEL),
        ]
        ctx.industry_all = _build_industry_all(industry_df)

        good_cols = ["indus_name", "weight", "w_chg_pct", "indus_pct_chg", "excess_pct"]
        ctx.top_good = _df_to_records(
            top_good[good_cols] if not top_good.empty else top_good,
            pct_cols=INDUSTRY_PCT_COLS,
            weight_cols=INDUSTRY_WEIGHT_COLS,
        )
        ctx.top_bad = _df_to_records(
            top_bad[good_cols] if not top_bad.empty else top_bad,
            pct_cols=INDUSTRY_PCT_COLS,
            weight_cols=INDUSTRY_WEIGHT_COLS,
        )
        if not unmapped_ind.empty:
            ctx.unmapped_industry = unmapped_ind.sort_values("market_value", ascending=False).head(20).to_dict("records")

        # --- Wind 分析（各模块独立，单模块失败不阻断整份报告）---
        def _run_wind(label, fn, *args, **kwargs):
            try:
                t1 = time.perf_counter()
                result = fn(*args, **kwargs)
                _log(f"{label} ({(time.perf_counter()-t1)*1000:.0f}ms)")
                return result
            except Exception as ex:
                _log(f"{label} 失败: {ex}")
                logger.exception("[%s] %s", trade_date, label)
                return None

        _log("Wind 估值暴露...")
        val_res = _run_wind("估值", analyze_valuation, pos_df, trade_date)
        if val_res:
            ctx.valuation, val_q = val_res
            ctx.quality.update(val_q)

        _log("Wind 盈利预期...")
        cons_res = _run_wind("一致预期", analyze_consensus, pos_df, trade_date)
        if cons_res:
            ctx.consensus, cons_q = cons_res
            ctx.quality.update(cons_q)

        _log("中信行业...")
        citics_res = _run_wind("中信", analyze_citics_industry, pos_df, trade_date)
        unmapped_citics = pd.DataFrame()
        if citics_res:
            citics_df, unmapped_citics, citics_q = citics_res
            ctx.quality.update(citics_q)
        else:
            citics_df = pd.DataFrame()
        ctx.citics_count = len(citics_df)
        ctx.citics_columns = [
            ("indus_code", "代码"),
            ("indus_name", "行业"),
            ("stock_count", "只数"),
            ("weight", "权重"),
            ("w_chg_pct", "持仓涨跌"),
            ("indus_pct_chg", "行业涨跌"),
            ("excess_pct", "超额"),
            ("profit", PROFIT_COL_LABEL),
        ]
        ctx.citics_all, _ = _build_sortable_table(
            citics_df, ctx.citics_columns,
            pct_cols=INDUSTRY_PCT_COLS, weight_cols=INDUSTRY_WEIGHT_COLS,
        )
        if not citics_df.empty:
            ctx.citics_top_good = _df_to_records(
                citics_df.nlargest(5, "excess_pct")[good_cols],
                pct_cols=INDUSTRY_PCT_COLS, weight_cols=INDUSTRY_WEIGHT_COLS,
            )
            ctx.citics_top_bad = _df_to_records(
                citics_df.nsmallest(5, "excess_pct")[good_cols],
                pct_cols=INDUSTRY_PCT_COLS, weight_cols=INDUSTRY_WEIGHT_COLS,
            )
        if not unmapped_citics.empty:
            ctx.unmapped_citics = unmapped_citics.sort_values("market_value", ascending=False).head(20).to_dict("records")

        _log("主题/概念暴露...")
        theme_res = _run_wind("主题", analyze_theme, pos_df, trade_date)
        if theme_res:
            theme_df, theme_q = theme_res
            ctx.quality.update(theme_q)
        else:
            theme_df = pd.DataFrame()
        ctx.theme_count = len(theme_df)
        ctx.theme_columns = [
            ("theme_code", "代码"),
            ("theme_name", "主题"),
            ("stock_count", "只数"),
            ("weight", "权重"),
            ("w_chg_pct", "持仓涨跌"),
            ("profit", PROFIT_COL_LABEL),
        ]
        ctx.theme_all, _ = _build_sortable_table(
            theme_df, ctx.theme_columns,
            pct_cols=THEME_PCT_COLS, weight_cols=INDUSTRY_WEIGHT_COLS,
        )

        _log("宽基风格 rq_base_index + Wind 指数...")
        style_res = _run_wind("风格", analyze_style, pos_df, trade_date)
        if style_res:
            style_df, mv_df, style_q = style_res
            ctx.quality.update(style_q)
        else:
            style_df, mv_df = pd.DataFrame(), pd.DataFrame()
        ctx.style_columns = [
            ("bucket", "板块"),
            ("stock_count", "只数"),
            ("weight", "权重"),
            ("w_chg_pct", "持仓涨跌"),
            ("index_pct_chg", "指数涨跌"),
            ("excess_pct", "超额"),
            ("bench_code", "基准代码"),
            ("profit", PROFIT_COL_LABEL),
        ]
        ctx.style_buckets, _ = _build_sortable_table(
            style_df, ctx.style_columns,
            pct_cols=STYLE_PCT_COLS, weight_cols=INDUSTRY_WEIGHT_COLS,
            default_sort="bucket",
        )
        ctx.mv_style_columns = [
            ("bucket", "风格"),
            ("stock_count", "只数"),
            ("weight", "权重"),
            ("w_chg_pct", "持仓涨跌"),
            ("profit", PROFIT_COL_LABEL),
        ]
        ctx.mv_style, _ = _build_sortable_table(
            mv_df, ctx.mv_style_columns,
            pct_cols=["w_chg_pct"], weight_cols=INDUSTRY_WEIGHT_COLS,
            default_sort="bucket",
        )

        _log("单票贡献...")
        t1 = time.perf_counter()
        top_gain, top_loss, contrib_q = analyze_stock_contribution(pos_df)
        ctx.quality.update(contrib_q)
        ctx.stock_contrib_columns = [
            ("code", "代码"),
            ("name", "名称"),
            ("weight", "权重"),
            ("change_pct", "涨跌"),
            ("daily_contrib", "当日贡献"),
            ("daily_contrib_pct", "贡献占比"),
            ("profit", PROFIT_COL_LABEL),
        ]
        ctx.stock_top_gain = _df_to_records(
            top_gain,
            pct_cols=["change_pct", "daily_contrib_pct"],
            weight_cols=["weight"],
        )
        ctx.stock_top_loss = _df_to_records(
            top_loss,
            pct_cols=["change_pct", "daily_contrib_pct"],
            weight_cols=["weight"],
        )
        _log(f"单票 ({(time.perf_counter()-t1)*1000:.0f}ms)")

        _log("生成 Plotly 图表...")
        t1 = time.perf_counter()
        charts = build_industry_charts(industry_df)
        ctx.chart_industry_weight = charts["chart_industry_weight"]
        ctx.chart_industry_excess = charts["chart_industry_excess"]
        wind_charts = build_wind_charts(citics_df, theme_df, style_df)
        ctx.chart_citics_weight = wind_charts.get("chart_citics_weight", "")
        ctx.chart_citics_excess = wind_charts.get("chart_citics_excess", "")
        ctx.chart_theme_weight = wind_charts.get("chart_theme_weight", "")
        ctx.chart_style_weight = wind_charts.get("chart_style_weight", "")
        _log(f"图表 ({(time.perf_counter()-t1)*1000:.0f}ms)")

    except Exception as e:
        ctx.error = str(e)
        ctx.debug_log.append(f"ERROR: {e}")
        logger.error("[%s] 报告失败: %s\n%s", trade_date, e, traceback.format_exc())

    ctx.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    ctx.debug_log.append(f"总耗时 {ctx.elapsed_ms}ms")
    logger.info("[%s] 总耗时 %dms", trade_date, ctx.elapsed_ms)
    return ctx


def render_markdown(ctx: DailyReportContext) -> str:
    if ctx.error:
        return f"# 日度持仓分析报告\n\n错误：{ctx.error}\n"

    s = ctx.summary
    v = ctx.valuation
    cs = ctx.consensus
    lines = [
        "# 日度持仓分析报告",
        "",
        f"- 策略：{ctx.strategy_tag}",
        f"- 快照日期：{ctx.trade_date}",
        f"- 生成时间：{ctx.generated_at}",
        "",
        "## 一、账户概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 持仓只数 | {s.get('count', '—')} |",
        f"| 股票市值 | {_num(s.get('total_market_value'))} |",
        f"| 等权平均涨跌 | {_pct(s.get('avg_change_pct'))} |",
        f"| 上涨/下跌/平盘 | {s.get('up_count', '—')}/{s.get('down_count', '—')}/{s.get('flat_count', '—')} |",
        f"| 浮动盈亏 | {_num(s.get('total_profit'))} |",
        "",
        "## 二、申万二级行业（rq_daily_indusSWL2_price）",
        "",
        f"共 {ctx.industry_count} 个行业，详见 Web 报告。",
        "",
        "## 三、Wind 估值暴露",
        "",
        f"| 加权 PE(TTM) | {_num(v.get('w_pe_ttm'))} |",
        f"| 加权 PB | {_num(v.get('w_pb'))} |",
        f"| 加权市值(亿) | {_num(v.get('w_mv_yi'))} |",
        f"| 加权市值分位 | {_pct(v.get('w_mv_pct'))} |",
        "",
        "## 四、Wind 盈利预期（FY1）",
        "",
        f"| 加权预期 PE | {_num(cs.get('w_est_pe'))} |",
        f"| 覆盖度 | {_pct(cs.get('coverage_pct', 0) * 100 if cs.get('coverage_pct') else None)} |",
        "",
        "## 五、中信行业",
        "",
        f"共 {ctx.citics_count} 个二级行业。",
        "",
        "## 六、主题/概念",
        "",
        f"共 {ctx.theme_count} 个主题暴露。",
        "",
        "## 七、宽基风格",
        "",
        "rq_base_index 成分 + Wind AINDEXEODPRICES 基准。",
        "",
        "## 八、单票贡献",
        "",
        f"当日组合盈亏约 {_num(ctx.quality.get('daily_pnl'))}。",
        "",
        "## 九、数据质量",
        "",
        f"- 未映射申万二级：{ctx.quality.get('indus_unmapped', 0)} 只",
        f"- Wind 估值日期：{ctx.quality.get('wind_deriv_date', '—')}",
    ]
    return "\n".join(lines)
