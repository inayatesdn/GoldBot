import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info:
        print("Contract size:", symbol_info.trade_contract_size)
        print("Tick size:", symbol_info.trade_tick_size)
        print("Tick value:", symbol_info.trade_tick_value)
        print("Point:", symbol_info.point)
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
