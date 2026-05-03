# Binance U本位合约交易质量报告系统

四阶段同步：探测 → 溯源 → 聚合 → 接管

## 快速开始

```bash
# 安装依赖
pip install requests websocket-client

# 配置 API 密钥
cp .env.example .env
vim .env

# 一键同步（自动探测活跃币种，过去 7 天）
python3 scripts/binance_sync.py
```

## 常用命令

```bash
# 一键同步（四阶段自动执行）
python3 scripts/binance_sync.py --symbol BTCUSDT

# 或指定多个币种
python3 scripts/binance_sync.py --symbols BTCUSDT ETHUSDT

# 增量同步（只拉取新数据）
python3 scripts/binance_sync.py -i --symbol BTCUSDT

# 查看浮动盈亏
python3 scripts/binance_sync.py --unrealized

# 启动 WebSocket 实时监听
python3 scripts/ws_listener.py

# Demo 模式（无需 API Key）
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --report
```

## 生成报告

直接告诉我：

```
"生成本周交易报告"
"生成本月交易报告"
"生成 CFGUSDT 交易报告"
```

我会查询数据库，按严格格式输出报告：
- **综合评级**（A+ ~ F）
- 核心数据概览
- 交易质量优缺
- 潜在隐患与优化空间

## 架构

```
Position (顶层) ← 状态机推算
    │ 1:N
    ▼
Order (中层) ← trades 聚合
    │ 1:N
    ▼
Trade (底层) ← /fapi/v1/userTrades

+ finance_income (资金费率)
+ WebSocket (实时监听)
```

## 关键特性

- ✅ 自动探测活跃币种
- ✅ BNB 手续费折算 USDT
- ✅ 强平订单检测
- ✅ 浮动盈亏追踪
- ✅ 资金费率记录
- ✅ WebSocket 实时监听
- ✅ 虚拟起点对齐（远古仓位）

## 目录结构

```
scripts/
├── binance_sync.py       # 数据同步
├── binance_sync_demo.py  # Demo 模式
├── position_builder.py   # 仓位构建
├── trade_stats.py        # 统计分析
├── report_generator.py   # 报告生成
├── ws_listener.py        # WebSocket 监听
├── db.py                 # 数据库
└── env_loader.py         # 环境变量
```
