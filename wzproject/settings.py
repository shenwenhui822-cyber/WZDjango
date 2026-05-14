"""
Django settings for WZDjango (Alpha data portal).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "django-insecure-change-me-in-production-wzdjango"

DEBUG = True

# Alpha 日报页：为 True 时，勾选「调试信息」或附加 ?debug=1 可查看 Mongo 查询与耗时（生产环境请设为 False）
ALPHA_DAILY_PAGE_DEBUG = DEBUG
# 基金净值页：为 True 时可在页面勾选「调试信息」查看 Mongo 查询片段等（生产建议 False）
FUND_NAV_PAGE_DEBUG = DEBUG

ALLOWED_HOSTS = ["*", "localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "portal",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "wzproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "wzproject.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 登录/登出跳转
LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/nav/raw/"
LOGOUT_REDIRECT_URL = "/"

# 跨域与 CSRF（便于前端通过 http://<host>:7443 访问）
CORS_ALLOW_ALL_ORIGINS = True
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:7443",
    "http://localhost:7443",
]

# MongoDB：命名约定为小写 + 下划线
# Alpha 日报落库：库 alpha_product，集合 alpha_sim_nav（与同事约定一致）
# MONGODB_URI = "mongodb://127.0.0.1:27017/"
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/")
MONGODB_DB_NAME = "alpha_product"
MONGODB_COLLECTION_NAME = "alpha_sim_nav"
# 博士一号真实净值：独立库 fund_nav_real；集合名 = 下方逻辑产品编码
MONGODB_FUND_NAV_REAL_DB = "fund_nav_real"
# alpha_mail_scheduler：每次定时任务结束后写入 mail_logs.MAIL_LOGS（log_type/import_succeeded/failure_reason/target_* 等；每次执行插入一条），并按 ALPHA_NOTIFY_* 发送汇总邮件（可配关闭）
MONGODB_MAIL_LOGS_DB = os.getenv("MONGODB_MAIL_LOGS_DB", "mail_logs")
MONGODB_MAIL_LOGS_COLLECTION = os.getenv("MONGODB_MAIL_LOGS_COLLECTION", "MAIL_LOGS")
SCHEDULER_MAIL_LOG_MAX_CHARS = int(os.getenv("SCHEDULER_MAIL_LOG_MAX_CHARS", "400000"))
# 调度汇总邮件正文上限（Mongo 中可存更长日志）；关闭邮件设 SCHEDULER_RESULT_ALPHA_NOTIFY_ENABLED=0
SCHEDULER_RESULT_EMAIL_BODY_MAX_CHARS = int(
    os.getenv("SCHEDULER_RESULT_EMAIL_BODY_MAX_CHARS", "100000")
)
NAV_REAL_WZ_BSYH_MASTER = "WZ_BSYH_MASTER"
NAV_REAL_WZ_BSYH_B = "WZ_BSYH_B"
# 吾执二二号 STZ053：fareport 邮件「净值表」附件竖表 → fund_nav_real.WZ_EEH_MASTER（auto_import_stz053_nav_mail）
NAV_REAL_WZ_EEH_MASTER = "WZ_EEH_MASTER"
# 吾执多元一号 SAJM63(总)：wangkan 邮件「基金净值」→ fund_nav_real.WZ_DYYH_MASTER（auto_import_dyyh_nav_mail）
NAV_REAL_WZ_DYYH_MASTER = "WZ_DYYH_MASTER"
# 吾执一零号 SQL632(总)：wangkan「【基金净值】…」→ fund_nav_real.WZ_YLH_MASTER（auto_import_ylh_nav_mail）
NAV_REAL_WZ_YLH_MASTER = "WZ_YLH_MASTER"
# 吾执零一号 STZ049：管理人「等22个产品净值表发送YYYYMMDD」邮件 + 集合计划每日净值表 → fund_nav_real.WZ_LYH_MASTER（auto_import_wz_lyh_nav_mail）
NAV_REAL_WZ_LYH_MASTER = "WZ_LYH_MASTER"
# 吾执量化精选一号 SASQ16：同上封邮件 / 同附件多行 → fund_nav_real.WZ_LHJXYH_MASTER（与零一号同一 auto_import_wz_lyh_nav_mail）
NAV_REAL_WZ_LHJXYH_MASTER = "WZ_LHJXYH_MASTER"
# 吾执三零号 SXN031(总)：wangkan 邮件「基金净值」→ fund_nav_real.WZ_SLH_MASTER（auto_import_slh_nav_mail）
NAV_REAL_WZ_SLH_MASTER = "WZ_SLH_MASTER"
# 吾执多元量选 SAJM64(总)：wangkan 邮件「基金净值」→ fund_nav_real.WZ_DYLX_MASTER（auto_import_dylx_nav_mail）
NAV_REAL_WZ_DYLX_MASTER = "WZ_DYLX_MASTER"
# 吾执一三号主代码 SAHK33：资产净值公告邮件 → fund_nav_real.WZ_YSH_MASTER（auto_import_ysh_nav_mail；同表多份额仅落库 SAHK33）
NAV_REAL_WZ_YSH_MASTER = "WZ_YSH_MASTER"
# 吾执零零号 SNP584：资产净值公告邮件 → fund_nav_real.WZ_LLH_MASTER（auto_import_llh_nav_mail；同表仅主基金全称 + SNP584）
NAV_REAL_WZ_LLH_MASTER = "WZ_LLH_MASTER"
# 吾执九零号 SXR194：资产净值公告邮件 → fund_nav_real.WZ_JLH_MASTER（auto_import_jlh_nav_mail；同表多份额仅落库 SXR194）
NAV_REAL_WZ_JLH_MASTER = "WZ_JLH_MASTER"
# 吾执 CTA 一号 SNG191：ALPHA_MAIL「净值表邮件…【国信托管】」zip/xlsx → fund_nav_real.WZ_CTAYH_MASTER（auto_import_ctayh_nav_mail）
NAV_REAL_WZ_CTAYH_MASTER = "WZ_CTAYH_MASTER"
# 吾执多元 CTA 一号 SXE021(总)：wangkan「【基金净值】…」→ fund_nav_real.WZ_DYCTAYH_MASTER（auto_import_dyctayh_nav_mail，每个交易日 T-1）
NAV_REAL_WZ_DYCTAYH_MASTER = "WZ_DYCTAYH_MASTER"
# 华泰证券 HT1 普通账单资金情况（吾执博士一号 666810103835）→ auto_import_htzq_ht1_capital_mail
HTZQ_666810103835_SETTLE_DB = "fstock_settle_real"
HTZQ_666810103835_SETTLE_COLLECTION = "HTZQ_666810103835"
# T0 日内汇总：FTP 拉取 excel 入库（库名/集合可配；密码建议用环境变量覆盖）
T0_FTP_HOST = os.getenv("T0_FTP_HOST", "47.102.193.151")
T0_FTP_PORT = int(os.getenv("T0_FTP_PORT", "21"))
T0_FTP_USER = os.getenv("T0_FTP_USER", "wuzhi")
T0_FTP_PASSWORD = os.getenv("T0_FTP_PASSWORD", "wuzhi2020")
T0_FTP_REMOTE_DIR = os.getenv("T0_FTP_REMOTE_DIR", "/daily_report")
T0_FTP_XLSX_SUFFIX = "_wuzhi_日内交易汇总.xlsx"
MONGODB_T0_PERFORMANCE_DB = "T0_performance"
MONGODB_T0_PERFORMANCE_COLLECTION = "daily_report"
# 周度绩效（qichat CSV）→ 集合 t0_order
MONGODB_T0_ORDER_COLLECTION = "t0_order"
T0_QICHAT_IMPORT_DIR = BASE_DIR / "qichat"
# 周度绩效邮件主题前缀，完整主题为 {前缀}{起YYYYMMDD}_{止YYYYMMDD}
T0_QICHAT_MAIL_SUBJECT_PREFIX = (
    os.getenv("T0_QICHAT_MAIL_SUBJECT_PREFIX") or "吾执_周度绩效_"
).strip()
# Alphadata 目录（xlsx 源文件）
ALPHADATA_DIR = BASE_DIR / "Alphadata"

# 交易日历：库名同 MONGODB_DB_NAME，独立集合（字段仍为 trade_date）
MONGODB_TRADE_CALENDAR_COLLECTION = "trade_calendar"
TRADE_DATES_CSV = BASE_DIR / "trade_dates_all" / "trade_dates_all.csv"

# RQ 基准行情：库 basic_rq，集合 rq_bench（供 update_rq_bench 系列任务使用）
MONGODB_RQ_BENCH_DB = "basic_rq"
MONGODB_RQ_BENCH_COLLECTION = "rq_bench"
# 同一库内：以 calc_ 开头的集合存放「计算结果」，与原始行情 rq_bench 区分
MONGODB_RQ_BENCH_CALC_PREFIX = "calc_"
# 产品净值 vs 基准对比（日频行、区间汇总）；名称可扩展，须以 calc_ 开头
MONGODB_RQ_BENCH_CALC_NAV_BENCH_DAILY = "calc_nav_bench_daily"
MONGODB_RQ_BENCH_CALC_NAV_BENCH_SUMMARY = "calc_nav_bench_summary"
# 泽鑫多维等五列净值表：与博士一号相同库 fund_nav_real，独立四个集合（与 WZ_BSYH_* 并列）
MONGODB_ZXDW_NAV_COLLECTIONS = (
    "WZ_ZXDW_MASTER",
    "WZ_ZXDW_A",
    "WZ_ZXDW_B",
    "WZ_ZXDW_C",
)
ZXDW_NAV_IMPORT_DIR = BASE_DIR / "WZ_ZXDW_MASTER"
# ZXDW 净值邮件附件落盘目录；主题关键词见 auto_import_zxdw_nav_mail.ZXDW_NAV_MAIL_FUND_KEY_PHRASES
ZXDW_NAV_MAIL_ATTACH_DIR = BASE_DIR / "downloaded_attachments_zxdw_nav"
# CTA 一号净值邮件附件落盘目录（主题见 auto_import_ctayh_nav_mail）
CTAYH_NAV_MAIL_ATTACH_DIR = BASE_DIR / "downloaded_attachments_ctayh_nav"
# 多元 CTA 一号净值邮件附件落盘目录（主题见 auto_import_dyctayh_nav_mail）
DYCTAYH_NAV_MAIL_ATTACH_DIR = BASE_DIR / "downloaded_attachments_dyctayh_nav"
# ZXDW 邮件导入：仅写入这些产品代码（逗号分隔；默认 STZ051，跳过 TZ051A/TZ051B 等列）。可用环境变量覆盖。
ZXDW_NAV_MAIL_IMPORT_ASSET_CODES = os.getenv("ZXDW_NAV_MAIL_IMPORT_ASSET_CODES", "STZ051")

# 期货结算单（长江）落库配置：库/集合由命令 auto_import_cjqh_settle_mail 使用
MONGODB_CJQH_SETTLE_DB = "future_settle_real"
MONGODB_CJQH_SETTLE_COLLECTION = "CJQH_81801575"
# 长江期货结算单邮件附件落盘目录
CJQH_SETTLE_ATTACH_DIR = BASE_DIR / "downloaded_attachments_cjqh"

# 期货结算单（华泰）落库配置：库/集合由命令 auto_import_htqh_settle_mail 使用
MONGODB_HTQH_SETTLE_DB = "future_settle_real"
MONGODB_HTQH_SETTLE_COLLECTION = "HTQH_80017209"
# 华泰期货结算单邮件附件落盘目录
HTQH_SETTLE_ATTACH_DIR = BASE_DIR / "downloaded_attachments_htqh"

# 期货结算单（五矿）落库配置：库/集合由命令 auto_import_wkqh_settle_mail 使用
MONGODB_WKQH_SETTLE_DB = "future_settle_real"
MONGODB_WKQH_SETTLE_COLLECTION = "WKQH_66601123"
# 五矿期货结算单邮件附件落盘目录
WKQH_SETTLE_ATTACH_DIR = BASE_DIR / "downloaded_attachments_wkqh"
# 国海证券17190083
GHZQ_17190083_SETTLE_DB = "fstock_settle_real" 
GHZQ_17190083_SETTLE_COLLECTION = "GHZQ_17190083"
GHZQ_17190083_SETTLE_ATTACH_DIR = BASE_DIR / "downloaded_attachments_ghzq"
# alpha产品表现页面的指标口径配置
# A 股日频年化因子，默认 252；若改为 244/245，需全站统一
NAV_ANNUALIZATION_FACTOR = 252
# 波动率/夏普的最小样本天数；不足时显示为空
NAV_MIN_SAMPLE_DAYS = 60
# 最大回撤是否在 0 时显示为空（True=显示为空；False=显示 0.00%）
NAV_MDD_ZERO_AS_NA = False


