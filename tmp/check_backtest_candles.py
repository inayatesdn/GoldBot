import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone

def main():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    utc_now = datetime.now(timezone.utc)
    start_dt = utc_now - timedelta(days=7)
    
    print(f"Requesting data for {symbol} from {start_dt} to {utc_now}...")
    
    r1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start_dt, utc_now)
    r3 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M3, start_dt - timedelta(hours=10), utc_now)
    r5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_dt - timedelta(hours=20), utc_now)
    
    print("M1 Candles retrieved:", len(r1) if r1 is not None else "None")
    print("M3 Candles retrieved:", len(r3) if r3 is not None else "None")
    print("M5 Candles retrieved:", len(r5) if r5 is not None else "None")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
