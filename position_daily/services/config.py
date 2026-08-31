"""MongoDB / 通联 MySQL（tldb）与业务常量（见 _tlnew_utf8.csv / 分析计算说明.md）"""

import os

STRATEGY_TAG = "DBZQ_18931015"

POSITION_COLLECTION = "DBZQ_18931015"

POSITION_DB = "position_close_record"

RQ_DB = "basic_rq"

MONGO_POSITION_URI = "mongodb://reader:readonly_wonderwz@192.168.110.199:27017/"

MONGO_RQ_URI = "mongodb://reader:readonly_wonderwz@192.168.110.199:27018/"

# 通联 MySQL tldb（表目录见根目录 _tlnew_utf8.csv；可用环境变量 WIND_MYSQL_* 覆盖）
WIND_MYSQL_HOST = os.environ.get("WIND_MYSQL_HOST", "114.80.62.203")
WIND_MYSQL_PORT = int(os.environ.get("WIND_MYSQL_PORT", "5306"))
WIND_MYSQL_USER = os.environ.get("WIND_MYSQL_USER", "root")
WIND_MYSQL_PASSWORD = os.environ.get("WIND_MYSQL_PASSWORD", "Wz@123456")
WIND_MYSQL_DATABASE = os.environ.get("WIND_MYSQL_DATABASE", "tldb")

# 宽基风格：rq_base_index 字段 + 通联 mkt_idxd 指数代码
BUCKET_ORDER = [
    ("上证50", "in_SZ50"),
    ("沪深300", "in_HS300"),
    ("中证500", "in_ZZ500"),
    ("中证1000", "in_ZZ1000"),
    ("中证2000", "in_ZZ2000"),
]

BENCH_INDEX_MAP = {
    "上证50": "000016",
    "沪深300": "000300",
    "中证500": "000905",
    "中证1000": "000852",
    "中证2000": "932000",
    "其他": "DY800002",  # 通联全A(沪深)
}
# 兼容旧导入名
BENCH_WIND_MAP = BENCH_INDEX_MAP

MV_LARGE_YUAN = 50_000_000_000  # 500 亿（mkt_equd_eval.MARKET_VALUE 单位：元）
MV_MID_YUAN = 10_000_000_000  # 100 亿
# 兼容旧名（若外部仍按「万元」阈值引用，数值已不再适用）
MV_LARGE_WAN = MV_LARGE_YUAN
MV_MID_WAN = MV_MID_YUAN

