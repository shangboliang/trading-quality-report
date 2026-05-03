#!/usr/bin/env python3
"""
交易数据中心：SQLite 完整表结构

架构：
  PositionHistory (顶层) -> Order (中层) -> Trade (底层)
  + finance_income (资金流水)
  + sync_state (同步状态)
"""

import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"


@contextmanager
def get_connection():
    """数据库连接上下文管理器"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # 暂时禁用外键约束，避免插入顺序问题
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """初始化表结构"""
    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. 仓位历史表 (PositionHistory)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS position_history (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                position_side TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                close_time INTEGER,
                entry_price REAL DEFAULT 0.0,
                exit_price REAL DEFAULT 0.0,
                max_qty REAL DEFAULT 0.0,
                total_realized_pnl REAL DEFAULT 0.0,
                total_commission REAL DEFAULT 0.0,
                total_funding_fee REAL DEFAULT 0.0,
                is_liquidation INTEGER DEFAULT 0,
                status TEXT DEFAULT 'OPEN'
            );
        """)

        # 2. 逻辑订单表 (Order)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `order` (
                order_id INTEGER PRIMARY KEY,
                position_id TEXT,
                symbol TEXT NOT NULL,
                type TEXT NOT NULL,
                side TEXT NOT NULL,
                position_side TEXT NOT NULL,
                status TEXT NOT NULL,
                price REAL DEFAULT 0.0,
                avg_price REAL DEFAULT 0.0,
                qty REAL DEFAULT 0.0,
                reduce_only INTEGER DEFAULT 0,
                working_type TEXT,
                created_at INTEGER,
                updated_at INTEGER
            );
        """)

        # 3. 撮合流水表 (Trade)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                position_side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                quote_qty REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                commission REAL NOT NULL,
                commission_asset TEXT NOT NULL,
                commission_usdt REAL DEFAULT 0.0,
                maker INTEGER NOT NULL,
                time INTEGER NOT NULL,
                is_virtual INTEGER DEFAULT 0,
                is_liquidation INTEGER DEFAULT 0
            );
        """)

        # 4. 资金流水表 (Finance Income)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS finance_income (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                income_type TEXT NOT NULL,
                income REAL NOT NULL,
                asset TEXT NOT NULL,
                time INTEGER NOT NULL,
                tran_id INTEGER UNIQUE,
                trade_id TEXT,
                UNIQUE(tran_id)
            );
        """)

        # 5. 同步状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                symbol TEXT PRIMARY KEY,
                last_trade_id INTEGER,
                last_sync_time INTEGER,
                virtual_entry_price REAL,
                virtual_qty REAL
            );
        """)

        # 性能索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_position_symbol ON position_history(symbol);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_position_open_time ON position_history(open_time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_position_status ON position_history(status);")
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_position_id ON `order`(position_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_symbol_side ON `order`(symbol, position_side);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_status ON `order`(status);")
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_order_id ON trade(order_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_time ON trade(time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_symbol ON trade(symbol, time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_liquidation ON trade(is_liquidation);")
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_income_type ON finance_income(income_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_income_time ON finance_income(time);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_income_symbol ON finance_income(symbol);")

    logger.info("Database initialized successfully")


def insert_trades(trades: list) -> int:
    """批量插入 Trade 记录"""
    saved = 0
    
    with get_connection() as conn:
        for t in trades:
            try:
                # 计算 USDT 手续费
                commission_usdt = t.get('commission_usdt', 0)
                if not commission_usdt:
                    commission_asset = t.get('commissionAsset', 'USDT')
                    if commission_asset == 'USDT':
                        commission_usdt = float(t['commission'])
                    else:
                        # BNB 或其他币种，需要外部转换
                        commission_usdt = float(t['commission'])
                
                conn.execute("""
                    INSERT OR IGNORE INTO trade 
                    (id, order_id, symbol, side, position_side, price, qty, 
                     quote_qty, realized_pnl, commission, commission_asset, 
                     commission_usdt, maker, time, is_liquidation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    t['id'], t['orderId'], t['symbol'], t['side'], t['positionSide'],
                    float(t['price']), float(t['qty']), float(t['quoteQty']),
                    float(t['realizedPnl']), float(t['commission']), 
                    t['commissionAsset'], commission_usdt,
                    1 if t['maker'] else 0, t['time'],
                    1 if t.get('is_liquidation', False) else 0
                ))
                saved += 1
            except sqlite3.IntegrityError:
                pass
            except Exception as e:
                logger.warning(f"Insert trade {t.get('id')} failed: {e}")
    
    return saved


def insert_orders(orders: list) -> int:
    """批量插入 Order 记录"""
    saved = 0
    
    with get_connection() as conn:
        for o in orders:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO `order`
                    (order_id, symbol, type, side, position_side, status, 
                     price, qty, reduce_only, working_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    o['orderId'], o['symbol'], o['type'], o['side'],
                    o.get('positionSide', 'BOTH'), o['status'],
                    float(o.get('price', 0)), float(o.get('origQty', 0)),
                    1 if o.get('reduceOnly', False) else 0,
                    o.get('workingType', ''),
                    o.get('time', 0), o.get('updateTime', 0)
                ))
                saved += 1
            except Exception as e:
                logger.warning(f"Insert order {o.get('orderId')} failed: {e}")
    
    return saved


def insert_income(incomes: list) -> int:
    """批量插入资金流水"""
    saved = 0
    
    with get_connection() as conn:
        for inc in incomes:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO finance_income
                    (symbol, income_type, income, asset, time, tran_id, trade_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    inc.get('symbol', ''),
                    inc['incomeType'],
                    float(inc['income']),
                    inc['asset'],
                    inc['time'],
                    inc.get('tranId', 0),
                    inc.get('tradeId', '')
                ))
                saved += 1
            except sqlite3.IntegrityError:
                pass
    
    return saved


def insert_virtual_trade(symbol: str, order_id: int, position_side: str,
                         price: float, qty: float, time_ms: int):
    """插入虚拟 Trade（用于远古仓位对齐）"""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO trade 
            (id, order_id, symbol, side, position_side, price, qty, 
             quote_qty, realized_pnl, commission, commission_asset, 
             commission_usdt, maker, time, is_virtual)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            -order_id, order_id, symbol,
            'BUY' if position_side == 'LONG' else 'SELL',
            position_side,
            price, qty, price * qty,
            0, 0, 'USDT', 0, 0, time_ms
        ))


def get_trades(symbol: str = None, start_time: int = None, end_time: int = None,
               is_liquidation: int = None) -> list:
    """查询 Trade"""
    with get_connection() as conn:
        query = "SELECT * FROM trade WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_time:
            query += " AND time >= ?"
            params.append(start_time)
        if end_time:
            query += " AND time <= ?"
            params.append(end_time)
        if is_liquidation is not None:
            query += " AND is_liquidation = ?"
            params.append(is_liquidation)
        
        query += " ORDER BY time ASC"
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_orders(symbol: str = None, status: str = None) -> list:
    """查询 Order"""
    with get_connection() as conn:
        query = "SELECT * FROM `order` WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if status:
            query += " AND status = ?"
            params.append(status)
        
        query += " ORDER BY created_at DESC"
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_positions(symbol: str = None, status: str = None) -> list:
    """查询 PositionHistory"""
    with get_connection() as conn:
        query = "SELECT * FROM position_history WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if status:
            query += " AND status = ?"
            params.append(status)
        
        query += " ORDER BY open_time DESC"
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_income(symbol: str = None, income_type: str = None,
               start_time: int = None, end_time: int = None) -> list:
    """查询资金流水"""
    with get_connection() as conn:
        query = "SELECT * FROM finance_income WHERE 1=1"
        params = []
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if income_type:
            query += " AND income_type = ?"
            params.append(income_type)
        if start_time:
            query += " AND time >= ?"
            params.append(start_time)
        if end_time:
            query += " AND time <= ?"
            params.append(end_time)
        
        query += " ORDER BY time ASC"
        
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def insert_position(position: dict):
    """插入 PositionHistory"""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO position_history
            (id, symbol, position_side, open_time, close_time, 
             entry_price, exit_price, max_qty,
             total_realized_pnl, total_commission, total_funding_fee,
             is_liquidation, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position['id'], position['symbol'], position['position_side'],
            position['open_time'], position.get('close_time'),
            position.get('entry_price', 0.0), position.get('exit_price', 0.0),
            position.get('max_qty', 0.0),
            position.get('total_realized_pnl', 0.0),
            position.get('total_commission', 0.0),
            position.get('total_funding_fee', 0.0),
            position.get('is_liquidation', 0),
            position.get('status', 'OPEN')
        ))


def update_order_position(order_id: int, position_id: str):
    """更新 Order 的 position_id"""
    with get_connection() as conn:
        conn.execute(
            "UPDATE `order` SET position_id = ? WHERE order_id = ?",
            (position_id, order_id)
        )


def get_latest_trade_id(symbol: str) -> int:
    """获取最新 trade_id"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(id) as max_id FROM trade WHERE symbol = ? AND is_virtual = 0", 
            (symbol,)
        ).fetchone()
        return row['max_id'] if row and row['max_id'] else None


def get_trade_stats(symbol: str, start_time: int, end_time: int) -> dict:
    """获取统计摘要"""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT 
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(realized_pnl) as total_pnl,
                SUM(commission_usdt) as total_commission_usdt,
                SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) as total_profit,
                SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END) as total_loss,
                SUM(commission) as total_commission,
                MAX(realized_pnl) as max_win,
                MIN(realized_pnl) as max_loss,
                SUM(CASE WHEN is_liquidation = 1 THEN 1 ELSE 0 END) as liquidation_count,
                SUM(CASE WHEN is_liquidation = 1 THEN realized_pnl ELSE 0 END) as liquidation_pnl
            FROM trade
            WHERE symbol = ? AND time BETWEEN ? AND ?
        """, (symbol, start_time, end_time)).fetchone()
        
        return dict(row) if row else {}


if __name__ == "__main__":
    init_db()
    print(f"Database created at: {DB_PATH}")
