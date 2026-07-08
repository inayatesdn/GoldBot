import sqlite3
import json
import os
import sys

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage', 'titan.db')

def generate_v9_audit():
    print("="*60)
    print("       TITAN V9 - HISTORICAL AUDIT & STRATEGY REPORT       ")
    print("="*60)
    
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # STEP 4: Strategy Performance by Direction
    print("\n--- [STEP 4] Strategy Performance by Direction ---")
    cursor.execute("SELECT direction, pnl FROM trades WHERE status='CLOSED'")
    trades = cursor.fetchall()
    
    buy_trades = [t for t in trades if t['direction'] == 'BUY']
    sell_trades = [t for t in trades if t['direction'] == 'SELL']
    
    def analyze_dir(trades_list):
        total = len(trades_list)
        if total == 0: return {"win_rate": 0, "profit_factor": 0, "avg_profit": 0, "avg_loss": 0}
        
        wins = [t['pnl'] for t in trades_list if t['pnl'] > 0]
        losses = [abs(t['pnl']) for t in trades_list if t['pnl'] <= 0]
        
        win_rate = (len(wins) / total) * 100
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        avg_profit = gross_profit / len(wins) if wins else 0
        avg_loss = gross_loss / len(losses) if losses else 0
        
        return {"total": total, "win_rate": win_rate, "profit_factor": profit_factor, "avg_profit": avg_profit, "avg_loss": avg_loss}
        
    buy_stats = analyze_dir(buy_trades)
    sell_stats = analyze_dir(sell_trades)
    
    print("BUY TRADES:")
    print(f"  Count        : {buy_stats['total']}")
    print(f"  Win Rate     : {buy_stats['win_rate']:.1f}%")
    print(f"  Profit Factor: {buy_stats['profit_factor']:.2f}")
    print(f"  Avg Profit   : ${buy_stats['avg_profit']:.2f}")
    print(f"  Avg Loss     : ${buy_stats['avg_loss']:.2f}")
    
    print("\nSELL TRADES:")
    print(f"  Count        : {sell_stats['total']}")
    print(f"  Win Rate     : {sell_stats['win_rate']:.1f}%")
    print(f"  Profit Factor: {sell_stats['profit_factor']:.2f}")
    print(f"  Avg Profit   : ${sell_stats['avg_profit']:.2f}")
    print(f"  Avg Loss     : ${sell_stats['avg_loss']:.2f}")
    
    diff = abs(buy_stats["win_rate"] - sell_stats["win_rate"])
    if buy_stats["total"] > 5 and sell_stats["total"] > 5 and diff > 15:
        print(f"\n[!] WARNING: Significant directional mismatch detected ({diff:.1f}% win rate delta). Need directional bias constraint review.")

    # STEP 3 & 6: Historical Audit on 100 Losing Trades
    print("\n--- [STEP 3 & 6] Historical Audit (Last 100 Losses) ---")
    
    cursor.execute("""
        SELECT t.ticket, t.direction, t.entry_price, t.sl, t.tp, t.pnl, l.root_cause_json, l.mfe, l.mae, l.sl_hit
        FROM trades t
        LEFT JOIN learning_outcomes l ON t.ticket = l.ticket
        WHERE t.status='CLOSED' AND t.pnl <= 0
        ORDER BY t.close_time DESC
        LIMIT 100
    """)
    loss_trades = cursor.fetchall()
    
    if len(loss_trades) == 0:
        print("No losing trades found in DB.")
        conn.close()
        return

    causes = {
        "Direction Wrong": 0,
        "Too Early (MFE > 0 but reversed)": 0,
        "Too Late (Lagging Entry)": 0,
        "Stop Loss Too Tight (Inside ATR/Liquidity Sweep)": 0,
        "Missing Confirmations": 0
    }
    
    stop_hit_count = 0
    price_continued_prob_count = 0 
    
    for row in loss_trades:
        rc_json_str = row['root_cause_json']
        mfe = row['mfe'] or 0
        mae = row['mae'] or 0
        sl = row['sl'] or 0
        entry = row['entry_price'] or 0
        is_sl_hit = row['sl_hit']
        
        if is_sl_hit: stop_hit_count += 1
            
        rc = {}
        if rc_json_str:
            try:
                rc = json.loads(rc_json_str)
            except:
                pass
                
        lbl = rc.get("primary_cause", "")
        if "Wrong Trend" in lbl or "Counter Trend" in lbl:
            causes["Direction Wrong"] += 1
        elif "Too Early" in lbl:
            causes["Too Early (MFE > 0 but reversed)"] += 1
        elif "Volatility" in lbl or "Tight" in lbl:
            causes["Stop Loss Too Tight (Inside ATR/Liquidity Sweep)"] += 1
        elif "Spread" in lbl:
             # Just map to missing confirmations for now if unspecified
             pass
        else:
            if mfe > 3.0:
                causes["Too Early (MFE > 0 but reversed)"] += 1
            elif mae > 10.0 and abs(entry - sl) < 2.0:
                causes["Stop Loss Too Tight (Inside ATR/Liquidity Sweep)"] += 1
                price_continued_prob_count += 1
            else:
                causes["Missing Confirmations"] += 1

    print(f"Total Losses Assessed: {len(loss_trades)}")
    print("\nMost Common Failure Reasons:")
    for k, v in causes.items():
        if v > 0:
            print(f"  - {k}: {v} trades ({(v/len(loss_trades))*100:.1f}%)")
            
    print("\nStop-Loss Analysis (Step 6):")
    print(f"  1. How many hit strict SL? {stop_hit_count} out of {len(loss_trades)} recorded.")
    print(f"  2. How many were too tight within normal ATR/Sweep? {causes['Stop Loss Too Tight (Inside ATR/Liquidity Sweep)']}")
    if price_continued_prob_count > 0:
        print(f"  3. Price continued in original direction after stopping out: Estimated {price_continued_prob_count} trades (Swept).")
    print("  4. Intelligent exit recommendation: Utilize trailing stops / dynamic BE faster upon breaking MFE > 3 ATR limits.")

    conn.close()

if __name__ == "__main__":
    generate_v9_audit()
