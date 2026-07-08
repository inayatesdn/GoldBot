import logging
import time
import MetaTrader5 as mt5
from Titan.config.config import MT5_PATH, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

logger = logging.getLogger("Titan.MT5Client")

class MT5Client:
    _initialized = False

    @staticmethod
    def initialize():
        if MT5Client._initialized:
            return True
        
        logger.info("Initializing MetaTrader 5 Connection...")
        
        # Connect to MT5. If login details are configured, pass them, else let it connect to active terminal.
        if MT5_LOGIN and MT5_PASSWORD:
            login_num = int(MT5_LOGIN)
            success = mt5.initialize(
                path=MT5_PATH,
                login=login_num,
                password=MT5_PASSWORD,
                server=MT5_SERVER
            )
        else:
            success = mt5.initialize(path=MT5_PATH)
            
        if not success:
            err_code = mt5.last_error()
            logger.error(f"MT5 initial connection failed, error: {err_code}")
            MT5Client._initialized = False
            return False
            
        logger.info("MT5 interface initialized successfully.")
        terminal_info = mt5.terminal_info()
        if terminal_info:
            logger.info(f"Terminal connected: {terminal_info.company} on {MT5_SERVER}. Trade allowed: {terminal_info.trade_allowed}")
            
        MT5Client._initialized = True
        return True

    @staticmethod
    def shutdown():
        if MT5Client._initialized:
            mt5.shutdown()
            MT5Client._initialized = False
            logger.info("MT5 broker pipeline shutdown completed.")

    @staticmethod
    def check_connection():
        if not MT5Client._initialized:
            return MT5Client.initialize()
        terminal_info = mt5.terminal_info()
        if terminal_info is None or not terminal_info.connected:
            logger.warning("MT5 link lost. Attempting reconnection...")
            MT5Client._initialized = False
            return MT5Client.initialize()
        return True

    @staticmethod
    def get_account_info():
        if not MT5Client.check_connection():
            return None
        info = mt5.account_info()
        if info is None:
            return None
        return {
            "login": info.login,
            "name": info.name,
            "server": info.server,
            "currency": info.currency,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_free": info.margin_free,
            "margin_level": info.margin_level,
            "leverage": info.leverage,
            "profit": info.profit
        }

    @staticmethod
    def get_symbol_info(symbol):
        if not MT5Client.check_connection():
            return None
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if info is None:
            logger.error(f"Failed to find symbol {symbol} details.")
            return None
        return info

    @staticmethod
    def get_live_tick(symbol):
        if not MT5Client.check_connection():
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {
            "time": tick.time,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "spread": abs(tick.ask - tick.bid)
        }

    @staticmethod
    def get_open_positions(symbol=None):
        if not MT5Client.check_connection():
            return []
        
        if symbol:
            raw_positions = mt5.positions_get(symbol=symbol)
        else:
            raw_positions = mt5.positions_get()
            
        if raw_positions is None:
            return []
            
        positions = []
        for pos in raw_positions:
            positions.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                "price_open": pos.price_open,
                "price_current": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "swap": pos.swap,
                "commission": getattr(pos, "commission", 0.0),
                "magic": getattr(pos, "magic", 0),
                "comment": pos.comment,
                "time": pos.time
            })
        return positions

    @staticmethod
    def get_pending_orders(symbol=None):
        if not MT5Client.check_connection():
            return []
        
        if symbol:
            raw_orders = mt5.orders_get(symbol=symbol)
        else:
            raw_orders = mt5.orders_get()
            
        if raw_orders is None:
            return []
            
        orders = []
        for o in raw_orders:
            orders.append({
                "ticket": o.ticket,
                "symbol": o.symbol,
                "volume": o.volume,
                "type": o.type, # Map details as needed
                "price_open": o.price_open,
                "sl": o.sl,
                "tp": o.tp,
                "comment": o.comment,
                "time_setup": o.time_setup
            })
        return orders

    @staticmethod
    def get_closed_trades(days=1, symbol=None):
        if not MT5Client.check_connection():
            return []
        
        from_date = int(time.time()) - (days * 24 * 3600)
        to_date = int(time.time()) + 3600
        
        # Query deals (execution history)
        history_deals = mt5.history_deals_get(from_date, to_date)
        if history_deals is None:
            return []
            
        deals = []
        for d in history_deals:
            # Filters entry point transactions, only count transaction deals with profit adjustments
            # We want trades that closed.
            if d.entry == mt5.DEAL_ENTRY_OUT or d.entry == mt5.DEAL_ENTRY_OUT_BY:
                if symbol and d.symbol != symbol:
                    continue
                deals.append({
                    "ticket": d.position_id,
                    "deal_ticket": d.ticket,
                    "order": d.order,
                    "symbol": d.symbol,
                    "volume": d.volume,
                    "direction": "SELL" if d.type == mt5.DEAL_TYPE_BUY else "BUY", # DEAL_TYPE_BUY is BUY transaction, closed trade was SELL
                    "close_price": d.price,
                    "profit": d.profit,
                    "commission": d.commission,
                    "swap": d.swap,
                    "time": time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(d.time)),
                    "pnl": d.profit + d.commission + d.swap
                })
        return deals

    @staticmethod
    def execute_order(symbol, action, volume, sl=0.0, tp=0.0, comment=""):
        if not MT5Client.check_connection():
            return {"success": False, "error": "MT5 Not Connected"}
            
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"success": False, "error": f"Failed to get price action for {symbol}"}
            
        # Determine filling mode
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return {"success": False, "error": f"Symbol {symbol} missing."}
            
        # Fill mode check
        filling_mode = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode & 1:  # SYMBOL_FILLING_FOK (1)
            filling_mode = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode & 2:  # SYMBOL_FILLING_IOC (2)
            filling_mode = mt5.ORDER_FILLING_IOC
        else:
            filling_mode = mt5.ORDER_FILLING_RETURN

        is_buy = action.upper() == "BUY"
        price = tick.ask if is_buy else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(price),
            "sl": float(sl) if sl else 0.0,
            "tp": float(tp) if tp else 0.0,
            "deviation": 20,
            "magic": 2026888, # Magic number to identify Titan trades
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        
        logger.info(f"Submitting Trade request: {action} {volume} {symbol} SL={sl:.3f} TP={tp:.3f}")
        result = mt5.order_send(request)
        
        if result is None:
            return {"success": False, "error": "order_send returned None"}
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Trade Execution Failed: code={result.retcode}, comment={result.comment}")
            return {
                "success": False, 
                "error": f"Code {result.retcode}: {result.comment}",
                "retcode": result.retcode
            }
            
        logger.info(f"Trade Executed Successfully (Ticket={result.order}, Fill Price={result.price})")
        return {
            "success": True,
            "ticket": result.order,
            "price": result.price,
            "volume": result.volume,
            "retcode": result.retcode
        }

    @staticmethod
    def modify_sl_tp(ticket, sl, tp):
        if not MT5Client.check_connection():
            return False
            
        # First retrieve raw position to modify
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error(f"Cannot modify parameters. Position {ticket} not found.")
            return False
            
        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "sl": float(sl),
            "tp": float(tp),
            "position": int(ticket)
        }
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to modify SL/TP for Ticket {ticket}: {getattr(result, 'comment', 'None')}")
            return False
        logger.info(f"Modified SL/TP for Ticket {ticket} successfully (SL={sl:.3f}, TP={tp:.3f})")
        return True

    @staticmethod
    def close_position(ticket, volume=None, comment="Close position"):
        if not MT5Client.check_connection():
            return False
            
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error(f"Cannot close position. Position {ticket} not active.")
            return False
            
        pos = positions[0]
        close_volume = volume if volume is not None else pos.volume
        
        symbol_info = mt5.symbol_info(pos.symbol)
        filling_mode = mt5.ORDER_FILLING_FOK
        if symbol_info.filling_mode & 1:  # SYMBOL_FILLING_FOK (1)
            filling_mode = mt5.ORDER_FILLING_FOK
        elif symbol_info.filling_mode & 2:  # SYMBOL_FILLING_IOC (2)
            filling_mode = mt5.ORDER_FILLING_IOC
        else:
            filling_mode = mt5.ORDER_FILLING_RETURN

        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        order_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        
        # Get live tick
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if is_buy else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": float(close_volume),
            "type": order_type,
            "position": int(ticket),
            "price": float(price),
            "deviation": 20,
            "magic": 2026888,
            "comment": comment,
            "type_filling": filling_mode,
        }
        
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Failed to close position {ticket}: {getattr(result, 'comment', 'None')}")
            return False
        logger.info(f"Closed Position {ticket} successfully (volume={close_volume}, price={result.price})")
        return True
