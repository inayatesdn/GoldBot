import MetaTrader5 as mt5

def main():
    if not mt5.initialize(path="C:\\Program Files\\MetaTrader 5\\terminal64.exe"):
        print("initialize() failed")
        return
    
    symbols = mt5.symbols_get()
    print("Total symbols:", len(symbols) if symbols else 0)
    
    # Check for gold symbols
    gold_syms = []
    cross_syms = []
    common_names = ['XAU', 'GOLD', 'DXY', 'USDX', 'US10Y', 'BTC', 'SPX', 'US500', 'VIX', 'EURUSD', 'GBPUSD']
    
    if symbols:
        for s in symbols:
            name = s.name.upper()
            if any(cn in name for cn in common_names):
                gold_syms.append(s.name)
                
    print("Interesting symbols found:", len(gold_syms))
    print(gold_syms[:30])
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
