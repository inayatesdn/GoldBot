import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
        
    for symbol in ["EURUSD", "GBPUSD"]:
        mt5.symbol_select(symbol, True)
        si = mt5.symbol_info(symbol)
        if not si:
            print(f"Skipping {symbol}, not found")
            continue
            
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"Skipping {symbol}, no tick data")
            continue
            
        print(f"\n--- Testing BUY on {symbol} (Ask: {tick.ask}) ---")
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": 0.01,
            "type": mt5.ORDER_TYPE_BUY,
            "price": tick.ask,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 20,
            "magic": 2026888,
            "comment": f"Test {symbol}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        res = mt5.order_send(request)
        if res:
            print(f"  Retcode: {res.retcode}")
            print(f"  Comment: {res.comment}")
            print(f"  Ticket: {res.order}")
            
    mt5.shutdown()

if __name__ == "__main__":
    main()
