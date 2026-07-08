import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        return
    print("Listing constants in mt5 module with 'filling' or 'FILLING':")
    for name in dir(mt5):
        if "filling" in name.lower() or "fok" in name.lower() or "ioc" in name.lower():
            print(f"  {name}: {getattr(mt5, name)}")
            
    sym = "XAUUSD"
    mt5.symbol_select(sym, True)
    si = mt5.symbol_info(sym)
    if si:
        print(f"Symbol {sym} filling_mode bitmask:", si.filling_mode)
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
