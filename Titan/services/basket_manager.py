import time
import logging
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, get_settings
from Titan.core.state import state
from Titan.core.logger import risk_logger
from Titan.services.execution_engine import ExecutionEngine
from Titan.execution.mt5_client import MT5Client

class BasketManager:
    def __init__(self, execution_engine: ExecutionEngine, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = risk_logger
        self.execution_engine = execution_engine

    def monitor_and_manage_basket(self) -> Dict[str, Any]:
        """Calculates basket stats and checks stop-loss / profit targets to execute collective exits."""
        if not state.mt5_connected:
            return {"status": "OFFLINE", "count": 0, "pnl": 0.0}

        settings = get_settings()
        
        # Retrieve active positions
        positions = MT5Client.get_open_positions()
        
        total_pnl = 0.0
        total_volume = 0.0
        oldest_time = time.time()
        active_tickets = []
        
        for pos in positions:
            total_pnl += pos["profit"]
            total_volume += pos["volume"]
            active_tickets.append(pos["ticket"])
            if pos["time"] < oldest_time:
                oldest_time = pos["time"]

        # Calculate basket profit/stop limits based on settings overrides or defaults
        balance = state.balance if state.balance > 0 else 10000.0
        
        # Basket Profit Targets ($ or % of equity)
        profit_target_money = float(settings.get("basket_profit_target_money", 100.0)) # Default $100 profit
        profit_target_pct = float(settings.get("basket_profit_target_pct", 0.0))       # Default 0% (disabled)
        if profit_target_pct > 0.0:
            profit_target_limit = balance * (profit_target_pct / 100.0)
        else:
            profit_target_limit = profit_target_money

        # Basket Stop Loss ($ or % of equity)
        stop_loss_money = float(settings.get("basket_stop_loss_money", 300.0))        # Default $300 loss
        stop_loss_pct = float(settings.get("basket_stop_loss_pct", 0.0))              # Default 0% (disabled)
        if stop_loss_pct > 0.0:
            stop_loss_limit = balance * (stop_loss_pct / 100.0)
        else:
            stop_loss_limit = stop_loss_money
            
        # Time caps
        max_hold_minutes = float(settings.get("max_hold_minutes", 60.0))
        basket_duration_minutes = (time.time() - oldest_time) / 60.0 if positions else 0.0

        # Maximum exposure guidelines
        max_exposure_lots = float(settings.get("max_exposure_lots", 5.0)) # Max 5 lots active

        # Update telemetry variables in global state thread-safely
        state.lock.acquire()
        state.open_positions = positions
        state.open_trades_count = len(positions)
        state.today_open_pnl = total_pnl
        state.basket_profit = total_pnl
        state.lock.release()

        # Check triggers
        # 1. Profit Target hit
        if len(positions) > 0 and total_pnl >= profit_target_limit:
            self.logger.warning(
                f"[Basket Manager] Take Profit target reached! "
                f"Floating Profit: ${total_pnl:.2f} >= Target limit: ${profit_target_limit:.2f}. Liquidating basket..."
            )
            self.execution_engine.close_all_positions()
            return {"status": "PROFIT_TARGET_HIT", "count": 0, "pnl": 0.0}

        # 2. Stop Loss hit
        if len(positions) > 0 and total_pnl <= -stop_loss_limit:
            self.logger.warning(
                f"[Basket Manager] Cumulative Stop Loss hit! "
                f"Floating Drawdown: ${total_pnl:.2f} <= Stop limit: -${stop_loss_limit:.2f}. Liquidating basket..."
            )
            self.execution_engine.close_all_positions()
            return {"status": "STOP_LOSS_HIT", "count": 0, "pnl": 0.0}

        # 3. Time Cap expired
        if len(positions) > 0 and basket_duration_minutes > max_hold_minutes:
            self.logger.warning(
                f"[Basket Manager] Basket duration time cap reached! "
                f"Age: {basket_duration_minutes:.1f}m > Limit: {max_hold_minutes}m. Liquidating basket..."
            )
            self.execution_engine.close_all_positions()
            return {"status": "TIME_CAP_EXPIRED", "count": 0, "pnl": 0.0}

        # 4. Max exposure breached (issue warnings to avoid further trades)
        if total_volume >= max_exposure_lots:
            self.logger.warning(
                f"[Basket Exposure Alert] Combined volume {total_volume:.2f} lots >= Limit {max_exposure_lots:.2f} lots. Blocking new entries."
            )

        return {
            "status": "RUNNING",
            "count": len(positions),
            "pnl": total_pnl,
            "exposure": total_volume,
            "duration_mins": basket_duration_minutes
        }
