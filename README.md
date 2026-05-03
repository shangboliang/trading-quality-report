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
# 一键同步
python3 scripts/binance_sync.py

# 增量同步
python3 scripts/binance_sync.py -i --symbol BTCUSDT

# 查看浮动盈亏
python3 scripts/binance_sync.py --unrealized

# 启动监听
python3 scripts/ws_listener.py

# Demo 模式（无需 API）
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --report
```

## 生成报告

直接告诉我：

```
"生成本周交易报告"
"生成本月交易报告"
"生成 CFGUSDT 交易报告"
```

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
