#!/usr/bin/env python3
"""
Binance U本位合约同步工具

四阶段流程：
1. 探测 (Discovery) - 确定需要同步的币种
2. 溯源 (Historical Sync) - 填充历史数据
3. 聚合 (Aggregation) - 数据升维
4. 接管 (Real-time) - WebSocket 监听

用法:
  # 完整同步（四阶段）
  python3 binance_sync.py --symbol BTCUSDT
  
  # 增量同步
  python3 binance_sync.py --symbol BTCUSDT --incremental
  
  # 只同步资金流水
  python3 binance_sync.py --symbol BTCUSDT --income-only
"""

import sys
import json
import time
import hmac
import hashlib
import argparse
from datetime import datetime, timedelta
from urllib.parse import urlencode
from pathlib import Path

import requests

from env_loader import get_api_credentials
from db import (
    init_db, insert_trades, insert_orders, insert_income,
    insert_virtual_trade, get_latest_trade_id, get_trades,
    insert_position, update_order_position, get_connection
)
from position_builder import build_positions

# 常量定义
BASE_URL = "https://fapi.binance.com"
MS_PER_DAY = 86400000  # 1天的毫秒数
DEFAULT_LIMIT = 1000   # userTrades/income 默认 limit，最大 1000
MAX_LIMIT = 1000       # allOrders 最大 limit
FORCE_ORDER_LIMIT = 100  # forceOrders 最大 limit
RECV_WINDOW = 5000     # 接收窗口
FLOAT_EPSILON = 1e-10  # 浮点数比较精度


def sign_params(params: dict, api_secret: str) -> dict:
    """HMAC SHA256 签名"""
    params['timestamp'] = int(time.time() * 1000)
    params['recvWindow'] = RECV_WINDOW
    query_string = urlencode(params)
    signature = hmac.new(
        api_secret.encode(),
        query_string.encode(),
        hashlib.sha256
    ).hexdigest()
    params['signature'] = signature
    return params


# ==========================================
# BNB 价格缓存
# ==========================================

_bnb_price_cache = {'price': None, 'time': 0}

def get_bnb_price(api_key: str = None, api_secret: str = None) -> float:
    """获取 BNB 当前价格（带缓存）"""
    now = time.time()
    
    # 缓存 5 分钟
    if _bnb_price_cache['price'] and now - _bnb_price_cache['time'] < 300:
        return _bnb_price_cache['price']
    
    try:
        resp = requests.get(f"{BASE_URL}/fapi/v1/ticker/price", params={'symbol': 'BNBUSDT'})
        resp.raise_for_status()
        price = float(resp.json()['price'])
        
        _bnb_price_cache['price'] = price
        _bnb_price_cache['time'] = now
        
        return price
    except Exception as e:
        print(f"Get BNB price failed: {e}", file=sys.stderr)
        return 600.0  # 默认价格


def convert_commission_to_usdt(commission: float, commission_asset: str, 
                                api_key: str = None, api_secret: str = None) -> float:
    """将手续费转换为 USDT"""
    if commission_asset == 'USDT':
        return commission
    elif commission_asset == 'BNB':
        bnb_price = get_bnb_price(api_key, api_secret)
        return commission * bnb_price
    else:
        # 其他币种暂不处理
        return commission


# ==========================================
# 强平检测
# ==========================================

def detect_liquidation(trade: dict) -> bool:
    """检测是否为强平订单"""
    # 强平订单特征：
    # 1. realizedPnl 通常为负数且金额较大
    # 2. 可能有特殊的标识
    
    # 检查是否为负数且金额较大
    pnl = float(trade.get('realizedPnl', 0))
    qty = float(trade.get('qty', 0))
    price = float(trade.get('price', 0))
    
    # 计算仓位价值
    position_value = qty * price
    
    # 如果亏损超过仓位价值的 50%，可能是强平
    if pnl < 0 and abs(pnl) > position_value * 0.5:
        return True
    
    return False


# ==========================================
# 阶段一：探测 (Discovery)
# ==========================================

def get_position_risk(api_key: str, api_secret: str) -> list:
    """获取当前持仓风险"""
    session = requests.Session()
    session.headers.update({"X-MBX-APIKEY": api_key})
    
    params = {}
    signed = sign_params(params, api_secret)
    resp = session.get(f"{BASE_URL}/fapi/v2/positionRisk", params=signed)
    resp.raise_for_status()
    
    return [p for p in resp.json() if abs(float(p['positionAmt'])) > FLOAT_EPSILON]


def get_income_history(api_key: str, api_secret: str, 
                       symbol: str = None, income_type: str = None,
                       start_time: int = None, end_time: int = None,
                       limit: int = DEFAULT_LIMIT) -> list:
    """获取收入流水"""
    session = requests.Session()
    session.headers.update({"X-MBX-APIKEY": api_key})
    
    params = {"limit": limit}
    if symbol:
        params['symbol'] = symbol
    if income_type:
        params['incomeType'] = income_type
    if start_time:
        params['startTime'] = start_time
    if end_time:
        params['endTime'] = end_time
    
    signed = sign_params(params.copy(), api_secret)
    resp = session.get(f"{BASE_URL}/fapi/v1/income", params=signed)
    resp.raise_for_status()
    
    return resp.json()


def get_force_orders(api_key: str, api_secret: str, 
                     symbol: str = None, limit: int = FORCE_ORDER_LIMIT) -> list:
    """获取强平订单"""
    session = requests.Session()
    session.headers.update({"X-MBX-APIKEY": api_key})
    
    params = {"limit": limit}
    if symbol:
        params['symbol'] = symbol
    
    signed = sign_params(params.copy(), api_secret)
    resp = session.get(f"{BASE_URL}/fapi/v1/forceOrders", params=signed)
    resp.raise_for_status()
    
    return resp.json()


def discovery(api_key: str, api_secret: str) -> list:
    """阶段一：探测活跃币种"""
    print("Phase 1: Discovery", file=sys.stderr)
    
    active_symbols = set()
    
    # 1. 当前持仓
    positions = get_position_risk(api_key, api_secret)
    for p in positions:
        active_symbols.add(p['symbol'])
    print(f"  Current positions: {len(positions)}", file=sys.stderr)
    
    # 2. 过去 7 天的收入流水
    start_time = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
    incomes = get_income_history(api_key, api_secret, start_time=start_time)
    for inc in incomes:
        if inc.get('symbol'):
            active_symbols.add(inc['symbol'])
    print(f"  Income records (7d): {len(incomes)}", file=sys.stderr)
    
    print(f"  Active symbols: {sorted(active_symbols)}", file=sys.stderr)
    return list(active_symbols)


# ==========================================
# 阶段二：溯源 (Historical Sync)
# ==========================================

def fetch_trades(api_key: str, api_secret: str, symbol: str, 
                 start_time: int = None, end_time: int = None, 
                 from_id: int = None, limit: int = DEFAULT_LIMIT) -> list:
    """拉取成交记录"""
    session = requests.Session()
    session.headers.update({"X-MBX-APIKEY": api_key})
    
    params = {"symbol": symbol, "limit": limit}
    
    if from_id:
        params['fromId'] = from_id
    else:
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
    
    signed = sign_params(params.copy(), api_secret)
    resp = session.get(f"{BASE_URL}/fapi/v1/userTrades", params=signed)
    resp.raise_for_status()
    
    return resp.json()


def fetch_orders(api_key: str, api_secret: str, symbol: str,
                 start_time: int = None, end_time: int = None,
                 limit: int = MAX_LIMIT) -> list:
    """拉取订单记录"""
    session = requests.Session()
    session.headers.update({"X-MBX-APIKEY": api_key})
    
    params = {"symbol": symbol, "limit": limit}
    if start_time:
        params['startTime'] = start_time
    if end_time:
        params['endTime'] = end_time
    
    signed = sign_params(params.copy(), api_secret)
    resp = session.get(f"{BASE_URL}/fapi/v1/allOrders", params=signed)
    resp.raise_for_status()
    
    return resp.json()


def historical_sync(api_key: str, api_secret: str, symbol: str):
    """阶段二：历史数据同步"""
    print(f"\nPhase 2: Historical Sync - {symbol}", file=sys.stderr)
    
    # 计算时间范围（过去 7 天）
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = end_time - 7 * MS_PER_DAY
    
    # 1. 拉取订单
    print("  Fetching orders...", file=sys.stderr)
    orders = fetch_orders(api_key, api_secret, symbol, start_time, end_time)
    if orders:
        saved = insert_orders(orders)
        print(f"    Orders: {saved}", file=sys.stderr)
    
    # 2. 拉取成交
    print("  Fetching trades...", file=sys.stderr)
    latest_id = get_latest_trade_id(symbol)
    from_id = latest_id + 1 if latest_id else None
    
    total_trades = 0
    while True:
        trades = fetch_trades(api_key, api_secret, symbol, from_id=from_id)
        if not trades:
            break
        
        # 处理手续费折算和强平标记
        for t in trades:
            # BNB 手续费折算
            if t.get('commissionAsset') == 'BNB':
                t['commission_usdt'] = convert_commission_to_usdt(
                    float(t['commission']), 'BNB', api_key, api_secret
                )
            else:
                t['commission_usdt'] = float(t.get('commission', 0))
            
            # 强平检测
            t['is_liquidation'] = detect_liquidation(t)
        
        saved = insert_trades(trades)
        total_trades += saved
        
        from_id = trades[-1]['id'] + 1
        if len(trades) < DEFAULT_LIMIT:
            break
        time.sleep(0.2)
    
    print(f"    Trades: {total_trades}", file=sys.stderr)
    
    # 3. 拉取资金流水
    print("  Fetching income...", file=sys.stderr)
    incomes = get_income_history(api_key, api_secret, symbol=symbol, 
                                 start_time=start_time)
    if incomes:
        saved = insert_income(incomes)
        print(f"    Income: {saved}", file=sys.stderr)
    
    # 4. 检查强平订单
    print("  Checking liquidations...", file=sys.stderr)
    force_orders = get_force_orders(api_key, api_secret, symbol)
    if force_orders:
        print(f"    Force orders: {len(force_orders)}", file=sys.stderr)
        # 标记强平
        with get_connection() as conn:
            for fo in force_orders:
                conn.execute("""
                    UPDATE trade SET is_liquidation = 1 
                    WHERE order_id = ?
                """, (fo.get('orderId'),))
    
    # 5. 虚拟起点对齐
    print("  Aligning positions...", file=sys.stderr)
    align_virtual_trades(api_key, api_secret, symbol)


def align_virtual_trades(api_key: str, api_secret: str, symbol: str):
    """虚拟起点对齐"""
    # 获取当前持仓
    positions = get_position_risk(api_key, api_secret)
    current_pos = None
    for p in positions:
        if p['symbol'] == symbol:
            current_pos = p
            break
    
    if not current_pos:
        return
    
    position_amt = float(current_pos['positionAmt'])
    entry_price = float(current_pos['entryPrice'])
    
    if abs(position_amt) < FLOAT_EPSILON:
        return
    
    # 计算本地累计持仓
    trades = get_trades(symbol=symbol)
    local_qty = 0.0
    for t in trades:
        if t['position_side'] == 'LONG':
            local_qty += t['qty'] if t['side'] == 'BUY' else -t['qty']
        else:  # SHORT
            local_qty += t['qty'] if t['side'] == 'SELL' else -t['qty']
    
    # 计算缺口
    abs_local = abs(local_qty)
    abs_current = abs(position_amt)
    
    if abs(abs_local - abs_current) > FLOAT_EPSILON:
        # 存在缺口，插入虚拟 Trade
        virtual_qty = abs_current - abs_local
        if virtual_qty > 0:
            print(f"    Virtual trade: {virtual_qty} @ {entry_price}", file=sys.stderr)
            position_side = 'LONG' if position_amt > 0 else 'SHORT'
            insert_virtual_trade(
                symbol=symbol,
                order_id=int(time.time() * 1000),  # 临时 order_id
                position_side=position_side,
                price=entry_price,
                qty=virtual_qty,
                time_ms=int(datetime.now().timestamp() * 1000) - MS_PER_DAY
            )


# ==========================================
# 阶段三：聚合 (Aggregation)
# ==========================================

def aggregate(api_key: str, api_secret: str, symbol: str):
    """阶段三：数据聚合"""
    print(f"\nPhase 3: Aggregation - {symbol}", file=sys.stderr)
    
    # 构建仓位历史
    build_positions(symbol)


# ==========================================
# 浮动盈亏计算
# ==========================================

def get_unrealized_pnl(api_key: str, api_secret: str) -> list:
    """获取浮动盈亏"""
    positions = get_position_risk(api_key, api_secret)
    
    result = []
    for p in positions:
        symbol = p['symbol']
        position_amt = float(p['positionAmt'])
        entry_price = float(p['entryPrice'])
        mark_price = float(p['markPrice'])
        unrealized_pnl = float(p['unRealizedProfit'])
        leverage = int(p.get('leverage', 1))
        margin_type = p.get('marginType', 'cross')
        
        if abs(position_amt) > FLOAT_EPSILON:
            direction = 'LONG' if position_amt > 0 else 'SHORT'
            
            # 计算收益率
            notional = abs(position_amt) * mark_price
            roi = (unrealized_pnl / (notional / leverage)) * 100 if notional > 0 else 0
            
            result.append({
                'symbol': symbol,
                'direction': direction,
                'qty': abs(position_amt),
                'entry_price': entry_price,
                'mark_price': mark_price,
                'unrealized_pnl': unrealized_pnl,
                'leverage': leverage,
                'margin_type': margin_type,
                'notional': notional,
                'roi': roi
            })
    
    return result


# ==========================================
# 完整同步流程
# ==========================================

def full_sync(api_key: str, api_secret: str, symbols: list = None):
    """完整同步流程"""
    init_db()
    
    # 阶段一：探测
    if not symbols:
        symbols = discovery(api_key, api_secret)
    
    if not symbols:
        print("No active symbols found", file=sys.stderr)
        return
    
    # 阶段二 & 三：逐币种同步
    for symbol in symbols:
        historical_sync(api_key, api_secret, symbol)
        time.sleep(0.5)
        aggregate(api_key, api_secret, symbol)
    
    print("\nSync completed!", file=sys.stderr)


def incremental_sync(api_key: str, api_secret: str, symbol: str):
    """增量同步"""
    init_db()
    
    print(f"Incremental sync: {symbol}", file=sys.stderr)
    
    # 只拉取新 trades
    latest_id = get_latest_trade_id(symbol)
    from_id = latest_id + 1 if latest_id else None
    
    if latest_id:
        print(f"  From trade_id: {from_id}", file=sys.stderr)
    
    total_trades = 0
    while True:
        trades = fetch_trades(api_key, api_secret, symbol, from_id=from_id)
        if not trades:
            break
        
        # 处理手续费折算和强平标记
        for t in trades:
            if t.get('commissionAsset') == 'BNB':
                t['commission_usdt'] = convert_commission_to_usdt(
                    float(t['commission']), 'BNB', api_key, api_secret
                )
            else:
                t['commission_usdt'] = float(t.get('commission', 0))
            t['is_liquidation'] = detect_liquidation(t)
        
        saved = insert_trades(trades)
        total_trades += saved
        
        from_id = trades[-1]['id'] + 1
        print(f"  Trades: {saved} (up to id {trades[-1]['id']})", file=sys.stderr)
        
        if len(trades) < DEFAULT_LIMIT:
            break
        time.sleep(0.2)
    
    # 重新聚合
    if total_trades > 0:
        aggregate(api_key, api_secret, symbol)
    
    print(f"Total: {total_trades} new trades", file=sys.stderr)


def income_sync(api_key: str, api_secret: str, symbol: str = None):
    """只同步资金流水"""
    init_db()
    
    print(f"Syncing income...", file=sys.stderr)
    
    # 过去 30 天
    start_time = int((datetime.now() - timedelta(days=30)).timestamp() * 1000)
    incomes = get_income_history(api_key, api_secret, symbol=symbol, start_time=start_time)
    
    if incomes:
        saved = insert_income(incomes)
        print(f"  Income: {saved}", file=sys.stderr)
        
        # 统计
        funding = sum(1 for i in incomes if i['incomeType'] == 'FUNDING_FEE')
        transfer = sum(1 for i in incomes if i['incomeType'] == 'TRANSFER')
        print(f"  FUNDING_FEE: {funding}, TRANSFER: {transfer}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Binance U本位合约数据同步工具',
        epilog='''
示例:
  %(prog)s                              # 自动探测活跃币种并同步
  %(prog)s --symbol BTCUSDT             # 同步指定币种
  %(prog)s --symbols BTCUSDT ETHUSDT    # 同步多个币种
  %(prog)s --symbol BTCUSDT -i          # 增量同步
  %(prog)s --symbol BTCUSDT --income-only  # 只同步资金流水
  %(prog)s --discover                   # 只探测活跃币种
  %(prog)s --unrealized                 # 查看浮动盈亏
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--symbol', '-s', help='指定交易对 (如 BTCUSDT)')
    parser.add_argument('--symbols', nargs='+', help='指定多个交易对')
    parser.add_argument('--incremental', '-i', action='store_true', help='增量同步 (用 fromId)')
    parser.add_argument('--income-only', action='store_true', help='只同步资金流水')
    parser.add_argument('--discover', action='store_true', help='只探测活跃币种')
    parser.add_argument('--unrealized', '-u', action='store_true', help='查看浮动盈亏')
    parser.add_argument('--days', '-d', type=int, default=7, help='同步天数 (默认: 7)')
    
    args = parser.parse_args()
    
    api_key, api_secret = get_api_credentials()
    
    if not api_key or not api_secret:
        print("Error: Set BINANCE_API_KEY and BINANCE_API_SECRET", file=sys.stderr)
        print("  方式1: export BINANCE_API_KEY=xxx", file=sys.stderr)
        print("  方式2: 编辑 .env 文件", file=sys.stderr)
        sys.exit(1)
    
    if args.discover:
        symbols = discovery(api_key, api_secret)
        print(json.dumps(symbols, indent=2))
    elif args.unrealized:
        positions = get_unrealized_pnl(api_key, api_secret)
        if positions:
            print(json.dumps(positions, indent=2))
        else:
            print("No open positions")
    elif args.income_only:
        income_sync(api_key, api_secret, args.symbol)
    elif args.incremental and args.symbol:
        incremental_sync(api_key, api_secret, args.symbol)
    elif args.symbols or args.symbol:
        symbols = args.symbols or [args.symbol]
        full_sync(api_key, api_secret, symbols)
    else:
        # 默认：探测并同步所有活跃币种
        print("未指定币种，自动探测活跃币种...", file=sys.stderr)
        full_sync(api_key, api_secret)


if __name__ == "__main__":
    main()
