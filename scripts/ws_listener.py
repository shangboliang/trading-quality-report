#!/usr/bin/env python3
"""
WebSocket 实时监听器

监听事件：
- ORDER_TRADE_UPDATE: 订单/成交更新
- ACCOUNT_UPDATE: 账户/持仓更新（含浮动盈亏）

用法:
  python3 ws_listener.py
"""

import os
import sys
import json
import time
import threading
from pathlib import Path

import websocket
import requests

from env_loader import get_api_credentials
from db import insert_trades, insert_orders, get_connection
from position_builder import build_positions

WS_URL = "wss://fstream.binance.com/ws"


class FuturesListener:
    """U本位合约 WebSocket 监听器"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws = None
        self.listen_key = None
        self._running = False
        self._reconnect_delay = 1
        self._positions = {}  # 实时持仓缓存
    
    def _get_listen_key(self) -> str:
        """获取 listenKey"""
        url = "https://fapi.binance.com/fapi/v1/listenKey"
        headers = {"X-MBX-APIKEY": self.api_key}
        resp = requests.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()['listenKey']
    
    def _keepalive(self):
        """续期 listenKey（每 30 分钟）"""
        while self._running:
            time.sleep(30 * 60)
            try:
                url = "https://fapi.binance.com/fapi/v1/listenKey"
                headers = {"X-MBX-APIKEY": self.api_key}
                requests.put(url, headers=headers)
            except Exception as e:
                print(f"Keepalive failed: {e}", file=sys.stderr)
    
    def _on_message(self, ws, message):
        """处理消息"""
        try:
            data = json.loads(message)
            
            if data.get('e') == 'ORDER_TRADE_UPDATE':
                self._handle_order_update(data)
            elif data.get('e') == 'ACCOUNT_UPDATE':
                self._handle_account_update(data)
                
        except Exception as e:
            print(f"Message error: {e}", file=sys.stderr)
    
    def _handle_order_update(self, data: dict):
        """处理订单更新"""
        order = data['o']
        
        symbol = order['s']
        order_id = order['i']
        side = order['S']
        position_side = order['ps']
        order_type = order['o']
        status = order['X']
        price = order['p']
        qty = order['q']
        
        # 状态映射
        status_map = {
            'NEW': '新建',
            'FILLED': '完全成交',
            'PARTIALLY_FILLED': '部分成交',
            'CANCELED': '已取消',
            'EXPIRED': '已过期',
            'REJECTED': '已拒绝',
        }
        
        # 订单类型映射
        type_map = {
            'LIMIT': '限价',
            'MARKET': '市价',
            'STOP': '止损',
            'TAKE_PROFIT': '止盈',
            'LIQUIDATION': '强平',
        }
        
        order_type_str = type_map.get(order_type, order_type)
        is_liquidation = order_type == 'LIQUIDATION'
        
        print(f"[ORDER] {symbol} {side} {position_side} {order_type_str} {status_map.get(status, status)}", 
              file=sys.stderr)
        
        if is_liquidation:
            print(f"  ⚠️ LIQUIDATION DETECTED!", file=sys.stderr)
        
        # 如果有成交
        if order.get('x') == 'TRADE':
            realized_pnl = float(order.get('rp', '0'))
            commission = float(order.get('n', '0'))
            
            trade = {
                'id': order['t'],
                'orderId': order_id,
                'symbol': symbol,
                'side': side,
                'positionSide': position_side,
                'price': order['L'],
                'qty': order['l'],
                'quoteQty': str(float(order['L']) * float(order['l'])),
                'realizedPnl': str(realized_pnl),
                'commission': str(commission),
                'commissionAsset': order.get('N', 'USDT'),
                'maker': order.get('m', False),
                'time': order['T'],
                'is_liquidation': is_liquidation
            }
            insert_trades([trade])
            
            pnl_str = f"{realized_pnl:+.4f}" if realized_pnl != 0 else "0"
            print(f"  Trade: {trade['qty']} @ {trade['price']} PnL: {pnl_str}", file=sys.stderr)
    
    def _handle_account_update(self, data: dict):
        """处理账户更新"""
        account = data['a']
        
        # 更新持仓
        for pos in account.get('P', []):
            symbol = pos['s']
            position_side = pos['ps']
            qty = float(pos['pa'])
            entry_price = float(pos['ep'])
            unrealized_pnl = float(pos['up'])
            margin_type = pos.get('mt', 'cross')
            
            # 更新缓存
            key = f"{symbol}_{position_side}"
            self._positions[key] = {
                'symbol': symbol,
                'position_side': position_side,
                'qty': qty,
                'entry_price': entry_price,
                'unrealized_pnl': unrealized_pnl,
                'margin_type': margin_type,
                'updated_at': int(time.time() * 1000)
            }
            
            if abs(qty) > 1e-10:
                direction = 'LONG' if qty > 0 else 'SHORT'
                print(f"[POSITION] {symbol} {direction}: {abs(qty):.6f} @ {entry_price:.2f} PnL: {unrealized_pnl:+.4f}",
                      file=sys.stderr)
        
        # 更新钱包余额
        for asset in account.get('B', []):
            if asset['a'] == 'USDT':
                balance = float(asset['wb'])
                available = float(asset['cw'])
                print(f"[WALLET] USDT: {balance:.2f} (Available: {available:.2f})", file=sys.stderr)
    
    def get_positions(self) -> list:
        """获取当前持仓（从缓存）"""
        return [p for p in self._positions.values() if abs(p['qty']) > 1e-10]
    
    def _on_error(self, ws, error):
        """错误处理"""
        print(f"WebSocket error: {error}", file=sys.stderr)
    
    def _on_close(self, ws, close_status_code, close_msg):
        """连接关闭"""
        print(f"WebSocket closed: {close_status_code}", file=sys.stderr)
        
        if self._running:
            # 自动重连
            delay = min(self._reconnect_delay * 2, 60)
            print(f"Reconnecting in {delay}s...", file=sys.stderr)
            time.sleep(delay)
            self._reconnect_delay = delay
            self.start()
    
    def _on_open(self, ws):
        """连接建立"""
        print("WebSocket connected", file=sys.stderr)
        print("Listening for ORDER_TRADE_UPDATE and ACCOUNT_UPDATE...", file=sys.stderr)
        self._reconnect_delay = 1
    
    def start(self):
        """启动监听"""
        self._running = True
        
        try:
            # 获取 listenKey
            self.listen_key = self._get_listen_key()
            
            # 启动 keepalive 线程
            threading.Thread(target=self._keepalive, daemon=True).start()
            
            # 建立连接
            ws_url = f"{WS_URL}/{self.listen_key}"
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open
            )
            
            print("Starting WebSocket listener...", file=sys.stderr)
            self.ws.run_forever()
            
        except Exception as e:
            print(f"Start failed: {e}", file=sys.stderr)
            if self._running:
                time.sleep(5)
                self.start()
    
    def stop(self):
        """停止监听"""
        self._running = False
        if self.ws:
            self.ws.close()


def main():
    api_key, api_secret = get_api_credentials()
    
    if not api_key or not api_secret:
        print("Error: Set BINANCE_API_KEY and BINANCE_API_SECRET", file=sys.stderr)
        sys.exit(1)
    
    listener = FuturesListener(api_key, api_secret)
    
    try:
        listener.start()
    except KeyboardInterrupt:
        print("\nStopping...", file=sys.stderr)
        listener.stop()


if __name__ == "__main__":
    main()
