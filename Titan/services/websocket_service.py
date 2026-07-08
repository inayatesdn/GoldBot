import asyncio
import logging
from typing import List, Any
from fastapi import WebSocket

from Titan.core.logger import websocket_logger

class WebSocketService:
    def __init__(self):
        self.connections: List[WebSocket] = []
        self.queue = asyncio.Queue()
        self.loop = None
        self.logger = websocket_logger

    async def connect(self, ws: WebSocket):
        """Accepts connection and registers clients for live updates."""
        try:
            await ws.accept()
            if ws not in self.connections:
                self.connections.append(ws)
            self.logger.info(f"WebSocket client connected. Total: {len(self.connections)}")
            
            # Immediately push current cached states so the UI completes load instantly
            from Titan.core.state import state
            state.lock.acquire()
            init_telemetry = state.to_telemetry_dict()
            init_positions = list(state.open_positions)
            init_decision = dict(state.latest_decision)
            candles = list(state.candles)
            state.lock.release()
            
            await ws.send_json({"type": "telemetry", "data": init_telemetry})
            await ws.send_json({"type": "positions", "data": init_positions})
            await ws.send_json({"type": "decision", "data": init_decision})
            if candles:
                await ws.send_json({"type": "candle_update", "data": candles[-1]})
                
        except Exception as e:
            self.logger.error(f"Error registering WebSocket client: {e}")

    def disconnect(self, ws: WebSocket):
        """Removes registered connections safely."""
        if ws in self.connections:
            self.connections.remove(ws)
            self.logger.info(f"WebSocket client disconnected. Total remaining: {len(self.connections)}")

    def push_update(self, msg_type: str, data: Any):
        """Thread-safe synchronous push interface for background services."""
        if self.loop:
            self.loop.call_soon_threadsafe(
                self.queue.put_nowait,
                {"type": msg_type, "data": data}
            )

    async def run_broadcast_worker(self):
        """Asynchronous execution task to route queues into client connections."""
        self.loop = asyncio.get_event_loop()
        self.logger.info("WebSocket routing broadcast worker loop is active.")
        while True:
            try:
                message = await self.queue.get()
                if message is None:
                    break
                    
                # Broadcast payload to active clients
                lost = []
                for ws in self.connections:
                    try:
                        await ws.send_json(message)
                    except Exception:
                        lost.append(ws)
                        
                for ws in lost:
                    self.disconnect(ws)
            except Exception as e:
                self.logger.error(f"WebSocket routing worker failed: {e}")
                await asyncio.sleep(0.5)

# Global singleton routing service
websocket_service = WebSocketService()
