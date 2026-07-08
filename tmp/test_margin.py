import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return
        
    print("MT5 version:", mt5.version())
    acct = mt5.account_info()
    if acct:
        print("Free Margin:", acct.margin_free)
        print("Balance:", acct.balance)
        print("Leverage:", acct.leverage)
    
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info:
        print("Symbol:", symbol_info.name)
        print("Bid:", mt5.symbol_info_tick(symbol).bid)
        print("Ask:", mt5.symbol_info_tick(symbol).ask)
        print("Margin mode:", symbol_info.trade_calc_mode)
    
    lot_size = 1.0
    price = mt5.symbol_info_tick(symbol).ask
    print(f"Calculating margin for {lot_size} lots at {price}...")
    
    margin_buy = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, lot_size, price)
    print("Margin BUY:", margin_buy)
    
    margin_sell = mt5.order_calc_margin(mt5.ORDER_TYPE_SELL, symbol, lot_size, price)
    print("Margin SELL:", margin_sell)
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
