#!/usr/bin/env python3
"""
交易质量报告生成器

组装数据 + 提示词，供 AI 生成报告

用法:
  python3 report_generator.py --symbol BTCUSDT --period week
  python3 report_generator.py --period month
"""

import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from db import get_trades, get_positions, get_income, get_trade_stats

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
    else:
        raise ValueError(f"Unknown period: {period}")


def collect_data(symbol: str = None, start_date: str = None, end_date: str = None) -> dict:
    """收集报告所需数据"""
    
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000) + 86400000
    
    # 获取交易数据
    trades = get_trades(symbol=symbol, start_time=start_ts, end_time=end_ts)
    
    # 获取仓位数据
    positions = get_positions(symbol=symbol, status='CLOSED')
    positions = [p for p in positions if p['close_time'] and start_ts <= p['close_time'] <= end_ts]
    
    # 获取资金流水
    income = get_income(symbol=symbol, start_time=start_ts, end_time=end_ts)
    funding_fees = [i for i in income if i['income_type'] == 'FUNDING_FEE']
    
    # 计算统计数据
    total_trades = len(trades)
    wins = len([t for t in trades if t['realized_pnl'] > 0])
    losses = len([t for t in trades if t['realized_pnl'] < 0])
    
    total_pnl = sum(t['realized_pnl'] for t in trades)
    total_profit = sum(t['realized_pnl'] for t in trades if t['realized_pnl'] > 0)
    total_loss = abs(sum(t['realized_pnl'] for t in trades if t['realized_pnl'] < 0))
    total_commission = sum(t['commission_usdt'] for t in trades)
    
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    profit_factor = (total_profit / total_loss) if total_loss > 0 else float('inf')
    
    max_win = max([t['realized_pnl'] for t in trades]) if trades else 0
    max_loss = min([t['realized_pnl'] for t in trades]) if trades else 0
    
    # 强平统计
    liquidations = [t for t in trades if t.get('is_liquidation') == 1]
    
    # 资金费用
    total_funding = sum(float(i['income']) for i in funding_fees)
    
    # 按币种统计
    symbols = list(set(t['symbol'] for t in trades))
    by_symbol = {}
    for sym in symbols:
        sym_trades = [t for t in trades if t['symbol'] == sym]
        sym_pnl = sum(t['realized_pnl'] for t in sym_trades)
        sym_wins = len([t for t in sym_trades if t['realized_pnl'] > 0])
        sym_total = len(sym_trades)
        by_symbol[sym] = {
            'trades': sym_total,
            'wins': sym_wins,
            'pnl': sym_pnl,
            'win_rate': (sym_wins / sym_total * 100) if sym_total > 0 else 0
        }
    
    # 仓位分析
    position_analysis = []
    for p in positions:
        position_analysis.append({
            'symbol': p['symbol'],
            'direction': p['position_side'],
            'entry_price': p['entry_price'],
            'exit_price': p['exit_price'],
            'pnl': p['total_realized_pnl'],
            'commission': p['total_commission'],
            'funding': p['total_funding_fee'],
            'is_liquidation': p.get('is_liquidation', 0),
            'hold_time_hours': ((p['close_time'] - p['open_time']) / 3600000) if p['close_time'] else 0
        })
    
    # 高风险行为检测
    high_risk = []
    
    # 检测逆势加仓
    for sym in symbols:
        sym_trades = sorted([t for t in trades if t['symbol'] == sym], key=lambda x: x['time'])
        consecutive_losses = 0
        for t in sym_trades:
            if t['realized_pnl'] < 0:
                consecutive_losses += 1
                if consecutive_losses >= 3:
                    high_risk.append({
                        'type': 'consecutive_losses',
                        'symbol': sym,
                        'count': consecutive_losses,
                        'description': f'{sym} 连续亏损 {consecutive_losses} 笔'
                    })
            else:
                consecutive_losses = 0
    
    # 检测大额亏损
    for t in trades:
        if t['realized_pnl'] < -10:  # 亏损超过 10 USDT
            high_risk.append({
                'type': 'large_loss',
                'symbol': t['symbol'],
                'pnl': t['realized_pnl'],
                'description': f'{t["symbol"]} 单笔亏损 {t["realized_pnl"]:.2f} USDT'
            })
    
    return {
        'period': {'start': start_date, 'end': end_date},
        'summary': {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'total_pnl': total_pnl,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'total_commission': total_commission,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'max_win': max_win,
            'max_loss': max_loss,
            'liquidation_count': len(liquidations),
            'total_funding': total_funding
        },
        'by_symbol': by_symbol,
        'positions': position_analysis,
        'high_risk': high_risk,
        'funding_fees': [{'symbol': i['symbol'], 'amount': float(i['income']), 
                         'time': datetime.fromtimestamp(i['time']/1000).strftime('%m-%d %H:%M')} 
                        for i in funding_fees[:20]]  # 最近 20 条
    }


def generate_prompt(data: dict) -> str:
    """生成 AI 报告提示词"""
    
    prompt = f"""你是一个专业的合约交易分析师。请根据以下交易数据，生成一份严格的交易质量报告。

【输出要求】
- 强制格式：你的回复第一行必须、且只能是 "**综合评级: X**" (X只能是A+, A, A-, B+, B, B-, C+, C, C-, D, F其中之一)，不要有任何前置问候语！
- 拒绝预测：只分析历史数据，绝不预测未来行情或分析宏观新闻。
- 数据支撑：所有的批评或表扬，必须引用具体的交易数据作为证据，严禁空洞说教。

【报告周期】
{data['period']['start']} 至 {data['period']['end']}

【核心数据】
- 总交易笔数: {data['summary']['total_trades']}
- 盈利笔数: {data['summary']['wins']}
- 亏损笔数: {data['summary']['losses']}
- 胜率: {data['summary']['win_rate']:.1f}%
- 总盈亏: {data['summary']['total_pnl']:.2f} USDT
- 总盈利: {data['summary']['total_profit']:.2f} USDT
- 总亏损: {data['summary']['total_loss']:.2f} USDT
- 利润因子: {data['summary']['profit_factor']:.2f}
- 最大单笔盈利: {data['summary']['max_win']:.2f} USDT
- 最大单笔亏损: {data['summary']['max_loss']:.2f} USDT
- 总手续费: {data['summary']['total_commission']:.2f} USDT
- 资金费用: {data['summary']['total_funding']:.4f} USDT
- 强平次数: {data['summary']['liquidation_count']}

【币种分布】
"""
    
    for sym, stats in data['by_symbol'].items():
        prompt += f"- {sym}: {stats['trades']}笔, 胜率{stats['win_rate']:.1f}%, 盈亏{stats['pnl']:.2f}USDT\n"
    
    prompt += "\n【已平仓仓位】\n"
    for pos in data['positions']:
        liquidation_mark = " ⚠️强平" if pos['is_liquidation'] else ""
        prompt += f"- {pos['symbol']} {pos['direction']}: 入场{pos['entry_price']:.4f} → 出场{pos['exit_price']:.4f}, 盈亏{pos['pnl']:.2f}USDT, 持仓{pos['hold_time_hours']:.1f}h{liquidation_mark}\n"
    
    if data['high_risk']:
        prompt += "\n【高风险行为检测】\n"
        for risk in data['high_risk']:
            prompt += f"- {risk['description']}\n"
    
    if data['funding_fees']:
        prompt += "\n【资金费用明细（最近）】\n"
        for fee in data['funding_fees']:
            prompt += f"- {fee['time']} {fee['symbol']}: {fee['amount']:.4f} USDT\n"
    
    prompt += """
【请严格按照以下 Markdown 结构输出】

**综合评级: X**

## 📊 一、 核心数据概览
（简述本周整体盈亏、胜率表现，以及整体回撤控制情况。引用具体的净利润和胜率数据。）

## ⚖️ 二、 交易质量优缺（包含仓位管理与手法解析）
（结合交易数据进行深度剖析）
- **优秀案例**：（指出表现极好的分批操作或精准择时，如顺势加仓、漂亮的分批止盈。如果没有，请明确写明"本周暂无表现突出的优秀案例"。）
- **高风险行为**：（严厉指出存在多次的逆势加仓、低质量或高风险的危险行为，并列举具体标的。）

## 💡 三、 潜在隐患与优化空间
（针对上述暴露的弱点，给出 2-3 条可落地的操作纪律或策略优化建议）
- **[核心隐患或建议的短语]**：详细说明...
- **[核心隐患或建议的短语]**：详细说明...
- **[核心隐患或建议的短语]**：详细说明...
"""
    
    return prompt


def main():
    parser = argparse.ArgumentParser(description='生成交易质量报告提示词')
    parser.add_argument('--symbol', '-s', help='交易对')
    parser.add_argument('--period', '-p', 
                       choices=['yesterday', 'week', 'month', 'last_week', 'last_month'],
                       default='week', help='报告周期')
    parser.add_argument('--start', help='开始日期')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--output', '-o', help='输出文件')
    
    args = parser.parse_args()
    
    # 确定日期范围
    if args.start and args.end:
        start_date, end_date = args.start, args.end
    else:
        start_date, end_date = get_date_range(args.period)
    
    # 收集数据
    data = collect_data(args.symbol, start_date, end_date)
    
    # 生成提示词
    prompt = generate_prompt(data)
    
    # 输出
    if args.output:
        with open(args.output, 'w') as f:
            f.write(prompt)
        print(f"Prompt saved to: {args.output}", file=sys.stderr)
    else:
        print(prompt)


if __name__ == '__main__':
    main()
