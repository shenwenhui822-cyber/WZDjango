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
LOGIN_REDIRECT_URL = "/home/"
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
NAV_REAL_WZ_BSYH_MASTER = "WZ_BSYH_MASTER"
NAV_REAL_WZ_BSYH_B = "WZ_BSYH_B"
# Alphadata 目录（xlsx 源文件）
ALPHADATA_DIR = BASE_DIR / "Alphadata"

# 交易日历：库名同 MONGODB_DB_NAME，独立集合（字段仍为 trade_date）
MONGODB_TRADE_CALENDAR_COLLECTION = "trade_calendar"
TRADE_DATES_CSV = BASE_DIR / "trade_dates_all" / "trade_dates_all.csv"
