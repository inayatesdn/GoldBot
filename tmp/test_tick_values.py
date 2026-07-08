import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
    sym = "XAUUSD"
    mt5.symbol_select(sym, True)
    si = mt5.symbol_info(sym)
    if si:
        for prop in dir(si):
            if "tick" in prop or "value" in prop or "contract" in prop:
                try:
                    print(f"{prop}: {getattr(si, prop)}")
                except:
                    pass
    mt5.shutdown()

if __name__ == "__main__":
    main()
