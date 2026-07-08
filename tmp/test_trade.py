import MetaTrader5 as mt5

def test_trade():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        print("Failed to get symbol info")
        return
    print("filling_mode bitmask:", symbol_info.filling_mode)
    
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print("Failed to get tick")
        return
    print(f"Current tick Ask: {tick.ask}, Bid: {tick.bid}")
    
    filling_modes = [
        ("ORDER_FILLING_FOK", mt5.ORDER_FILLING_FOK),
        ("ORDER_FILLING_IOC", mt5.ORDER_FILLING_IOC),
        ("ORDER_FILLING_RETURN", mt5.ORDER_FILLING_RETURN)
    ]
    
    for name, mode in filling_modes:
        print(f"\n--- Testing with mode: {name} ({mode}) ---")
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
            "comment": f"Test {name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mode,
        }
        res = mt5.order_send(request)
        if res is None:
            print("  Result is None")
        else:
            print(f"  Retcode: {res.retcode}")
            print(f"  Comment: {res.comment}")
            print(f"  Order Ticket: {res.order}")
            
    mt5.shutdown()

if __name__ == "__main__":
    test_trade()
