import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    si = mt5.symbol_info(symbol)
    if si:
        print("Symbol:", si.name)
        print("Trade Mode (0=Disabled, 1=LongOnly, 2=ShortOnly, 3=CloseOnly, 4=Full):", si.trade_mode)
        print("Execution Mode (0=Request, 1=Instant, 2=Market, 3=Exchange):", si.trade_execution)
        print("Is trade allowed:", si.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL)
        print("Expiration Mode flags:", si.expiration_mode)
        print("Volume Min:", si.volume_min)
        print("Volume Max:", si.volume_max)
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
