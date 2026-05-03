---
name: trading-quality-report
description: Binance U本位合约交易质量报告系统 - 四阶段同步、三层架构、WebSocket 实时监听
version: 6.1.0
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

## 环境准备

```bash
pip install requests websocket-client
```

### API 密钥配置

```bash
cp .env.example .env
vim .env
```

## 使用方式

### 一键同步（自动探测活跃币种）

```bash
python3 scripts/binance_sync.py
```

### 指定币种同步

```bash
python3 scripts/binance_sync.py --symbol BTCUSDT
python3 scripts/binance_sync.py --symbols BTCUSDT ETHUSDT
```

### 增量同步

```bash
python3 scripts/binance_sync.py -i --symbol BTCUSDT
```

### 查看浮动盈亏

```bash
python3 scripts/binance_sync.py --unrealized
```

### 启动监听

```bash
python3 scripts/ws_listener.py
```

### Demo 模式（无需 API）

```bash
python3 scripts/binance_sync_demo.py --symbol BTCUSDT --report
```

## 报告生成

### 方式一：直接告诉我

```
"生成本周交易报告"
"生成本月交易报告"
"生成 CFGUSDT 交易报告"
```

我会：
1. 查询数据库
2. 计算统计数据
3. 按严格格式输出报告

报告格式：
- 第一行：**综合评级: X**（A+ ~ F）
- 核心数据概览
- 交易质量优缺
- 潜在隐患与优化空间

### 方式二：脚本生成提示词

```bash
python3 scripts/report_generator.py --period week
```

用于导出提示词给其他 AI 使用。

## 常用提示语

| 提示语 | 说明 |
|--------|------|
| "同步合约数据" | 一键同步（自动探测） |
| "增量同步" | 只拉取新数据 |
| "生成本周交易报告" | AI 直接输出报告 |
| "查看浮动盈亏" | 未实现盈亏 |
| "查看资金费用" | FUNDING_FEE 明细 |
| "启动监听" | 启动 WebSocket |

## 数据库表

| 表 | 说明 | 来源 |
|----|------|------|
| position_history | 仓位历史 | 状态机推算 |
| order | 逻辑订单 | trades 聚合 |
| trade | 撮合流水 | /fapi/v1/userTrades |
| finance_income | 资金流水 | /fapi/v1/income |

## 资金流水类型

| 类型 | 说明 |
|------|------|
| FUNDING_FEE | 资金费率（每 8 小时） |
| TRANSFER | 钱包划转 |
| COMMISSION | 手续费返佣 |
| REALIZED_PNL | 已实现盈亏 |

## API 接口

| 接口 | 说明 |
|------|------|
| GET /fapi/v2/positionRisk | 当前持仓 |
| GET /fapi/v1/income | 收入流水 |
| GET /fapi/v1/allOrders | 所有订单 |
| GET /fapi/v1/userTrades | 成交记录 |
| GET /fapi/v1/forceOrders | 强平订单 |

## API Limit

| 接口 | 最大 limit |
|------|-----------|
| /fapi/v1/userTrades | 1000 |
| /fapi/v1/allOrders | 1000 |
| /fapi/v1/income | 1000 |
| /fapi/v1/forceOrders | 100 |

## Rate Limit 处理

### API 权重限制
- IP 限制：每分钟 2400 权重
- 响应头：`X-MBX-USED-WEIGHT-1M` 显示当前权重
- 超限返回 429，IP 封禁返回 418

### 统一请求函数
```python
def api_request(session, method, url, params=None, max_retries=3):
    """处理 429/418 错误，指数退避重试"""
    for attempt in range(max_retries + 1):
        resp = session.get(url, params=params)
        
        if resp.status_code == 200:
            _check_weight(resp)  # 检查权重使用情况
            return resp
        elif resp.status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', 60))
            time.sleep(retry_after + 1)
            continue
        elif resp.status_code == 418:
            raise IPBanError("IP banned")
    
    raise RateLimitError("Max retries exceeded")
```

### 权重检查
```python
def _check_weight(response):
    """80% 阈值警告，90% 以上自动等待"""
    used_weight = response.headers.get('X-MBX-USED-WEIGHT-1M')
    if used_weight:
        weight = float(used_weight)
        if weight > 2400 * 0.9:
            time.sleep(2)
        elif weight > 2400 * 0.8:
            time.sleep(1)
```

## 关键特性

- BNB 手续费自动折算 USDT
- 强平订单自动检测标记
- 虚拟起点对齐（解决远古仓位）
- 浮动盈亏实时追踪
- Rate Limit 自动处理（429/418）

## 重要提醒

- `python3 scripts/binance_sync.py` 无需指定 --symbol，自动探测活跃币种
- 推送代码前必须经过用户同意
- 命令保持简洁，避免冗余参数

## Git 推送

**必须经过用户同意才能推送代码到远程仓库。**

```bash
# 提交
git add -A && git commit -m "message"

# 等待用户同意后推送
git push
```

## 安全建议

- .env 文件已加入 .gitignore
- API 只开「读取」权限
- 设置 IP 白名单
- 使用子账户隔离风险
