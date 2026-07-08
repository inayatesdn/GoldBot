import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
    for sym in ["XAUUSD", "EURUSD", "GBPUSD"]:
        mt5.symbol_select(sym, True)
        si = mt5.symbol_info(sym)
        if si:
            print(f"[{sym}] contract_size={si.trade_contract_size}, tick_size={si.trade_tick_size}, tick_value={si.trade_tick_value}, point={si.point}")
    mt5.shutdown()

if __name__ == "__main__":
    main()
