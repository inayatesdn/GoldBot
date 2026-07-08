import time
import logging
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL
from Titan.core.state import state
from Titan.core.logger import execution_logger

class ExecutionEngine:
    def __init__(self, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = execution_logger
        self.magic_number = 2026888
        
        # Transient failure retry list
        self.transient_retcodes = [
            10004, # TRADE_RETCODE_REQUOTE
            10012, # TRADE_RETCODE_PRICE_OFF
            10015, # TRADE_RETCODE_INVALID_PRICE
            10016, # TRADE_RETCODE_PRICE_CHANGED
            10019, # TRADE_RETCODE_OFF_QUOTES
            10021, # TRADE_RETCODE_TIMEOUT
            10023  # TRADE_RETCODE_CONNECTION
        ]
        
    def execute_order(self, action: str, volume: float, sl: float = 0.0, tp: float = 0.0, comment: str = "", retries: int = 3) -> Dict[str, Any]:
        """Submits trade order to MT5 broker, retrying if transient issue detected."""
        if not state.mt5_connected:
            return {"success": False, "error": "MT5 terminal disconnected."}
            
        symbol_info = mt5.symbol_info(self.symbol)
        if symbol_info is None:
            return {"success": False, "error": f"Failed to acquire specifications for symbol {self.symbol}"}

        # Select filling mode
        filling_mode = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode & 1:
            filling_mode = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode & 2:
            filling_mode = mt5.ORDER_FILLING_IOC
        else:
            filling_mode = mt5.ORDER_FILLING_RETURN

        is_buy = action.upper() == "BUY"
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

        for attempt in range(retries):
            # Fetch latest tick on each attempt to avoid requotes
            tick = mt5.symbol_info_tick(self.symbol)
            if tick is None:
                self.logger.warning(f"[Attempt {attempt+1}/{retries}] Failed to fetch tick for pricing query.")
                time.sleep(0.1)
                continue
                
            price = tick.ask if is_buy else tick.bid
            
            # Recalculate SL/TP if they were passed in relative points (or adjust if absolute prices were provided)
            # Make sure we use absolute prices passed in
            target_sl = float(sl) if sl else 0.0
            target_tp = float(tp) if tp else 0.0

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": float(volume),
                "type": order_type,
                "price": float(price),
                "sl": target_sl,
                "tp": target_tp,
                "deviation": 20,
                "magic": self.magic_number,
                "comment": comment or f"Titan {action}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            self.logger.info(f"Submitting Trade [Attempt {attempt+1}/{retries}]: {action} {volume} {self.symbol} at {price:.3f} | SL={target_sl:.3f} TP={target_tp:.3f}")
            
            result = mt5.order_send(request)
            
            if result is None:
                self.logger.error(f"[Attempt {attempt+1}/{retries}] order_send returned None.")
                time.sleep(0.1)
                continue

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                self.logger.info(f"Trade successfully filled. Ticket: #{result.order} Price: {result.price}")
                return {
                    "success": True,
                    "ticket": result.order,
                    "price": result.price,
                    "volume": result.volume,
                    "comment": comment
                }
                
            if result.retcode in self.transient_retcodes:
                self.logger.warning(f"Transient error code {result.retcode} ({result.comment}) detected on attempt {attempt+1}. Retrying...")
                time.sleep(0.2)
                continue
            else:
                self.logger.error(f"Fatal trade failure execution code {result.retcode}: {result.comment}")
                return {"success": False, "error": f"Code {result.retcode}: {result.comment}"}

        return {"success": False, "error": "Maximum trade execution retries exceeded."}

    def close_position_ticket(self, ticket: int, volume: float = None, comment: str = "Titan Close") -> bool:
        """Closes an open position from ticket list."""
        if not state.mt5_connected:
            return False
            
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            self.logger.error(f"Failed to find position #{ticket} to close.")
            return False
            
        pos = positions[0]
        close_volume = volume if volume is not None else pos.volume
        
        symbol_info = mt5.symbol_info(pos.symbol)
        filling_mode = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode & 1:
            filling_mode = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode & 2:
            filling_mode = mt5.ORDER_FILLING_IOC
        else:
            filling_mode = mt5.ORDER_FILLING_RETURN

        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        order_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        
        for attempt in range(3):
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                time.sleep(0.1)
                continue
            price = tick.bid if is_buy else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": float(close_volume),
                "type": order_type,
                "position": int(ticket),
                "price": float(price),
                "deviation": 20,
                "magic": self.magic_number,
                "comment": comment,
                "type_filling": filling_mode,
            }
            
            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                self.logger.info(f"Position #{ticket} closed successfully.")
                return True
            time.sleep(0.2)
            
        return False

    def close_all_positions(self) -> bool:
        """Emergency Liquidate: command closure of ALL open positions."""
        self.logger.warning("Emergency Liquidation command entered! Closing all open positions...")
        if not state.mt5_connected:
            return False
            
        open_positions = mt5.positions_get()
        if not open_positions:
            self.logger.info("Liquidation: No open positions to close.")
            return True
            
        success = True
        for pos in open_positions:
            # Match only our symbol or all? Rule: close everything managed or overall
            if self.close_position_ticket(pos.ticket, comment="Liquidate All Command"):
                self.logger.info(f"Liquidated position #{pos.ticket}.")
            else:
                self.logger.error(f"Failed to liquidate position #{pos.ticket}.")
                success = False
        return success
