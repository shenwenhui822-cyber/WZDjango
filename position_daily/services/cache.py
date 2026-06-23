import logging
import pickle
from pathlib import Path

from .config import STRATEGY_TAG
from .report_builder import DailyReportContext, build_daily_report

logger = logging.getLogger("position_daily.cache")

CACHE_DIR = Path(__file__).resolve().parents[2] / "reports" / "cache"


def _cache_path(trade_date: str, *, strategy_tag: str | None = None) -> Path:
    tag = (strategy_tag or STRATEGY_TAG).strip()
    return CACHE_DIR / tag / f"{trade_date}.pkl"


def load_cached_report(trade_date: str, *, strategy_tag: str | None = None) -> DailyReportContext | None:
    path = _cache_path(trade_date, strategy_tag=strategy_tag)
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            ctx = pickle.load(f)
        logger.info("缓存命中 date=%s file=%s", trade_date, path)
        return ctx
    except Exception:
        logger.exception("读取缓存失败 date=%s", trade_date)
        path.unlink(missing_ok=True)
        return None


def save_cached_report(ctx: DailyReportContext) -> None:
    if ctx.error:
        return
    path = _cache_path(ctx.trade_date, strategy_tag=ctx.strategy_tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(ctx, f)
    logger.info("已写入缓存 date=%s", ctx.trade_date)


def _cache_valid(ctx: DailyReportContext) -> bool:
    return (
        hasattr(ctx, "industry_all")
        and isinstance(ctx.industry_all, list)
        and hasattr(ctx, "valuation")
        and isinstance(ctx.valuation, dict)
        and any(k == "daily_pnl" for k, _ in getattr(ctx, "industry_columns", []))
    )


def get_daily_report(
    trade_date: str,
    *,
    strategy_tag: str | None = None,
    force_refresh: bool = False,
) -> tuple[DailyReportContext, bool]:
    """返回 (context, from_cache)。"""
    tag = (strategy_tag or STRATEGY_TAG).strip()
    if not force_refresh:
        cached = load_cached_report(trade_date, strategy_tag=tag)
        if cached is not None and _cache_valid(cached):
            cached.debug_log = list(cached.debug_log) + ["（服务端缓存命中，未重新计算）"]
            return cached, True
        if cached is not None:
            logger.info("缓存格式已过期 date=%s tag=%s，将重新计算", trade_date, tag)

    ctx = build_daily_report(trade_date, strategy_tag=tag)
    if not ctx.error:
        save_cached_report(ctx)
        ctx.debug_log.append("（已重新计算并更新缓存）")
    else:
        ctx.debug_log.append("（计算失败，未写入缓存）")
    return ctx, False
