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

from wzproject.mongodb_settings import *  # noqa: F403

# alpha产品表现页面的指标口径配置
# A 股日频年化因子，默认 252；若改为 244/245，需全站统一
NAV_ANNUALIZATION_FACTOR = 252
# 波动率/夏普的最小样本天数；不足时显示为空
NAV_MIN_SAMPLE_DAYS = 60
# 最大回撤是否在 0 时显示为空（True=显示为空；False=显示 0.00%）
NAV_MDD_ZERO_AS_NA = False


