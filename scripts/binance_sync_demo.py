#!/usr/bin/env python3
"""
模拟同步工具 - 无需 API Key

生成模拟数据并执行完整流程

用法:
  python3 binance_sync_demo.py --symbol BTCUSDT
  python3 binance_sync_demo.py --symbols BTCUSDT ETHUSDT
"""

import sys
import json
import uuid
import random
import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

from db import init_db, insert_trades, insert_orders, insert_income
from position_builder import build_positions, print_position_report

# 常量
MS_PER_DAY = 86400000


def generate_demo_trades(symbol: str, days: int = 7, count: int = 50) -> list:
    """生成模拟成交数据 - 确保开仓平仓顺序正确"""
    base_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    id_base = int(time.time() * 1000) % 1000000
    
    trades = []
    trade_idx = 0
    
    # 生成多个完整的开仓-平仓周期
    while trade_idx < count:
        # 开仓
        position_side = random.choice(['LONG', 'SHORT'])
        entry_price = 50000 + random.uniform(-5000, 5000)
        qty = random.uniform(0.001, 0.01)
        
        if position_side == 'LONG':
            open_side = 'BUY'
        else:
            open_side = 'SELL'
        
        # 开仓 trade
        open_time = base_time + random.randint(0, days * MS_PER_DAY)
        trades.append({
            'id': id_base + trade_idx,
            'orderId': id_base + 1000 + trade_idx // 3,
            'symbol': symbol,
            'side': open_side,
            'positionSide': position_side,
            'price': str(round(entry_price, 2)),
            'qty': str(round(qty, 6)),
            'quoteQty': str(round(entry_price * qty, 4)),
            'realizedPnl': '0',
            'commission': str(round(entry_price * qty * 0.0004, 4)),
            'commissionAsset': 'USDT',
            'maker': False,
            'time': open_time
        })
        trade_idx += 1
        
        if trade_idx >= count:
            break
        
        # 平仓
        if position_side == 'LONG':
            close_side = 'SELL'
            # 多头：价格上涨盈利
            price_change = random.uniform(-0.02, 0.03)
        else:
            close_side = 'BUY'
            # 空头：价格下跌盈利
            price_change = random.uniform(-0.03, 0.02)
        
        exit_price = entry_price * (1 + price_change)
        realized_pnl = (exit_price - entry_price) * qty if position_side == 'LONG' else (entry_price - exit_price) * qty
        realized_pnl -= entry_price * qty * 0.0004  # 手续费
        
        close_time = open_time + random.randint(60000, 3600000)  # 1分钟到1小时后
        trades.append({
            'id': id_base + trade_idx,
            'orderId': id_base + 1000 + trade_idx // 3,
            'symbol': symbol,
            'side': close_side,
            'positionSide': position_side,
            'price': str(round(exit_price, 2)),
            'qty': str(round(qty, 6)),
            'quoteQty': str(round(exit_price * qty, 4)),
            'realizedPnl': str(round(realized_pnl, 4)),
            'commission': str(round(exit_price * qty * 0.0004, 4)),
            'commissionAsset': 'USDT',
            'maker': False,
            'time': close_time
        })
        trade_idx += 1
    
    # 按时间排序
    trades.sort(key=lambda x: x['time'])
    return trades


def generate_demo_orders(trades: list) -> list:
    """从 trades 聚合 orders"""
    order_map = {}
    
    for t in trades:
        oid = t['orderId']
        if oid not in order_map:
            order_map[oid] = {
                'orderId': oid,
                'symbol': t['symbol'],
                'type': 'MARKET',
                'side': t['side'],
                'positionSide': t['positionSide'],
                'status': 'FILLED',
                'price': '0',
                'origQty': '0',
                'reduceOnly': False,
                'workingType': 'MARK_PRICE',
                'time': t['time'],
                'updateTime': t['time']
            }
        
        # 累加数量
        order_map[oid]['origQty'] = str(float(order_map[oid]['origQty']) + float(t['qty']))
    
    return list(order_map.values())


def generate_demo_income(symbol: str, days: int = 7) -> list:
    """生成模拟资金流水"""
    base_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    
    incomes = []
    
    # 资金费率 (每 8 小时)
    for i in range(days * 3):
        income_time = base_time + i * 8 * 3600000
        incomes.append({
            'symbol': symbol,
            'incomeType': 'FUNDING_FEE',
            'income': str(round(random.uniform(-0.5, 0.5), 4)),
            'asset': 'USDT',
            'time': income_time,
            'tranId': 3000000 + i,
            'tradeId': ''
        })
    
    # 手续费返佣
    for i in range(5):
        incomes.append({
            'symbol': symbol,
            'incomeType': 'COMMISSION',
            'income': str(round(random.uniform(0.01, 0.1), 4)),
            'asset': 'BNB',
            'time': base_time + random.randint(0, days * MS_PER_DAY),
            'tranId': 4000000 + i,
            'tradeId': ''
        })
    
    return incomes


def demo_sync(symbols: list, days: int = 7):
    """模拟同步"""
    init_db()
    
    print(f"Demo sync: {symbols}", file=sys.stderr)
    print(f"Period: {days} days", file=sys.stderr)
    
    for symbol in symbols:
        print(f"\n{'='*40}", file=sys.stderr)
        print(f"Syncing {symbol}...", file=sys.stderr)
        print(f"{'='*40}", file=sys.stderr)
        
        # 1. 生成 trades
        trades = generate_demo_trades(symbol, days, count=50)
        saved = insert_trades(trades)
        print(f"  Trades: {saved}", file=sys.stderr)
        
        # 2. 生成 orders
        orders = generate_demo_orders(trades)
        saved = insert_orders(orders)
        print(f"  Orders: {saved}", file=sys.stderr)
        
        # 3. 生成 income
        incomes = generate_demo_income(symbol, days)
        saved = insert_income(incomes)
        print(f"  Income: {saved}", file=sys.stderr)
        
        # 4. 构建仓位
        print(f"  Building positions...", file=sys.stderr)
        build_positions(symbol)
    
    print(f"\nDemo sync completed!", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='模拟同步工具 - 无需 API Key',
        epilog='''
示例:
  %(prog)s                              # 默认模拟 BTCUSDT
  %(prog)s --symbol BTCUSDT             # 模拟指定币种
  %(prog)s --symbols BTCUSDT ETHUSDT    # 模拟多个币种
  %(prog)s --days 30                    # 模拟过去 30 天
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--symbol', '-s', default='BTCUSDT', help='交易对 (默认: BTCUSDT)')
    parser.add_argument('--symbols', nargs='+', help='多个交易对')
    parser.add_argument('--days', '-d', type=int, default=7, help='模拟天数 (默认: 7)')
    parser.add_argument('--report', '-r', action='store_true', help='生成后显示报告')
    
    args = parser.parse_args()
    
    symbols = args.symbols or [args.symbol]
    
    demo_sync(symbols, args.days)
    
    if args.report:
        for symbol in symbols:
            print_position_report(symbol)


if __name__ == "__main__":
    main()
