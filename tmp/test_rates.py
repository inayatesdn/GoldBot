import MetaTrader5 as mt5

if not mt5.initialize():
    print("MT5 Init Failed:", mt5.last_error())
    exit()

symbol = "XAUUSD"
selected = mt5.symbol_select(symbol, True)
print(f"Symbol select '{symbol}' status:", selected)

rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 150)
if rates is None:
    print("copy_rates_from_pos returned None. Last error:", mt5.last_error())
else:
    print("Successfully retrieved rates count:", len(rates))
    print("First candle details:", rates[0])

mt5.shutdown()
