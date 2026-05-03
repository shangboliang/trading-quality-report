---
name: trading-quality-report
description: Binance U本位合约交易质量报告系统 - 四阶段同步、三层架构、WebSocket 实时监听
version: 5.0.0
tags: [trading, binance, futures, report, statistics, websocket, sqlite]
triggers:
  - 交易质量报告
  - 交易报告
  - trading report
  - binance futures
  - 合约交易
---

# 交易质量报告系统

四阶段同步：探测 → 溯源 → 聚合 → 接管

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  WebSocket (阶段四: 接管)                                    │
│  ORDER_TRADE_UPDATE → 实时更新 Order/Trade                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│  PositionHistory (顶层) ← 状态机推算                         │
│  字段: id, symbol, position_side, total_realized_pnl        │
│       total_funding_fee, is_liquidation                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ 1:N
┌──────────────────────────┴──────────────────────────────────┐
│  Order (中层) ← trades 按 order_id 聚合                      │
│  字段: order_id, position_id, avg_price, qty                │
└──────────────────────────┬──────────────────────────────────┘
                           │ 1:N
┌──────────────────────────┴──────────────────────────────────┐
│  Trade (底层) ← GET /fapi/v1/userTrades                     │
│  字段: id, order_id, price, qty, realized_pnl               │
└─────────────────────────────────────────────────────────────┘

+ finance_income (资金费率、划转、返佣)
```

## 环境准备

```bash
pip install requests websocket-client
```

### API 密钥配置

**方式 1: 系统环境变量**
```bash
export BINANCE_API_KEY="your_key"
export BINANCE_API_SECRET="your_secret"
```

**方式 2: .env 文件**
```bash
cp .env.example .env
vim .env
```

**优先级:** 系统环境变量 > .env 文件

## 四阶段流程

### 阶段一：探测 (Discovery)
确定需要同步的币种，避免遍历 600+ 币种

```bash
python3 scripts/binance_sync.py --discover
```

接口：
- `GET /fapi/v2/positionRisk` - 当前持仓 (权重 5)
- `GET /fapi/v1/income` - 过去 7 天流水 (权重 30)

### 阶段二：溯源 (Historical Sync)
填充历史数据

```bash
python3 scripts/binance_sync.py --symbol BTCUSDT
```

接口：
- `GET /fapi/v1/allOrders` - 订单骨架
- `GET /fapi/v1/userTrades` - 成交流水
- `GET /fapi/v1/income` - 资金流水

关键：**虚拟起点对齐**
- 问题：远古仓位（6个月前开的）在本地没有 trade 记录
- 解决：对比本地累计持仓 vs positionRisk 实盘持仓
- 如有缺口，以 entryPrice 插入虚拟 Trade (id 为负数)

### 阶段三：聚合 (Aggregation)
数据升维

```bash
python3 scripts/position_builder.py BTCUSDT
```

逻辑：
- 订单级：avg_price 聚合
- 仓位级：状态机推算 PositionHistory

### 阶段四：接管 (Real-time)
WebSocket 实时监听

```bash
python3 scripts/ws_listener.py
```

事件：
- `ORDER_TRADE_UPDATE` - 订单/成交更新
- `ACCOUNT_UPDATE` - 账户/持仓更新

## 使用流程

```bash
# Demo 模式（无需 API）
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --report

# 完整同步
python3 scripts/binance_sync.py --symbol BTCUSDT

# 增量同步
python3 scripts/binance_sync.py --symbol BTCUSDT -i

# 同步资金流水
python3 scripts/binance_sync.py --symbol BTCUSDT --income-only

# 生成统计
python3 scripts/trade_stats.py --symbol BTCUSDT --period week

# 启动实时监听
python3 scripts/ws_listener.py
```

## 数据库表

| 表 | 说明 | 来源 |
|----|------|------|
| position_history | 仓位历史 | 状态机推算 |
| order | 逻辑订单 | trades 聚合 |
| trade | 撮合流水 | /fapi/v1/userTrades |
| finance_income | 资金流水 | /fapi/v1/income |
| sync_state | 同步状态 | 本地记录 |

## 资金流水类型

| 类型 | 说明 |
|------|------|
| FUNDING_FEE | 资金费率（每 8 小时） |
| TRANSFER | 钱包划转 |
| COMMISSION | 手续费返佣 |
| REALIZED_PNL | 已实现盈亏 |

## API 接口

| 接口 | 权重 | 说明 |
|------|------|------|
| GET /fapi/v2/positionRisk | 5 | 当前持仓 |
| GET /fapi/v1/income | 30 | 收入流水 |
| GET /fapi/v1/allOrders | 5 | 所有订单 |
| GET /fapi/v1/userTrades | 5 | 成交记录 |
| GET /fapi/v1/listenKey | 1 | WebSocket key |

## 限制

- 7天跨度：startTime 和 endTime 差值 ≤ 7 天
- 6个月硬极限：最多查过去 6 个月
- fromId 和时间参数互斥

## 关键实现要点

### 浮点数比较
```python
FLOAT_EPSILON = 1e-10
if abs(qty) < FLOAT_EPSILON:  # ✓ 正确
if qty == 0:                   # ✗ 错误
```

### 数据库连接
```python
@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### 魔法数字常量化
```python
MS_PER_DAY = 86400000
DEFAULT_LIMIT = 1000
FLOAT_EPSILON = 1e-10
```

### 外键约束
- 插入顺序：先 Order，再 Trade
- 或暂时禁用外键：`PRAGMA foreign_keys=OFF`

## 监听管理

### 启动后台监听
```bash
# 前台运行
python3 scripts/ws_listener.py

# 后台运行
nohup python3 scripts/ws_listener.py > /tmp/ws_listener.log 2>&1 &
```

### Cron 定时同步
```bash
# 每小时增量同步
0 * * * * cd ~/.hermes/skills/data-science/trading-quality-report && python3 scripts/binance_sync.py -i
```

## 报告生成

### 生成提示词
```bash
# 本周报告
python3 scripts/report_generator.py --period week

# 指定币种
python3 scripts/report_generator.py --symbol CFGUSDT --period week

# 保存到文件
python3 scripts/report_generator.py --period week -o /tmp/prompt.txt
```

### 使用方式
```bash
# 1. 生成提示词
python3 scripts/report_generator.py --period week > /tmp/prompt.txt

# 2. 发送给 AI 生成报告
# AI 会按照严格格式输出：
# - 综合评级 (A+ ~ F)
# - 核心数据概览
# - 交易质量优缺
# - 潜在隐患与优化空间
```

### 报告格式
- 第一行必须是 **综合评级: X**
- 只分析历史数据，不预测未来
- 所有评价必须有数据支撑

## 常用提示语

| 提示语 | 说明 |
|--------|------|
| "同步合约数据" | 执行完整同步 |
| "增量同步" | 只拉取新数据 |
| "生成交易报告" | 输出统计 JSON |
| "生成本周报告" | 生成 AI 报告提示词 |
| "查看当前持仓" | 显示 OPEN 状态仓位 |
| "查看浮动盈亏" | 未实现盈亏 |
| "查看本周盈亏" | 本周统计 |
| "查看资金费用" | FUNDING_FEE 明细 |
| "启动监听" | 启动 WebSocket |
| "停止监听" | 停止 WebSocket |

## 安全建议

- .env 文件已加入 .gitignore
- API 只开「读取」权限
- 设置 IP 白名单
- 使用子账户隔离风险

## 新增功能

### BNB 手续费折算
自动将 BNB 手续费转换为 USDT：
```python
commission_usdt = commission_bnb * bnb_price
```
- BNB 价格缓存 5 分钟
- commission_usdt 字段存储折算后金额

### 强平订单标记
自动检测强平订单：
- 通过 `/fapi/v1/forceOrders` 接口获取
- trade 表 `is_liquidation` 字段标记
- position_history 表 `is_liquidation` 字段标记

### 浮动盈亏
实时查看未实现盈亏：
```bash
python3 scripts/binance_sync.py --unrealized
```

WebSocket 实时推送：
- `ACCOUNT_UPDATE` 事件
- 包含 entry_price、mark_price、unrealized_pnl
- 计算 ROI (收益率)

## 已知问题

- 网络不稳定时可能遇到 SSL 错误，需要重试
- BNB 价格缓存 5 分钟，可能有轻微误差
