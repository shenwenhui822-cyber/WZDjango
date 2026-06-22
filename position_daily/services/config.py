"""MongoDB / Wind MySQL 与业务常量（见 date.md / 分析计算说明.md）"""

import os

STRATEGY_TAG = "DBZQ_18931015"

POSITION_COLLECTION = "DBZQ_18931015"

POSITION_DB = "position_close_record"

RQ_DB = "basic_rq"

MONGO_POSITION_URI = "mongodb://reader:readonly_wonderwz@192.168.110.199:27017/"

MONGO_RQ_URI = "mongodb://reader:readonly_wonderwz@192.168.110.199:27018/"

# Wind MySQL（生产建议改用环境变量 WIND_MYSQL_*）
WIND_MYSQL_HOST = os.environ.get("WIND_MYSQL_HOST", "114.80.62.203")
WIND_MYSQL_PORT = int(os.environ.get("WIND_MYSQL_PORT", "5306"))
WIND_MYSQL_USER = os.environ.get("WIND_MYSQL_USER", "root")
WIND_MYSQL_PASSWORD = os.environ.get("WIND_MYSQL_PASSWORD", "Wz@123456")
WIND_MYSQL_DATABASE = os.environ.get("WIND_MYSQL_DATABASE", "wind")

# 宽基风格：rq_base_index 字段 + Wind 指数代码（AINDEXEODPRICES）
BUCKET_ORDER = [
    ("上证50", "in_SZ50"),
    ("沪深300", "in_HS300"),
    ("中证500", "in_ZZ500"),
    ("中证1000", "in_ZZ1000"),
    ("中证2000", "in_ZZ2000"),
]

BENCH_WIND_MAP = {
    "上证50": "000016.SH",
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "中证1000": "000852.SH",
    "中证2000": "932000.CSI",
    "其他": "881001.WI",
}

MV_LARGE_WAN = 5_000_000  # 500 亿（Wind S_VAL_MV 单位：万元）
MV_MID_WAN = 1_000_000  # 100 亿

