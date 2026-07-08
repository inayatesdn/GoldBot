import MetaTrader5 as mt5
from datetime import datetime, timezone

def main():
    if not mt5.initialize():
        return
        
    now = datetime.now(timezone.utc)
    print("Current system time (UTC):", now)
    
    for symbol in ["EURUSD", "GBPUSD", "XAUUSD"]:
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            tick_time = datetime.fromtimestamp(tick.time, timezone.utc)
            time_diff = now - tick_time
            print(f"[{symbol}] Last Tick UTC: {tick_time} | Delay: {time_diff.total_seconds():.1f} seconds | Bid: {tick.bid} | Ask: {tick.ask}")
        else:
            print(f"[{symbol}] Failed to get tick information")
            
    mt5.shutdown()

if __name__ == "__main__":
    main()
