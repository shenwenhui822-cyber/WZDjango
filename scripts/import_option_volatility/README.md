# option.volatility CSV 导入

修改顶部 `CSV_FILES`，CSV 与脚本放同一目录，执行：

```bash
python import_volatility_csv.py
```

写入 `option.volatility`，MongoDB：`mongodb://option:volatility@192.168.110.199:27017/?authSource=admin`（用户在 admin 库认证）。

CSV 列：`date` 及 9 个 ETF：`ETF_510050`、`ETF_510300`、`ETF_510500`、`ETF_588000`、`ETF_588080`、`ETF_159901`、`ETF_159915`、`ETF_159919`、`ETF_159922`（`date` 也可用 `日期`）。依赖：`pip install pymongo`。
