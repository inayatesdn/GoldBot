import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
    sym = "XAUUSD"
    mt5.symbol_select(sym, True)
    
    # Calc profit for BUY 1.0 lot from 4114.00 to 4115.00
    profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, 1.0, 4114.00, 4115.00)
    print("MT5 order_calc_profit for BUY 1.0 lot from 4114.00 to 4115.00 (difference $1.00):", profit)
    
    # Calc profit for SELL 1.0 lot from 4115.00 to 4114.00
    profit_sell = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, sym, 1.0, 4115.00, 4114.00)
    print("MT5 order_calc_profit for SELL 1.0 lot from 4115.00 to 4114.00 (difference $1.00):", profit_sell)
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
