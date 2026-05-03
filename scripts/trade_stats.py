#!/usr/bin/env python3
"""
交易统计工具 - 基于三层架构

数据来源：
- Trade (底层) - 单笔成交明细
- Position (顶层) - 仓位周期统计

用法:
  python3 trade_stats.py --symbol BTCUSDT --period week
  python3 trade_stats.py --symbol BTCUSDT --period month --level position
  python3 trade_stats.py --demo --symbol BTCUSDT --period week
"""

import sys
import json
import random
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from db import get_trades, get_positions, get_trade_stats

DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"


def get_date_range(period: str) -> tuple:
    today = datetime.now()
    
    if period == 'yesterday':
        d = today - timedelta(days=1)
        return d.strftime('%Y-%m-%d'), d.strftime('%Y-%m-%d')
    elif period == 'week':
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return monday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')
    elif period == 'month':
        first = today.replace(day=1)
        if today.month == 12:
            last = today.replace(year=today.year+1, month=1, day=1) - timedelta(days=1)
        else:
            last = today.replace(month=today.month+1, day=1) - timedelta(days=1)
        return first.strftime('%Y-%m-%d'), last.strftime('%Y-%m-%d')
    elif period == 'last_week':
        last_sunday = today - timedelta(days=today.weekday()+1)
        last_monday = last_sunday - timedelta(days=6)
        return last_monday.strftime('%Y-%m-%d'), last_sunday.strftime('%Y-%m-%d')
    elif period == 'last_month':
        first_this = today.replace(day=1)
        last_last = first_this - timedelta(days=1)
        first_last = last_last.replace(day=1)
        return first_last.strftime('%Y-%m-%d'), last_last.strftime('%Y-%m-%d')
    elif period == 'today':
        return today.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')
    else:
        raise ValueError(f"Unknown period: {period}")


def calculate_trade_stats(trades: list, initial_capital: float = 10000.0) -> dict:
    """基于 Trade 计算统计"""
    if not trades:
        return {}
    
    pnls = [t['realized_pnl'] for t in trades]
    profits = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total_profit = sum(profits)
    total_loss = abs(sum(losses))
    total_pnl = total_profit - total_loss
    
    wins = len(profits)
    losses_count = len(losses)
    total = wins + losses_count
    
    win_rate = (wins / total * 100) if total > 0 else 0
    avg_win = total_profit / wins if wins > 0 else 0
    avg_loss = total_loss / losses_count if losses_count > 0 else 1
    profit_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else 0
    
    return {
        'total_trades': total,
        'wins': wins,
        'losses': losses_count,
        'total_pnl': round(total_pnl, 2),
        'total_profit': round(total_profit, 2),
        'total_loss': round(total_loss, 2),
        'win_rate': round(win_rate, 1),
        'profit_loss_ratio': profit_loss_ratio,
        'profit_factor': profit_factor,
        'return_rate': round((total_pnl / initial_capital) * 100, 2),
        'avg_pnl': round(total_pnl / total, 2) if total > 0 else 0,
        'max_win': round(max(profits) if profits else 0, 2),
        'max_loss': round(abs(min(losses)) if losses else 0, 2),
    }


def calculate_position_stats(positions: list, initial_capital: float = 10000.0) -> dict:
    """基于 Position 计算统计"""
    if not positions:
        return {}
    
    closed = [p for p in positions if p['status'] == 'CLOSED']
    
    if not closed:
        return {'total_positions': 0}
    
    pnls = [p['total_realized_pnl'] for p in closed]
    profits = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total_profit = sum(profits)
    total_loss = abs(sum(losses))
    total_pnl = total_profit - total_loss
    
    wins = len(profits)
    losses_count = len(losses)
    total = wins + losses_count
    
    # 按方向统计
    long_positions = [p for p in closed if p['position_side'] == 'LONG']
    short_positions = [p for p in closed if p['position_side'] == 'SHORT']
    long_pnl = sum(p['total_realized_pnl'] for p in long_positions)
    short_pnl = sum(p['total_realized_pnl'] for p in short_positions)
    
    return {
        'total_positions': total,
        'open_positions': len(positions) - len(closed),
        'long_positions': len(long_positions),
        'short_positions': len(short_positions),
        'wins': wins,
        'losses': losses_count,
        'total_pnl': round(total_pnl, 2),
        'long_pnl': round(long_pnl, 2),
        'short_pnl': round(short_pnl, 2),
        'win_rate': round(wins / total * 100, 1) if total > 0 else 0,
        'profit_factor': round(total_profit / total_loss, 2) if total_loss > 0 else 0,
        'return_rate': round((total_pnl / initial_capital) * 100, 2),
        'avg_pnl': round(total_pnl / total, 2) if total > 0 else 0,
    }


def get_highlights(trades: list) -> dict:
    """计算亮点"""
    pnls = [t['realized_pnl'] for t in trades]
    
    return {
        'cut_loss': [{'pnl': round(p, 2)} for p in pnls if p < 0 and abs(p) < 5],
        'let_profit_run': [{'pnl': round(p, 2)} for p in pnls if p > 15],
    }


def get_risks(trade_stats: dict, position_stats: dict = None) -> list:
    """检测风险"""
    risks = []
    
    total = trade_stats.get('total_trades', 0)
    if total > 100:
        risks.append({'type': 'overtrading', 'message': f'交易 {total} 笔，超过 100 笔阈值'})
    
    win_rate = trade_stats.get('win_rate', 0)
    if win_rate < 40:
        risks.append({'type': 'low_win_rate', 'message': f'胜率 {win_rate}%，低于 40% 阈值'})
    
    max_loss = trade_stats.get('max_loss', 0)
    if max_loss > 50:
        risks.append({'type': 'large_loss', 'message': f'最大单笔亏损 {max_loss} USDT'})
    
    pf = trade_stats.get('profit_factor', 0)
    if 0 < pf < 1.5:
        risks.append({'type': 'low_profit_factor', 'message': f'利润因子 {pf}，低于 1.5'})
    
    return risks


def generate_demo_stats(symbol: str, start_date: str, end_date: str) -> dict:
    """生成模拟数据"""
    base_time = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
    
    trades = []
    for i in range(50):
        if random.random() < 0.6:
            pnl = random.uniform(0.5, 15)
        else:
            pnl = -random.uniform(0.5, 10)
        
        trades.append({
            'id': 100000 + i,
            'realized_pnl': round(pnl, 4),
            'commission': round(random.uniform(0.01, 0.1), 4),
            'time': base_time + random.randint(0, days * 86400000),
        })
    
    trade_stats = calculate_trade_stats(trades)
    highlights = get_highlights(trades)
    risks = get_risks(trade_stats)
    
    # 权益曲线
    initial_capital = 10000.0
    equity = initial_capital
    equity_curve = [{'time': 'start', 'equity': equity}]
    for t in sorted(trades, key=lambda x: x['time']):
        equity += t['realized_pnl']
        equity_curve.append({
            'time': datetime.fromtimestamp(t['time'] / 1000).strftime('%Y-%m-%d %H:%M'),
            'equity': round(equity, 2)
        })
    
    return {
        'symbol': symbol,
        'start_date': start_date,
        'end_date': end_date,
        'is_demo': True,
        'trade_stats': trade_stats,
        'highlights': highlights,
        'risks': risks,
        'equity_curve': equity_curve,
    }


def main():
    parser = argparse.ArgumentParser(description='Trading statistics')
    parser.add_argument('--symbol', '-s', default='BTCUSDT', help='Trading pair')
    parser.add_argument('--period', '-p', 
                       choices=['today', 'yesterday', 'week', 'month', 'last_week', 'last_month'],
                       help='Period')
    parser.add_argument('--start', help='Start date')
    parser.add_argument('--end', help='End date')
    parser.add_argument('--level', choices=['trade', 'position'], default='trade',
                       help='Stats level: trade (bottom) or position (top)')
    parser.add_argument('--capital', '-c', type=float, default=10000, help='Initial capital')
    parser.add_argument('--demo', action='store_true', help='Demo mode')
    
    args = parser.parse_args()
    
    if args.period:
        start_date, end_date = get_date_range(args.period)
    elif args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        print("Error: Specify --period or --start/--end", file=sys.stderr)
        sys.exit(1)
    
    if args.demo:
        stats = generate_demo_stats(args.symbol, start_date, end_date)
    else:
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000) + 86400000
        
        trades = get_trades(args.symbol, start_ts, end_ts)
        trade_stats = calculate_trade_stats(trades, args.capital)
        highlights = get_highlights(trades)
        risks = get_risks(trade_stats)
        
        position_stats = None
        if args.level == 'position':
            positions = get_positions(args.symbol)
            position_stats = calculate_position_stats(positions, args.capital)
        
        # 权益曲线
        equity = args.capital
        equity_curve = [{'time': 'start', 'equity': equity}]
        for t in sorted(trades, key=lambda x: x['time']):
            equity += t['realized_pnl']
            equity_curve.append({
                'time': datetime.fromtimestamp(t['time'] / 1000).strftime('%Y-%m-%d %H:%M'),
                'equity': round(equity, 2)
            })
        
        stats = {
            'symbol': args.symbol,
            'start_date': start_date,
            'end_date': end_date,
            'is_demo': False,
            'trade_stats': trade_stats,
            'position_stats': position_stats,
            'highlights': highlights,
            'risks': risks,
            'equity_curve': equity_curve,
        }
    
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
