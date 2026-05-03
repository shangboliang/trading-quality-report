#!/usr/bin/env python3
"""
仓位构建器 - 从 Trade 流水推算 PositionHistory

状态机逻辑：
1. 按 symbol + position_side 分组遍历 Trade
2. 跟踪持仓数量 (qty)
3. 当 qty 从 0 变为 > 0：开仓
4. 当 qty 从 > 0 变为 0：平仓
"""

import uuid
from datetime import datetime
from collections import defaultdict

from db import get_trades, insert_position, update_order_position, get_connection

# 常量
FLOAT_EPSILON = 1e-10  # 浮点数比较精度
DEFAULT_LIMIT = 1000


def build_positions(symbol: str = None):
    """从 Trade 推算 PositionHistory"""
    
    trades = get_trades(symbol=symbol)
    
    if not trades:
        print("No trades found")
        return []
    
    # 按 symbol + position_side 分组
    groups = defaultdict(list)
    for t in trades:
        key = (t['symbol'], t['position_side'])
        groups[key].append(t)
    
    positions = []
    
    for (sym, pos_side), group_trades in groups.items():
        group_trades.sort(key=lambda x: x['time'])
        
        current_qty = 0.0
        current_position = None
        order_ids = []
        
        for t in group_trades:
            side = t['side']
            qty = t['qty']
            realized_pnl = t['realized_pnl']
            commission = t['commission']
            
            # 判断开平仓
            is_open = (pos_side == 'LONG' and side == 'BUY') or \
                      (pos_side == 'SHORT' and side == 'SELL')
            is_close = (pos_side == 'LONG' and side == 'SELL') or \
                       (pos_side == 'SHORT' and side == 'BUY')
            
            if is_open:
                if abs(current_qty) < FLOAT_EPSILON:
                    # 新开仓
                    current_position = {
                        'id': str(uuid.uuid4()),
                        'symbol': sym,
                        'position_side': pos_side,
                        'open_time': t['time'],
                        'close_time': None,
                        'entry_price': t['price'],
                        'exit_price': 0.0,
                        'max_qty': 0.0,
                        'total_realized_pnl': 0.0,
                        'total_commission': 0.0,
                        'total_funding_fee': 0.0,
                        'is_liquidation': 0,
                        'status': 'OPEN'
                    }
                    positions.append(current_position)
                    order_ids = []
                
                current_qty += qty
                if current_position:
                    current_position['max_qty'] = max(current_position['max_qty'], abs(current_qty))
                if t['order_id'] not in order_ids:
                    order_ids.append(t['order_id'])
            
            if is_close:
                if current_position:
                    current_position['total_realized_pnl'] += realized_pnl
                    current_position['total_commission'] += commission
                    current_position['exit_price'] = t['price']
                
                current_qty -= qty
                
                if abs(current_qty) < FLOAT_EPSILON:
                    current_qty = 0.0
                    if current_position:
                        current_position['close_time'] = t['time']
                        current_position['status'] = 'CLOSED'
                        
                        # 更新关联的 orders
                        for oid in order_ids:
                            update_order_position(oid, current_position['id'])
                        
                        current_position = None
                        order_ids = []
    
    # 保存 positions
    for p in positions:
        insert_position(p)
    
    print(f"Built {len(positions)} positions")
    return positions


def get_position_summary(symbol: str = None) -> list:
    """获取仓位汇总"""
    with get_connection() as conn:
        query = """
            SELECT 
                ph.*,
                (SELECT COUNT(DISTINCT o.order_id) FROM `order` o 
                 WHERE o.position_id = ph.id) as order_count
            FROM position_history ph
        """
        
        if symbol:
            query += " WHERE ph.symbol = ?"
            rows = conn.execute(query + " ORDER BY ph.open_time DESC", (symbol,)).fetchall()
        else:
            rows = conn.execute(query + " ORDER BY ph.open_time DESC").fetchall()
        
        return [dict(r) for r in rows]


def print_position_report(symbol: str = None):
    """打印仓位报告"""
    positions = get_position_summary(symbol)
    
    if not positions:
        print("No positions found")
        return
    
    print(f"\n{'='*80}")
    print(f"Position History Report")
    print(f"{'='*80}\n")
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    
    for p in positions:
        pnl = p['total_realized_pnl']
        total_pnl += pnl
        
        if pnl > 0:
            wins += 1
        elif p['status'] == 'CLOSED':
            losses += 1
        
        open_time = datetime.fromtimestamp(p['open_time'] / 1000).strftime('%Y-%m-%d %H:%M')
        close_time = datetime.fromtimestamp(p['close_time'] / 1000).strftime('%Y-%m-%d %H:%M') if p['close_time'] else 'OPEN'
        
        direction = '🟢 LONG' if p['position_side'] == 'LONG' else '🔴 SHORT'
        status = '✅' if pnl > 0 else '❌' if p['status'] == 'CLOSED' else '⏳'
        
        print(f"{status} {p['symbol']} {direction}")
        print(f"   Open:  {open_time}")
        print(f"   Close: {close_time}")
        print(f"   Entry: {p['entry_price']:.2f} → Exit: {p['exit_price']:.2f}")
        print(f"   Max Qty: {p['max_qty']:.6f}")
        print(f"   PnL:   {pnl:+.2f} USDT (Commission: {p['total_commission']:.2f})")
        print(f"   Funding: {p['total_funding_fee']:.2f}")
        print(f"   Orders: {p['order_count']}")
        print()
    
    closed_count = wins + losses
    print(f"{'='*80}")
    print(f"Summary: {wins}W / {losses}L | Total PnL: {total_pnl:+.2f} USDT")
    if closed_count > 0:
        print(f"Win Rate: {wins/closed_count*100:.1f}%")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    import sys
    
    symbol = sys.argv[1] if len(sys.argv) > 1 else None
    build_positions(symbol)
    print_position_report(symbol)
