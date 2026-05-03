# Binance U本位合约交易质量报告系统

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

### 1. 安装依赖

```bash
pip install requests websocket-client
```

### 2. 配置 API 密钥

```bash
cp .env.example .env
vim .env
```

填入：
```
BINANCE_API_KEY=你的key
BINANCE_API_SECRET=你的secret
```

### 3. 测试（无需 API）

```bash
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --report
```

### 4. 真实数据同步

```bash
python3 scripts/binance_sync.py
```

## 完整交互流程

### 流程一：首次使用

```bash
# 1. Demo 测试（无需 API Key）
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --days 7 --report

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env 填入密钥

# 3. 探测活跃币种
python3 scripts/binance_sync.py --discover

# 4. 完整同步
python3 scripts/binance_sync.py
```

### 流程二：日常使用

```bash
# 增量同步（拉取新数据）
python3 scripts/binance_sync.py -i --symbol BTCUSDT

# 启动 WebSocket 实时监听
python3 scripts/ws_listener.py

# 查看浮动盈亏
python3 scripts/binance_sync.py --unrealized
```

### 流程三：生成报告

```bash
# 方式 1：直接告诉 AI
"生成本周交易报告"
"生成本月交易报告"
"生成 CFGUSDT 交易报告"

# 方式 2：使用脚本生成提示词
python3 scripts/report_generator.py --period week
python3 scripts/report_generator.py --symbol CFGUSDT --period month
python3 scripts/report_generator.py --period week -o /tmp/prompt.txt
```

### 流程四：数据查询

```bash
# 查看仓位
python3 scripts/position_builder.py
python3 scripts/position_builder.py CFGUSDT

# 查看统计
python3 scripts/trade_stats.py --period week
python3 scripts/trade_stats.py --symbol CFGUSDT --period month
python3 scripts/trade_stats.py --period week --level position

# 查看资金费用
sqlite3 data/trading.db "SELECT * FROM finance_income WHERE income_type='FUNDING_FEE' ORDER BY time DESC LIMIT 20;"
```

## 命令速查表

| 命令 | 说明 |
|------|------|
| `binance_sync.py` | 自动探测并同步所有活跃币种 |
| `binance_sync.py --symbol BTCUSDT` | 同步指定币种 |
| `binance_sync.py --symbols BTCUSDT ETHUSDT` | 同步多个币种 |
| `binance_sync.py -i --symbol BTCUSDT` | 增量同步 |
| `binance_sync.py --discover` | 探测活跃币种 |
| `binance_sync.py --unrealized` | 查看浮动盈亏 |
| `binance_sync.py --income-only` | 只同步资金流水 |
| `binance_sync_demo.py --symbol BTCUSDT --report` | Demo 模式 |
| `position_builder.py` | 构建所有仓位 |
| `position_builder.py CFGUSDT` | 构建指定币种仓位 |
| `trade_stats.py --period week` | 本周统计 |
| `trade_stats.py --symbol CFGUSDT --period month` | 指定币种统计 |
| `report_generator.py --period week` | 生成报告提示词 |
| `ws_listener.py` | 启动 WebSocket 监听 |

## 报告生成说明

### AI 直接生成

当你对我说以下指令时：
```
"生成本周交易报告"
"生成 CFGUSDT 交易报告"
```

我的执行流程：
1. 查询数据库（trade、position_history、finance_income）
2. 计算统计数据（胜率、盈亏比、利润因子）
3. 检测高风险行为（连续亏损、大额亏损）
4. 按严格格式输出报告

报告格式：
- 第一行：**综合评级: X**（A+ ~ F）
- 核心数据概览
- 交易质量优缺
- 潜在隐患与优化空间

### 脚本生成提示词

```bash
# 生成提示词
python3 scripts/report_generator.py --period week

# 保存到文件
python3 scripts/report_generator.py --period week -o /tmp/prompt.txt
```

提示词内容包含：
- 核心数据（总盈亏、胜率、利润因子）
- 币种分布
- 已平仓仓位详情
- 高风险行为检测
- 资金费用明细

可以将提示词发送给其他 AI（如 ChatGPT、Claude）生成报告。

## 数据库查询

```bash
# 进入数据库
sqlite3 data/trading.db

# 查看表
.tables

# 查看交易
SELECT * FROM trade ORDER BY time DESC LIMIT 10;

# 查看仓位
SELECT * FROM position_history WHERE status='CLOSED';

# 查看资金费用
SELECT * FROM finance_income WHERE income_type='FUNDING_FEE';

# 查看强平订单
SELECT * FROM trade WHERE is_liquidation=1;
```

## 四阶段同步流程

### 阶段一：探测 (Discovery)
确定需要同步的币种，避免遍历 600+ 币种

- `GET /fapi/v2/positionRisk` - 当前持仓
- `GET /fapi/v1/income` - 过去 7 天流水

### 阶段二：溯源 (Historical Sync)
填充历史数据

- `GET /fapi/v1/allOrders` - 订单骨架
- `GET /fapi/v1/userTrades` - 成交流水
- `GET /fapi/v1/income` - 资金流水
- `GET /fapi/v1/forceOrders` - 强平订单
- 虚拟起点对齐（解决远古仓位）

### 阶段三：聚合 (Aggregation)
数据升维

- 订单级：avg_price 聚合
- 仓位级：状态机推算 PositionHistory

### 阶段四：接管 (Real-time)
WebSocket 实时监听

- `ORDER_TRADE_UPDATE` - 订单/成交更新
- `ACCOUNT_UPDATE` - 账户/持仓更新（含浮动盈亏）

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
| GET /fapi/v1/forceOrders | 20 | 强平订单 |
| GET /fapi/v1/listenKey | 1 | WebSocket key |

## 目录结构

```
├── scripts/
│   ├── binance_sync.py       # 四阶段同步
│   ├── binance_sync_demo.py  # Demo 模式
│   ├── position_builder.py   # 仓位构建
│   ├── trade_stats.py        # 统计分析
│   ├── report_generator.py   # 报告生成
│   ├── ws_listener.py        # WebSocket 监听
│   ├── db.py                 # 数据库
│   └── env_loader.py         # 环境变量
├── data/
│   └── trading.db            # SQLite 数据库
├── .env.example              # 密钥模板
├── .gitignore
├── README.md
└── SKILL.md
```

## 限制

- 7天跨度：startTime 和 endTime 差值 ≤ 7 天
- 6个月硬极限：最多查过去 6 个月
- fromId 和时间参数互斥

## 关键特性

- ✅ 虚拟起点对齐（解决远古仓位）
- ✅ 幂等性插入（INSERT OR IGNORE）
- ✅ 自动重连 WebSocket
- ✅ 强平订单检测
- ✅ BNB 手续费折算 USDT
- ✅ 资金费率记录
- ✅ 浮动盈亏实时追踪
- ✅ Demo 模式（无需 API）

## 安全

- API 只开「读取」权限
- 设置 IP 白名单
- 使用子账户隔离
- .env 不提交 git

## 常见问题

**Q: 如何测试？**
```bash
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --report
```

**Q: 如何增量同步？**
```bash
python3 scripts/binance_sync.py -i --symbol BTCUSDT
```

**Q: 如何查看浮动盈亏？**
```bash
python3 scripts/binance_sync.py --unrealized
```

**Q: 如何生成报告？**
```
告诉 AI: "生成本周交易报告"
```

**Q: 如何查看资金费用？**
```bash
sqlite3 data/trading.db "SELECT * FROM finance_income WHERE income_type='FUNDING_FEE';"
```
