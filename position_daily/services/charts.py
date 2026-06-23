# A 股配色：红涨绿跌
_CN_UP = "#c92a2a"
_CN_DOWN = "#2b8a3e"

import pandas as pd

from plotly import graph_objs as go


def _cn_bar_colors(values) -> list[str]:
    return [_CN_UP if (x or 0) >= 0 else _CN_DOWN for x in values]





def _fig_html(fig, include_js: str | bool = False) -> str:

    return fig.to_html(

        full_html=False,

        include_plotlyjs=include_js,

        config={"displayModeBar": False},

    )





def chart_industry_weight(industry_df: pd.DataFrame, top_n: int = 15) -> str:

    if industry_df.empty:

        return ""

    df = industry_df.nlargest(top_n, "weight").sort_values("weight")

    df["weight_pct"] = df["weight"] * 100

    colors = _cn_bar_colors(df["w_chg_pct"])

    fig = go.Figure(

        data=[

            go.Bar(

                y=df["indus_name"],

                x=df["weight_pct"],

                orientation="h",

                marker_color=colors,

                text=[f"{v:.2f}%" for v in df["weight_pct"]],

                textposition="outside",

            )

        ],

        layout={

            "title": f"申万二级行业权重 Top {top_n}",

            "height": max(420, top_n * 28),

            "xaxis_title": "市值权重 (%)",

            "margin": {"l": 120, "t": 50, "r": 40},

        },

    )

    return _fig_html(fig, include_js=False)





def chart_industry_excess(industry_df: pd.DataFrame, top_n: int = 15) -> str:

    if industry_df.empty:

        return ""

    df = industry_df.nlargest(top_n, "weight").copy()

    df = df.sort_values("excess_pct")

    colors = _cn_bar_colors(df["excess_pct"])

    fig = go.Figure(

        data=[

            go.Bar(

                y=df["indus_name"],

                x=df["excess_pct"],

                orientation="h",

                marker_color=colors,

                text=[f"{x:.2f}%" if pd.notna(x) else "—" for x in df["excess_pct"]],

                textposition="outside",

            )

        ],

        layout={

            "title": f"重仓行业相对超额（权重 Top {top_n}）",

            "height": max(420, top_n * 28),

            "xaxis_title": "相对行业超额 (%)",

            "margin": {"l": 120, "t": 50, "r": 40},

        },

    )

    return _fig_html(fig, include_js=False)





def build_industry_charts(industry_df: pd.DataFrame) -> dict[str, str]:

    return {

        "chart_industry_weight": chart_industry_weight(industry_df),

        "chart_industry_excess": chart_industry_excess(industry_df),

    }


def chart_generic_weight(df: pd.DataFrame, name_col: str, title: str, top_n: int = 12) -> str:
    if df is None or df.empty:
        return ""
    d = df.nlargest(top_n, "weight").sort_values("weight")
    d = d.copy()
    d["weight_pct"] = d["weight"] * 100
    colors = _cn_bar_colors(d.get("w_chg_pct", [0] * len(d)))
    fig = go.Figure(
        data=[
            go.Bar(
                y=d[name_col],
                x=d["weight_pct"],
                orientation="h",
                marker_color=colors,
                text=[f"{v:.2f}%" for v in d["weight_pct"]],
                textposition="outside",
            )
        ],
        layout={
            "title": title,
            "height": max(380, top_n * 26),
            "xaxis_title": "暴露权重 (%)",
            "margin": {"l": 140, "t": 50, "r": 40},
        },
    )
    return _fig_html(fig, include_js=False)


def chart_generic_excess(df: pd.DataFrame, name_col: str, title: str, top_n: int = 12) -> str:
    if df is None or df.empty or "excess_pct" not in df.columns:
        return ""
    d = df.nlargest(top_n, "weight").copy().sort_values("excess_pct")
    colors = _cn_bar_colors(d["excess_pct"])
    fig = go.Figure(
        data=[
            go.Bar(
                y=d[name_col],
                x=d["excess_pct"],
                orientation="h",
                marker_color=colors,
                text=[f"{x:.2f}%" if pd.notna(x) else "—" for x in d["excess_pct"]],
                textposition="outside",
            )
        ],
        layout={
            "title": title,
            "height": max(380, top_n * 26),
            "xaxis_title": "超额 (%)",
            "margin": {"l": 140, "t": 50, "r": 40},
        },
    )
    return _fig_html(fig, include_js=False)


def build_wind_charts(citics_df, theme_df, style_df) -> dict[str, str]:
    style_chart = ""
    if style_df is not None and not style_df.empty:
        s = style_df.rename(columns={"bucket": "label"})
        style_chart = chart_generic_weight(s, "label", "宽基板块权重", top_n=8)
    return {
        "chart_citics_weight": chart_generic_weight(citics_df, "indus_name", "中信二级行业权重 Top 12"),
        "chart_citics_excess": chart_generic_excess(citics_df, "indus_name", "中信行业重仓相对超额 Top 12"),
        "chart_theme_weight": chart_generic_weight(theme_df, "theme_name", "主题暴露 Top 12"),
        "chart_style_weight": style_chart,
    }


