import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    si = mt5.symbol_info(symbol)
    if si:
        print("Symbol:", si.name)
        # Execution modes:
        # SYMBOL_TRADE_EXECUTION_REQUEST = 0
        # SYMBOL_TRADE_EXECUTION_INSTANT = 1
        # SYMBOL_TRADE_EXECUTION_MARKET = 2
        # SYMBOL_TRADE_EXECUTION_EXCHANGE = 3
        print("Trade Execution Mode (trade_exemode):", si.trade_exemode)
        
        # Test BUY with price=0.0 (Market Execution standard)
        print("\n--- Testing Market Order with Price = 0.0 ---")
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": 0.01,
            "type": mt5.ORDER_TYPE_BUY,
            "price": 0.0,  # Ignored for Market Execution
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 20,
            "magic": 2026888,
            "comment": "Test Market Buy Price 0",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
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
    main()
