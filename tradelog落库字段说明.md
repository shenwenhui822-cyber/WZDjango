# tradelog 落库字段说明 — accounts

Mongo 每条文档里的 `accounts` 数组，是 `ACT_86014577.py` 落库的核心业务数据。  
本文只说明 `accounts`，按层级从外到内展开到最底层字段。

---

## 0. accounts 是什么

```
Mongo 文档
└── accounts[]          ← 本文范围
    ├── [0] 账号 A 快照
    ├── [1] 账号 B 快照   （仅配置了多账号时才有）
    └── ...
```

- **类型**：数组（`array`）
- **元素个数**：与策略配置的资金账号数一致；默认只有 1 个（`86014577`）
- **元素含义**：某次轮询时刻，该资金账号的「账户资金 + 持仓明细」快照
- **数据来源**：QMT 接口 `get_trade_detail_data(account_id, account_type, "account"|"position")`，经策略清洗后写入

每个数组元素只有两种形态，由 **`ok`** 区分：

| ok | 含义 | 何时出现 |
|----|------|----------|
| `true` | 账户资金行查询成功 | QMT 返回了至少 1 条 account 行 |
| `false` | 账户资金行查询失败 | QMT 未返回 account 行（仍可能有持仓数据） |

---

## 1. 成功快照（ok = true）

完整树形结构：

```
accounts[i]  (ok=true)
├── ok
├── account_id
├── account_type
├── total_asset
├── market_value
├── available_cash
├── position_profit
├── positions[]
│   └── positions[j]
│       ├── code
│       ├── name
│       ├── volume
│       ├── available_volume
│       ├── cost_price
│       ├── market_value
│       ├── profit
│       ├── profit_pct
│       ├── last_price
│       ├── prev_close
│       ├── change_amount
│       └── change_pct
├── position_summary
│   ├── count
│   ├── total_market_value
│   ├── total_profit
│   ├── avg_change_pct
│   ├── up_count
│   ├── down_count
│   └── flat_count
├── row_debug
│   └── { 动态键: 字符串或数值 }    ← QMT 原始账户行
├── source
├── ts_unix
└── position_query_meta          ← 可选，仅采集持仓时附带
    ├── row_count
    ├── err
    └── tick_err                 ← 可选
```

---

### 1.1 ok

| 项 | 值 |
|----|-----|
| 类型 | `boolean` |
| 取值 | 固定 `true` |
| 含义 | 标记本条 account 元素为「账户资金查询成功」 |

---

### 1.2 account_id

| 项 | 值 |
|----|-----|
| 类型 | `string` |
| 含义 | 本条快照对应的**资金账号** |
| 示例 | `"86014577"` |
| 来源 | 策略配置或环境变量 `SHOWACCOUNT_ACCOUNT_ID` / `SHOWACCOUNT_ACCOUNT_IDS`；写入前 `str()` 化 |

---

### 1.3 account_type

| 项 | 值 |
|----|-----|
| 类型 | `string` |
| 含义 | QMT 账户类型，决定 `get_trade_detail_data` 的第二个参数 |
| 常见值 | `"STOCK"`（股票） |
| 来源 | 依次读取 `ContextInfo.acct_type` → `account_type` → `accountType`；都没有则默认 `"STOCK"` |

---

### 1.4 total_asset — 总资产

| 项 | 值 |
|----|-----|
| 类型 | `number`（浮点） |
| 单位 | 元 |
| 含义 | 账户总资产（含持仓 + 现金等） |
| QMT 原始字段（按优先级取第一个有效值） | `m_dBalance` → `total_asset` → `totalAsset` → `m_dAsset` |
| 缺省 | 四个字段都读不到时为 `0.0` |

---

### 1.5 available_cash — 可用资金

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 含义 | 当前可用于下单的现金 |
| QMT 原始字段（优先级） | `m_dAvailable` → `cash` → `available_cash` → `m_dEnableBalance` |
| 缺省 | `0.0` |

---

### 1.6 market_value — 持仓市值

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 含义 | 账户层面持仓总市值 |
| QMT 原始字段（优先级） | `m_dInstrumentValue` → `m_dMarketValue` → `market_value` → `m_dStockValue` → `stock_value` |
| **回退计算** | 若上述字段 ≤ 0，且 `total_asset > 0` 且 `available_cash ≥ 0`，则：<br>`market_value = max(0, total_asset - available_cash)` |
| 缺省 | 仍无法得到时为 `0.0` |

> 注意：账户级 `market_value` 来自 QMT 账户行；与 `position_summary.total_market_value`（各持仓市值之和）可能不完全相等。

---

### 1.7 position_profit — 持仓浮动盈亏

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | 写入前 `round(..., 2)` |
| 含义 | 账户层面持仓浮动盈亏 |
| QMT 原始字段（优先级） | `m_dPositionProfit` → `m_dFloatProfit` → `position_profit` |
| **回退计算** | 若 QMT 值为 `0` 且 `positions` 非空：<br>`position_profit = position_summary.total_profit`（各持仓 `profit` 之和） |
| 缺省 | `0.0`（再 round 为 `0.0`） |

---

### 1.8 source

| 项 | 值 |
|----|-----|
| 类型 | `string` |
| 取值 | 固定 `"act_get_trade_detail_data"` |
| 含义 | 标明账户/持仓数值来自 QMT 的 `get_trade_detail_data`，非其他接口 |

---

### 1.9 ts_unix

| 项 | 值 |
|----|-----|
| 类型 | `number`（浮点） |
| 单位 | Unix 秒 |
| 含义 | **本条 account 对象组装完成**的时刻，非 Mongo 插入时刻 |
| 来源 | `time.time()`，在 `_row_to_doc` 末尾写入 |

---

### 1.10 row_debug — QMT 账户原始行

| 项 | 值 |
|----|-----|
| 类型 | `object`（键值对，键不固定） |
| 含义 | 从 QMT 返回的**第一条 account 行**原样提取的调试字段，便于对照接口 |
| 值类型 | 浮点保留 4 位小数；其余转为 `string` |

**会收录哪些键：**

| 键名规则 | 说明 |
|----------|------|
| 以 `m_` 开头 | QMT C++ 结构体字段，如 `m_dBalance`、`m_dAvailable` |
| 固定白名单 | `account_id`、`broker`、`platform`、`node`、`product` |

**常见底层键（账户行）：**

| row_debug 键 | 通常对应业务含义 | 与落库字段关系 |
|--------------|------------------|----------------|
| `m_dBalance` | 总资产 | → `total_asset` 首选来源 |
| `m_dAsset` | 资产 | → `total_asset` 备选 |
| `m_dAvailable` | 可用 | → `available_cash` 首选 |
| `m_dEnableBalance` | 可用余额 | → `available_cash` 备选 |
| `m_dInstrumentValue` | 证券市值 | → `market_value` 首选 |
| `m_dMarketValue` | 市值 | → `market_value` 备选 |
| `m_dStockValue` | 股票市值 | → `market_value` 备选 |
| `m_dPositionProfit` | 持仓盈亏 | → `position_profit` 首选 |
| `m_dFloatProfit` | 浮动盈亏 | → `position_profit` 备选 |

> `row_debug` 只存原始值，**不参与** `total_asset` 等字段的二次计算；业务字段已在上一层按优先级读好。

---

### 1.11 position_query_meta — 持仓采集过程（可选）

仅当 `SHOWACCOUNT_INCLUDE_POSITIONS` 未关闭（默认开启）时出现。

```
position_query_meta
├── row_count      int     QMT 返回的持仓原始行数（含 volume=0 的行）
├── err            string  持仓查询错误；成功为空字符串 ""
└── tick_err       string  可选；拉取行情 tick 失败时的原因
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `row_count` | `int` | `get_trade_detail_data(..., "position")` 返回列表长度 |
| `err` | `string` | 持仓接口异常或空结果时的错误文案；正常为空 `""` |
| `tick_err` | `string` | 仅当 `ContextInfo.get_full_tick` 不可用或报错时出现，如 `"no get_full_tick"` |

> 出现 `tick_err` 时，`positions[].last_price` / `prev_close` / `change_*` 可能为 0 或回退到 QMT 持仓行，分析时需留意。

---

### 1.12 positions[] — 持仓明细数组

```
positions[]
└── positions[j]    单只证券快照（volume > 0 才入库）
```

| 项 | 值 |
|----|-----|
| 类型 | `array` |
| 排序 | 按 `market_value` **降序**（市值大的在前） |
| 过滤 | 仅保留 `volume > 0` 的标的；QMT 返回但持仓为 0 的行丢弃 |
| 空数组 | `SHOWACCOUNT_INCLUDE_POSITIONS=0` 时恒为 `[]` |

单条持仓完整树：

```
positions[j]
├── code
├── name
├── volume
├── available_volume
├── cost_price
├── market_value
├── profit
├── profit_pct
├── last_price
├── prev_close
├── change_amount
└── change_pct
```

---

#### 1.12.1 code — 证券代码

| 项 | 值 |
|----|-----|
| 类型 | `string` |
| 格式 | `{代码}.{交易所}`，如 `600519.SH`、`000001.SZ` |
| QMT 原始字段 | `m_strInstrumentID` / `instrument_id` / `stock_code`（代码）<br>`m_strExchangeID` / `exchange_id` / `exchange`（交易所） |
| 组装规则 | 代码与交易所都有：`"{code}.{ex}"`；只有代码：仅代码字符串 |

---

#### 1.12.2 name — 证券名称

| 项 | 值 |
|----|-----|
| 类型 | `string` |
| QMT 原始字段（优先级） | `m_strInstrumentName` → `instrument_name` → `stock_name` |
| 缺省 | `""` |

---

#### 1.12.3 volume — 持仓数量

| 项 | 值 |
|----|-----|
| 类型 | `int` |
| 单位 | 股（张） |
| QMT 原始字段（优先级） | `m_nVolume` → `volume` → `position` |
| 缺省 | `0`（为 0 时整行不进入 `positions`） |

---

#### 1.12.4 available_volume — 可用数量

| 项 | 值 |
|----|-----|
| 类型 | `int` |
| 单位 | 股 |
| 含义 | 当前可卖出数量（T+1 等规则下可能小于 `volume`） |
| QMT 原始字段（优先级） | `m_nCanUseVolume` → `can_use_volume` → `available_volume` |
| 缺省 | `0` |

---

#### 1.12.5 cost_price — 成本价

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | `round(..., 4)` |
| 含义 | 持仓成本单价（开仓价或均价） |
| QMT 原始字段（优先级） | `m_dOpenPrice` → `m_dAvgPrice` → `open_price` → `cost_price` |
| 缺省 | `0.0` |

---

#### 1.12.6 last_price — 最新价

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | `round(..., 4)` |
| 含义 | 用于算市值、盈亏、涨跌幅的现价 |

**取值顺序（层层回退）：**

1. 行情 tick（`get_full_tick(code)` 返回的 dict）中第一个 **> 0** 的字段：  
   `lastPrice` → `last` → `price`
2. tick 无效时，QMT 持仓行：`m_dLastPrice` → `last_price`
3. 仍无效：`0.0`

---

#### 1.12.7 prev_close — 昨收价

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | `round(..., 4)` |
| 含义 | 昨日收盘价，用于计算当日涨跌 |

**取值来源（仅 tick，无 QMT 回退）：**

tick 中第一个 **> 0** 的字段：  
`lastClose` → `preClose` → `last_close` → `prev_close`

无 tick 或字段无效：`0.0`

---

#### 1.12.8 market_value — 单票市值

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | `round(..., 2)` |

**取值顺序：**

1. QMT 持仓行（优先级）：  
   `m_dInstrumentValue` → `m_dMarketValue` → `market_value` → `instrument_value`
2. 若 ≤ 0，且 `last_price > 0` 且 `volume > 0`：  
   **`market_value = last_price × volume`**
3. 否则：`0.0`（再 round）

---

#### 1.12.9 profit — 单票浮动盈亏

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | `round(..., 2)` |

**取值顺序：**

1. QMT 持仓行（优先级）：  
   `m_dPositionProfit` → `m_dFloatProfit` → `position_profit` → `float_profit` → `profit`
2. 若 QMT 值为 **恰好 0**，且 `cost_price > 0`、`last_price > 0`、`volume > 0`：  
   **`profit = (last_price - cost_price) × volume`**
3. 否则保持 QMT 值（含真实的 0 盈亏）

> 若 QMT 返回 0 但实际有浮盈，会触发公式重算；若 QMT 返回非 0，直接使用 QMT 值。

---

#### 1.12.10 profit_pct — 相对成本收益率

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | **百分比数值**（5.8824 表示 5.8824%） |
| 精度 | `round(..., 4)` |
| 公式 | 当 `cost_price > 0` 且 `last_price > 0`：<br>`profit_pct = (last_price - cost_price) / cost_price × 100` |
| 缺省 | `0.0` |

---

#### 1.12.11 change_amount — 相对昨收涨跌额

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | 元 |
| 精度 | `round(..., 4)` |
| 公式 | 当 `last_price > 0` 且 `prev_close > 0`：<br>`change_amount = last_price - prev_close` |
| 缺省 | `0.0` |

---

#### 1.12.12 change_pct — 相对昨收涨跌幅

| 项 | 值 |
|----|-----|
| 类型 | `number` |
| 单位 | **百分比数值** |
| 精度 | `round(..., 4)` |
| 公式 | 当 `last_price > 0` 且 `prev_close > 0`：<br>`change_pct = (last_price - prev_close) / prev_close × 100` |
| 缺省 | `0.0` |

---

### 1.13 position_summary — 对 positions[] 的聚合

由 `_position_summary(positions)` 对**同一 account 下全部 `positions[j]`** 二次计算，不读 QMT 账户行。

```
position_summary
├── count
├── total_market_value
├── total_profit
├── avg_change_pct
├── up_count
├── down_count
└── flat_count
```

| 字段 | 类型 | 精度 | 计算公式 |
|------|------|------|----------|
| `count` | `int` | — | `len(positions)`，即有效持仓只数 |
| `total_market_value` | `number` | 2 位小数 | `Σ positions[j].market_value` |
| `total_profit` | `number` | 2 位小数 | `Σ positions[j].profit` |
| `avg_change_pct` | `number` | 4 位小数 | 平均持仓涨跌幅：对所有 `last_price > 0` 的持仓取 `change_pct` 算术平均；无有效标的时为 `0.0` |
| `up_count` | `int` | — | `change_pct > 0` 的持仓只数 |
| `down_count` | `int` | — | `change_pct < 0` 的持仓只数 |
| `flat_count` | `int` | — | `count - up_count - down_count`（含 `change_pct == 0` 或昨收无效导致为 0 的标的） |

**与账户级字段的关系：**

| 账户级字段 | 与 position_summary 关系 |
|------------|--------------------------|
| `position_profit` | QMT 为 0 时回退为 `total_profit` |
| `market_value` | 独立来自 QMT 账户行，**不等于** `total_market_value` 的必要条件 |

---

## 2. 失败快照（ok = false）

当 `get_trade_detail_data(..., "account")` 无返回时出现。

```
accounts[i]  (ok=false)
├── ok              false
├── account_id
├── account_type
├── err_msg
├── positions[]           ← 结构同 §1.12，可能仍有数据
├── position_summary      ← 结构同 §1.13，基于已有 positions 计算
└── source                "act_get_trade_detail_data"
```

与成功形态的差异：

| 字段 | 失败时 |
|------|--------|
| `ok` | `false` |
| `err_msg` | 有；账户查询失败原因，如 `"empty"` 或异常字符串 |
| `total_asset` / `market_value` / `available_cash` / `position_profit` | **不存在** |
| `row_debug` | **不存在** |
| `ts_unix` | **不存在** |
| `position_query_meta` | **不存在** |
| `positions` / `position_summary` | **仍可能存在**（持仓接口与账户接口独立查询） |

### 2.1 err_msg

| 项 | 值 |
|----|-----|
| 类型 | `string` |
| 含义 | 账户资金行查询失败的具体原因 |
| 常见值 | `"empty"`（接口空结果）、`get_trade_detail_data not available`、QMT 抛出的异常文本 |

---

## 3. 完整示例（仅 accounts 部分）

```json
"accounts": [
  {
    "ok": true,
    "account_id": "86014577",
    "account_type": "STOCK",
    "total_asset": 1000000.0,
    "market_value": 800000.0,
    "available_cash": 200000.0,
    "position_profit": 15000.0,
    "positions": [
      {
        "code": "600519.SH",
        "name": "贵州茅台",
        "volume": 100,
        "available_volume": 100,
        "cost_price": 1700.0,
        "market_value": 180000.0,
        "profit": 10000.0,
        "profit_pct": 5.8824,
        "last_price": 1800.0,
        "prev_close": 1780.0,
        "change_amount": 20.0,
        "change_pct": 1.1236
      }
    ],
    "position_summary": {
      "count": 1,
      "total_market_value": 180000.0,
      "total_profit": 10000.0,
      "avg_change_pct": 1.1236,
      "up_count": 1,
      "down_count": 0,
      "flat_count": 0
    },
    "row_debug": {
      "m_dBalance": "1000000.0",
      "m_dAvailable": "200000.0",
      "m_dInstrumentValue": 800000.0
    },
    "source": "act_get_trade_detail_data",
    "ts_unix": 1717390800.12,
    "position_query_meta": {
      "row_count": 1,
      "err": ""
    }
  }
]
```

---

## 4. 账户简报（表格示例）

从 `accounts[0]` 根字段 + `position_summary` 可整理成如下表格简报，供人工快速浏览。  
示例数据：账号 `012000076288`。

**账户简报**

| 项目 | 落库字段 | 数值 |
|------|----------|------|
| 资金账号 | `account_id` | 012000076288 |
| 账户类型 | `account_type` | STOCK |
| 总资产 | `total_asset` | 707,877.46 |
| 持仓市值 | `market_value` | 670,282.35 |
| 可用资金 | `available_cash` | 37,626.10 |
| 持仓浮动盈亏 | `position_profit` | -30,954.39 |
| 持仓只数 | `position_summary.count` | 121 |
| 持仓总盈亏 | `position_summary.total_profit` | -31,515.33 |
| 平均持仓涨跌幅 | `position_summary.avg_change_pct` | -1.7575% |
| 上涨 / 下跌 / 平盘 | `up_count` / `down_count` / `flat_count` | 18 / 102 / 1 |

---

## 5. 字段依赖关系（便于排查）

```
QMT account 行 ──→ total_asset / available_cash / market_value / position_profit
                 └──→ row_debug（原始镜像）

QMT position 行 ──→ code / name / volume / available_volume / cost_price
                  └──→ market_value / profit（优先）

get_full_tick ──→ last_price / prev_close
              └──→ 间接影响 market_value、profit、change_*、profit_pct

positions[] ──→ position_summary（纯聚合）

position_summary.total_profit ──→ 可能回写 position_profit（当 QMT 账户盈亏为 0）
```




{
  "_id": {
    "$oid": "6a1952e0b22ae71d2beaaf5f"
  },
  "t_unix": 1780044511.488771,
  "t_iso": "2026-05-29 16:48:31",
  "label": "init",
  "strategy_tag": "ZSZQ_911600210",
  "account_id": "911600210",
  "binding": {
    "strategy_tag": "ZSZQ_911600210",
    "binding_acct": "",
    "binding_acct_type": "CREDIT"
  },
  "meta": {
    "err": "",
    "path": "",
    "last_ts": 1780044511.488771,
    "mongo_err": ""
  },
  "accounts": [
    {
      "ok": true,
      "account_id": "911600210",
      "account_type": "CREDIT",
      "total_asset": 25318338.4,
      "market_value": 25317449.44,
      "available_cash": 565.38,
      "position_profit": -1610582.05,
      "positions": [
        {
          "code": "300308.SZ",
          "name": "中际旭创",
          "volume": 100,
          "available_volume": 100,
          "cost_price": 1052.631,
          "market_value": 116116,
          "profit": 10852.9,
          "profit_pct": 10.3103,
          "last_price": 1161.16,
          "prev_close": 1197.99,
          "change_amount": -36.83,
          "change_pct": -3.0743
        },
        {
          "code": "300277.SZ",
          "name": "汽轮科技",
          "volume": 300,
          "available_volume": 0,
          "cost_price": 16.198,
          "market_value": 4620,
          "profit": -239.4,
          "profit_pct": -4.9265,
          "last_price": 15.4,
          "prev_close": 16.46,
          "change_amount": -1.06,
          "change_pct": -6.4399
        }
      ],
      "position_summary": {
        "count": 697,
        "total_market_value": 25317449.44,
        "total_profit": -1610581.99,
        "avg_change_pct": -3.5255,
        "up_count": 63,
        "down_count": 631,
        "flat_count": 3
      },
      "row_debug": {
        "m_Enable": "True",
        "m_dAssetBalance": 0,
        "m_dAssureAsset": 19186440.7,
        "m_dAssureEnbuyBalance": 565.38,
        "m_dAvailable": 565.38,
        "m_dBalance": 25318338.4,
        "m_dBuySecuRepayFrozenCommission": 0,
        "m_dBuySecuRepayFrozenMargin": 0,
        "m_dBuyWaitMoney": 0,
        "m_dCashIn": 0,
        "m_dCloseProfit": 0,
        "m_dCommission": 7489.2552,
        "m_dCredit": 0,
        "m_dCurrMargin": 0,
        "m_dDebtLoss": 1.7976931348623157e+308,
        "m_dDebtProfit": 1.7976931348623157e+308,
        "m_dDeposit": 0,
        "m_dDiffAssureEnbuyBalance": 0,
        "m_dDiffEnableBailBalance": 0,
        "m_dDiffFinEnbuyBalance": 0,
        "m_dDiffFinEnrepaidBalance": 0,
        "m_dEnableBailBalance": 1019834.74,
        "m_dEncumberedAssets": 0,
        "m_dEntrustAsset": 19186440.7,
        "m_dFetchAssetBalance": 1.7976931348623157e+308,
        "m_dFetchBalance": 565.38,
        "m_dFinCompactBalance": 1.7976931348623157e+308,
        "m_dFinCompactFare": 1.7976931348623157e+308,
        "m_dFinCompactInterest": 3679.07,
        "m_dFinDebt": 6128218.62,
        "m_dFinEnableBalance": 1.7976931348623157e+308,
        "m_dFinEnableQuota": 4620120.77,
        "m_dFinEnbuyBalance": 1.7976931348623157e+308,
        "m_dFinEnrepaidBalance": 1.7976931348623157e+308,
        "m_dFinIncome": 1.7976931348623157e+308,
        "m_dFinLoss": 1.7976931348623157e+308,
        "m_dFinMarketValue": 1.7976931348623157e+308,
        "m_dFinMaxQuota": 10750000,
        "m_dFinProfit": 1.7976931348623157e+308,
        "m_dFinProfitAmortized": 1.7976931348623157e+308,
        "m_dFinUsedBail": 1.7976931348623157e+308,
        "m_dFinUsedQuota": 6129879.23,
        "m_dFrozenCash": 0,
        "m_dFrozenCommission": 0,
        "m_dFrozenMargin": 0,
        "m_dFrozenRoyalty": 0,
        "m_dFundValue": 0,
        "m_dGoldFrozen": 0,
        "m_dGoldValue": 0,
        "m_dInitBalance": 0,
        "m_dInitCloseMoney": -869386.9352,
        "m_dInstrumentValue": 25317449.44,
        "m_dInstrumentValueRMB": 0,
        "m_dLoanValue": 0,
        "m_dLongValue": 0,
        "m_dMargin": 0,
        "m_dMaxMarginRate": 0,
        "m_dMortgage": 0,
        "m_dNav": 0,
        "m_dNetValue": 0,
        "m_dOtherFare": 1.7976931348623157e+308,
        "m_dOtherFinCompactInterest": 0,
        "m_dOtherRealCompactBalance": 0,
        "m_dPerAssurescaleValue": 4.129,
        "m_dPositionProfit": -1610582.047,
        "m_dPreBalance": 0,
        "m_dPreCredit": 0,
        "m_dPreMortgage": 0,
        "m_dPurchasingPower": 0,
        "m_dRawMargin": 0,
        "m_dRealRiskDegree": 0,
        "m_dRealUsedMargin": 0,
        "m_dReceiveInterestTotal": 0,
        "m_dRepurchaseValue": 0,
        "m_dRisk": 0,
        "m_dRoyalty": 0,
        "m_dSellWaitMoney": 0,
        "m_dShortValue": 0,
        "m_dSloCompactBalance": 1.7976931348623157e+308,
        "m_dSloCompactFare": 1.7976931348623157e+308,
        "m_dSloCompactInterest": 0,
        "m_dSloEnableQuota": 4620120.77,
        "m_dSloEnrepaidBalance": 1.7976931348623157e+308,
        "m_dSloIncome": 1.7976931348623157e+308,
        "m_dSloLoss": 1.7976931348623157e+308,
        "m_dSloMarketValue": 0,
        "m_dSloMaxQuota": 10750000,
        "m_dSloProfit": 1.7976931348623157e+308,
        "m_dSloProfitAmortized": 1.7976931348623157e+308,
        "m_dSloSellBalance": 0,
        "m_dSloUsedBail": 1.7976931348623157e+308,
        "m_dSloUsedQuota": 0,
        "m_dSpecialEnableBalance": 0,
        "m_dStockValue": 25317449.44,
        "m_dSubscribeFee": 0,
        "m_dTotalDebit": 6131897.7,
        "m_dTotalEnableQuota": 1.7976931348623157e+308,
        "m_dTotalUsedQuota": 1.7976931348623157e+308,
        "m_dUnderlyMarketValue": 1.7976931348623157e+308,
        "m_dUsedBailBalance": 1.7976931348623157e+308,
        "m_dUsedSloSellBalance": 0,
        "m_dWithdraw": 0,
        "m_nBrokerType": "3",
        "m_nContractEndDate": "2147483647",
        "m_strAccountID": "911600210",
        "m_strAccountKey": "3____10452____10452____49____911600210____",
        "m_strMoneyType": "",
        "m_strOpenDate": "",
        "m_strStatus": "登录成功",
        "m_strTradingDate": "20260529"
      },
      "source": "act_get_trade_detail_data",
      "ts_unix": 1780044511.4857779,
      "position_query_meta": {
        "row_count": 795,
        "err": ""
      }
    }
  ],
  "query_meta": [
    {
      "account_id": "911600210",
      "row_count": 1,
      "err": "",
      "position_count": 697,
      "row_0": {
        "m_Enable": "True",
        "m_dAssetBalance": 0,
        "m_dAssureAsset": 19186440.7,
        "m_dAssureEnbuyBalance": 565.38,
        "m_dAvailable": 565.38,
        "m_dBalance": 25318338.4,
        "m_dBuySecuRepayFrozenCommission": 0,
        "m_dBuySecuRepayFrozenMargin": 0,
        "m_dBuyWaitMoney": 0,
        "m_dCashIn": 0,
        "m_dCloseProfit": 0,
        "m_dCommission": 7489.2552,
        "m_dCredit": 0,
        "m_dCurrMargin": 0,
        "m_dDebtLoss": 1.7976931348623157e+308,
        "m_dDebtProfit": 1.7976931348623157e+308,
        "m_dDeposit": 0,
        "m_dDiffAssureEnbuyBalance": 0,
        "m_dDiffEnableBailBalance": 0,
        "m_dDiffFinEnbuyBalance": 0,
        "m_dDiffFinEnrepaidBalance": 0,
        "m_dEnableBailBalance": 1019834.74,
        "m_dEncumberedAssets": 0,
        "m_dEntrustAsset": 19186440.7,
        "m_dFetchAssetBalance": 1.7976931348623157e+308,
        "m_dFetchBalance": 565.38,
        "m_dFinCompactBalance": 1.7976931348623157e+308,
        "m_dFinCompactFare": 1.7976931348623157e+308,
        "m_dFinCompactInterest": 3679.07,
        "m_dFinDebt": 6128218.62,
        "m_dFinEnableBalance": 1.7976931348623157e+308,
        "m_dFinEnableQuota": 4620120.77,
        "m_dFinEnbuyBalance": 1.7976931348623157e+308,
        "m_dFinEnrepaidBalance": 1.7976931348623157e+308,
        "m_dFinIncome": 1.7976931348623157e+308,
        "m_dFinLoss": 1.7976931348623157e+308,
        "m_dFinMarketValue": 1.7976931348623157e+308,
        "m_dFinMaxQuota": 10750000,
        "m_dFinProfit": 1.7976931348623157e+308,
        "m_dFinProfitAmortized": 1.7976931348623157e+308,
        "m_dFinUsedBail": 1.7976931348623157e+308,
        "m_dFinUsedQuota": 6129879.23,
        "m_dFrozenCash": 0,
        "m_dFrozenCommission": 0,
        "m_dFrozenMargin": 0,
        "m_dFrozenRoyalty": 0,
        "m_dFundValue": 0,
        "m_dGoldFrozen": 0,
        "m_dGoldValue": 0,
        "m_dInitBalance": 0,
        "m_dInitCloseMoney": -869386.9352,
        "m_dInstrumentValue": 25317449.44,
        "m_dInstrumentValueRMB": 0,
        "m_dLoanValue": 0,
        "m_dLongValue": 0,
        "m_dMargin": 0,
        "m_dMaxMarginRate": 0,
        "m_dMortgage": 0,
        "m_dNav": 0,
        "m_dNetValue": 0,
        "m_dOtherFare": 1.7976931348623157e+308,
        "m_dOtherFinCompactInterest": 0,
        "m_dOtherRealCompactBalance": 0,
        "m_dPerAssurescaleValue": 4.129,
        "m_dPositionProfit": -1610582.047,
        "m_dPreBalance": 0,
        "m_dPreCredit": 0,
        "m_dPreMortgage": 0,
        "m_dPurchasingPower": 0,
        "m_dRawMargin": 0,
        "m_dRealRiskDegree": 0,
        "m_dRealUsedMargin": 0,
        "m_dReceiveInterestTotal": 0,
        "m_dRepurchaseValue": 0,
        "m_dRisk": 0,
        "m_dRoyalty": 0,
        "m_dSellWaitMoney": 0,
        "m_dShortValue": 0,
        "m_dSloCompactBalance": 1.7976931348623157e+308,
        "m_dSloCompactFare": 1.7976931348623157e+308,
        "m_dSloCompactInterest": 0,
        "m_dSloEnableQuota": 4620120.77,
        "m_dSloEnrepaidBalance": 1.7976931348623157e+308,
        "m_dSloIncome": 1.7976931348623157e+308,
        "m_dSloLoss": 1.7976931348623157e+308,
        "m_dSloMarketValue": 0,
        "m_dSloMaxQuota": 10750000,
        "m_dSloProfit": 1.7976931348623157e+308,
        "m_dSloProfitAmortized": 1.7976931348623157e+308,
        "m_dSloSellBalance": 0,
        "m_dSloUsedBail": 1.7976931348623157e+308,
        "m_dSloUsedQuota": 0,
        "m_dSpecialEnableBalance": 0,
        "m_dStockValue": 25317449.44,
        "m_dSubscribeFee": 0,
        "m_dTotalDebit": 6131897.7,
        "m_dTotalEnableQuota": 1.7976931348623157e+308,
        "m_dTotalUsedQuota": 1.7976931348623157e+308,
        "m_dUnderlyMarketValue": 1.7976931348623157e+308,
        "m_dUsedBailBalance": 1.7976931348623157e+308,
        "m_dUsedSloSellBalance": 0,
        "m_dWithdraw": 0,
        "m_nBrokerType": "3",
        "m_nContractEndDate": "2147483647",
        "m_strAccountID": "911600210",
        "m_strAccountKey": "3____10452____10452____49____911600210____",
        "m_strMoneyType": "",
        "m_strOpenDate": "",
        "m_strStatus": "登录成功",
        "m_strTradingDate": "20260529"
      }
    }
  ]
}