import sys
import json
sys.path.insert(0, r"C:\Users\hp\Desktop\goldtradingbot")

from Titan.research.backtester import Backtester
from Titan.execution.mt5_client import MT5Client

def main():
    print("Initializing MT5 to fetch historical data for backtesting...")
    if not MT5Client.initialize():
        print("Failed to initialize MT5")
        return
        
    print("\n--- Running Simulation Replay on XAUUSD (Last Week) ---")
    results = Backtester.run_historical_backtest("XAUUSD", "last_week")
    
    if "error" in results:
        print("Error during simulation:", results["error"])
        MT5Client.shutdown()
        return
        
    print("\n==========================================")
    print("        TITAN SIMULATION REPLAY RESULTS    ")
    print("==========================================")
    print(f"Final Balance:    ${results.get('final_balance', 0.0):,.2f}")
    print(f"Total Trades:     {results.get('total_trades', 0)}")
    print(f"Win Rate:         {results.get('win_rate', 0.0):.2f}%")
    print(f"Profit Factor:    {results.get('profit_factor', 0.0):.2f}")
    print(f"Max Drawdown:     {results.get('max_drawdown', 0.0):.2f}%")
    print(f"Sharpe Ratio:     {results.get('sharpe', 0.0):.2f}")
    print(f"Expectancy:       ${results.get('expectancy', 0.0):.2f}")
    print(f"Avg Hold Minutes: {results.get('avg_hold_mins', 0.0):.2f}")
    print("==========================================")
    
    MT5Client.shutdown()

if __name__ == "__main__":
    main()
