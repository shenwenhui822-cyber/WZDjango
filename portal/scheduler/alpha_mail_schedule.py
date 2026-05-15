"""
alpha_mail_scheduler 的默认任务表与环境开关过滤。

调度时间格式为 HH:MM；kwargs 可含 scheduler_job_key（仅写入 MAIL_LOGS，不传给 manage.py）。
"""
from __future__ import annotations

import os
from typing import Any

# (HH:MM, management command name, kwargs)
# 邮件类任务在各自命令内校验「查询日～运行日」闭区间交易日个数 ≤ MAIL_JOB_MAX_TRADING_DAY_SPAN（默认 3）。
# auto_import_qichat_t0_mail：IMAP 动态主题拉取上周 CSV + 入库（仅调度：每周首个交易日，见 alpha_mail_scheduler._should_skip_scheduled_job）。
DEFAULT_MAIL_SCHEDULER_SCHEDULES: list[tuple[str, str, dict[str, Any]]] = [
    ("09:00", "auto_import_htzq_ht1_capital_mail", {"scheduler_job_key": "htzq_ht1_capital"}),
    ("09:30", "update_rq_bench", {"scheduler_job_key": "rq_bench"}),
    ("09:31", "auto_import_fund_nav_mail", {"scheduler_job_key": "fund_nav_boshiyihao"}),
    ("09:33", "auto_import_ghzq_settle_mail", {"scheduler_job_key": "ghzq_settle"}),
    ("11:10", "auto_import_ysh_nav_mail", {"scheduler_job_key": "ysh_nav"}),
    ("11:30", "auto_import_llh_nav_mail", {"scheduler_job_key": "llh_nav"}),
    ("11:35", "auto_import_dyyh_nav_mail", {"scheduler_job_key": "dyyh_nav"}),
    ("11:40", "auto_import_jlh_nav_mail", {"scheduler_job_key": "jlh_nav"}),
    ("11:50", "auto_import_ctayh_nav_mail", {"scheduler_job_key": "ctayh_nav"}),
    ("12:00", "auto_import_zxdw_nav_mail", {"scheduler_job_key": "zxdw_nav"}),
    ("12:10", "auto_import_dylx_nav_mail", {"scheduler_job_key": "dylx_nav"}),
    ("14:10", "auto_import_dyctayh_nav_mail", {"scheduler_job_key": "dyctayh_nav"}),
    ("14:30", "auto_import_ylh_nav_mail", {"scheduler_job_key": "ylh_nav"}),
    ("18:05", "sync_t0_performance", {"scheduler_job_key": "sync_t0_ftp"}),
    ("17:10", "auto_import_wz_lyh_nav_mail", {"scheduler_job_key": "wz_lyh_nav"}),
    ("17:20", "auto_import_slh_nav_mail", {"scheduler_job_key": "slh_nav"}),
    ("18:00", "auto_import_wkqh_settle_mail", {"scheduler_job_key": "wkqh_settle"}),
    ("16:05", "auto_import_cjqh_settle_mail", {"scheduler_job_key": "cjqh_settle"}),
    ("18:10", "auto_import_stz053_nav_mail", {"scheduler_job_key": "stz053_nav"}),
    ("19:00", "auto_import_htqh_settle_mail", {"scheduler_job_key": "htqh_settle"}),
    ("19:40", "auto_import_dwyh_nav_mail", {"scheduler_job_key": "dwyh_nav"}),
    ("20:00", "auto_import_qichat_t0_mail", {"scheduler_job_key": "qichat_t0_weekly"}),
    ("21:00", "auto_import_alpha_mail", {"scheduler_job_key": "alpha_mail"}),
]


def _env_enabled(env_name: str, *, default: str = "1") -> bool:
    return os.getenv(env_name, default).strip() not in ("0", "false", "False")


def _htzq_ht1_capital_mail_enabled() -> bool:
    return _env_enabled("HTZQ_HT1_CAPITAL_MAIL_SCHEDULER_ENABLED")


def _fund_nav_enabled() -> bool:
    return _env_enabled("FUND_NAV_SCHEDULER_ENABLED")


def _rq_bench_enabled() -> bool:
    return _env_enabled("RQ_BENCH_SCHEDULER_ENABLED")


def _zxdw_nav_mail_enabled() -> bool:
    return _env_enabled("ZXDW_NAV_MAIL_SCHEDULER_ENABLED")


def _wkqh_settle_mail_enabled() -> bool:
    return _env_enabled("WKQH_SETTLE_MAIL_SCHEDULER_ENABLED")


def _cjqh_settle_mail_enabled() -> bool:
    return _env_enabled("CJQH_SETTLE_MAIL_SCHEDULER_ENABLED")


def _ghzq_settle_mail_enabled() -> bool:
    return _env_enabled("GHZQ_SETTLE_MAIL_SCHEDULER_ENABLED")


def _htqh_settle_mail_enabled() -> bool:
    return _env_enabled("HTQH_SETTLE_MAIL_SCHEDULER_ENABLED")


def _stz053_nav_mail_enabled() -> bool:
    return _env_enabled("STZ053_NAV_MAIL_SCHEDULER_ENABLED")


def _slh_nav_mail_enabled() -> bool:
    return _env_enabled("SLH_NAV_MAIL_SCHEDULER_ENABLED")


def _dyyh_nav_mail_enabled() -> bool:
    return _env_enabled("DYYH_NAV_MAIL_SCHEDULER_ENABLED")


def _dylx_nav_mail_enabled() -> bool:
    return _env_enabled("DYLX_NAV_MAIL_SCHEDULER_ENABLED")


def _ysh_nav_mail_enabled() -> bool:
    return _env_enabled("YSH_NAV_MAIL_SCHEDULER_ENABLED")


def _llh_nav_mail_enabled() -> bool:
    return _env_enabled("LLH_NAV_MAIL_SCHEDULER_ENABLED")


def _jlh_nav_mail_enabled() -> bool:
    return _env_enabled("JLH_NAV_MAIL_SCHEDULER_ENABLED")


def _ylh_nav_mail_enabled() -> bool:
    return _env_enabled("YLH_NAV_MAIL_SCHEDULER_ENABLED")


def _wz_lyh_nav_mail_enabled() -> bool:
    return _env_enabled("WZ_LYH_NAV_MAIL_SCHEDULER_ENABLED")


def _ctayh_nav_mail_enabled() -> bool:
    return _env_enabled("CTAYH_NAV_MAIL_SCHEDULER_ENABLED")


def _dyctayh_nav_mail_enabled() -> bool:
    return _env_enabled("DYCTAYH_NAV_MAIL_SCHEDULER_ENABLED")


def _dwyh_nav_mail_enabled() -> bool:
    return _env_enabled("DWYH_NAV_MAIL_SCHEDULER_ENABLED")


def _t0_ftp_sync_enabled() -> bool:
    return _env_enabled("T0_FTP_SYNC_SCHEDULER_ENABLED")


def _t0_qichat_weekly_sync_enabled() -> bool:
    return _env_enabled("T0_QICHAT_WEEKLY_SCHEDULER_ENABLED")


def get_active_mail_scheduler_schedules() -> list[tuple[str, str, dict[str, Any]]]:
    """按环境变量剔除关闭项后的当日调度表（顺序与时间即 DEFAULT_MAIL_SCHEDULER_SCHEDULES）。"""
    s = list(DEFAULT_MAIL_SCHEDULER_SCHEDULES)
    if not _htzq_ht1_capital_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_htzq_ht1_capital_mail"]
    if not _fund_nav_enabled():
        s = [x for x in s if x[1] != "auto_import_fund_nav_mail"]
    if not _rq_bench_enabled():
        s = [x for x in s if x[1] != "update_rq_bench"]
    if not _zxdw_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_zxdw_nav_mail"]
    if not _wkqh_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_wkqh_settle_mail"]
    if not _cjqh_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_cjqh_settle_mail"]
    if not _ghzq_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ghzq_settle_mail"]
    if not _htqh_settle_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_htqh_settle_mail"]
    if not _stz053_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_stz053_nav_mail"]
    if not _slh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_slh_nav_mail"]
    if not _dyyh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dyyh_nav_mail"]
    if not _dylx_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dylx_nav_mail"]
    if not _ysh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ysh_nav_mail"]
    if not _llh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_llh_nav_mail"]
    if not _jlh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_jlh_nav_mail"]
    if not _ctayh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ctayh_nav_mail"]
    if not _dyctayh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dyctayh_nav_mail"]
    if not _dwyh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_dwyh_nav_mail"]
    if not _ylh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_ylh_nav_mail"]
    if not _wz_lyh_nav_mail_enabled():
        s = [x for x in s if x[1] != "auto_import_wz_lyh_nav_mail"]
    if not _t0_ftp_sync_enabled():
        s = [x for x in s if x[1] != "sync_t0_performance"]
    if not _t0_qichat_weekly_sync_enabled():
        s = [x for x in s if x[1] != "auto_import_qichat_t0_mail"]
    return s
