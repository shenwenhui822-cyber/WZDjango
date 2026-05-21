# option.volatility CSV 导入

修改顶部 `CSV_FILES`，CSV 与脚本放同一目录，执行：

```bash
python import_volatility_csv.py
```

写入 `option.volatility`，MongoDB：`mongodb://option:volatility@192.168.110.199:27017/?authSource=admin`（用户在 admin 库认证）。

CSV 列：`date`、`volatility_num`（或 `日期`、`波动率`）。依赖：`pip install pymongo`。
