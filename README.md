# Binance U本位合约交易质量报告

四阶段同步：探测 → 溯源 → 聚合 → 接管

## 架构

```
Position (顶层) ← 状态机推算
    │ 1:N
    ▼
Order (中层) ← trades 按 order_id 聚合
    │ 1:N
    ▼
Trade (底层) ← /fapi/v1/userTrades

+ finance_income (资金费率、划转、返佣)
+ WebSocket (实时接管)
```

## 快速开始

### 1. 配置 API 密钥

```bash
cp .env.example .env
vim .env
```

### 2. 完整同步

```bash
# 一键同步（四阶段自动执行）
python3 scripts/binance_sync.py --symbol BTCUSDT

# 或指定多个币种
python3 scripts/binance_sync.py --symbols BTCUSDT ETHUSDT
```

### 3. 启动实时监听

```bash
python3 scripts/ws_listener.py
```

### 4. 生成统计报告

```bash
python3 scripts/trade_stats.py --symbol BTCUSDT --period week
```

## 命令速查

| 命令 | 说明 |
|------|------|
| `binance_sync.py --discover` | 探测活跃币种 |
| `binance_sync.py --symbol BTCUSDT` | 完整同步 |
| `binance_sync.py --symbol BTCUSDT -i` | 增量同步 |
| `binance_sync.py --symbol BTCUSDT --income-only` | 只同步资金流水 |
| `position_builder.py BTCUSDT` | 构建仓位历史 |
| `ws_listener.py` | WebSocket 实时监听 |
| `trade_stats.py --period week` | 本周统计 |
| `trade_stats.py --period month` | 本月统计 |
| `trade_stats.py --demo` | 演示模式 |

## 四阶段流程

### 阶段一：探测 (Discovery)
确定需要同步的币种，避免遍历 600+ 币种

- `GET /fapi/v2/positionRisk` - 当前持仓
- `GET /fapi/v1/income` - 过去 7 天流水

### 阶段二：溯源 (Historical Sync)
填充历史数据

- `GET /fapi/v1/allOrders` - 订单骨架
- `GET /fapi/v1/userTrades` - 成交流水
- `GET /fapi/v1/income` - 资金流水
- 虚拟起点对齐（解决远古仓位）

### 阶段三：聚合 (Aggregation)
数据升维

- 订单级：avg_price 聚合
- 仓位级：状态机推算 PositionHistory

### 阶段四：接管 (Real-time)
WebSocket 实时监听

- `ORDER_TRADE_UPDATE` - 订单/成交
- `ACCOUNT_UPDATE` - 账户/持仓

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

## 目录结构

```
├── scripts/
│   ├── binance_sync.py       # 四阶段同步
│   ├── position_builder.py   # 仓位构建
│   ├── trade_stats.py        # 统计分析
│   ├── ws_listener.py        # WebSocket 监听
│   ├── db.py                 # 数据库
│   └── env_loader.py         # 环境变量
├── data/
│   └── trading.db            # SQLite 数据库
├── .env.example              # 密钥模板
├── .gitignore
├── README.md                 # 本文档
└── SKILL.md                  # AI 详细文档
```

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

## 关键特性

- 虚拟起点对齐（解决远古仓位）
- 幂等性插入（INSERT OR IGNORE）
- 自动重连 WebSocket
- 强平标志识别
- BNB 手续费折算
- 资金费率记录

## 安全

- API 只开「读取」权限
- 设置 IP 白名单
- 使用子账户隔离
- .env 不提交 git
