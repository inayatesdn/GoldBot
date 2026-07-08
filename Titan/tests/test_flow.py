import unittest
import numpy as np
from datetime import datetime, timezone

from Titan.market.sessions import SessionManager
from Titan.market.economic_calendar import EconomicCalendar
from Titan.market.regime import RegimeClassifier
from Titan.strategies.technical_analysis import TechAnalysis
from Titan.core.decision_engine import DecisionEngine

class TestTitanCoreModules(unittest.TestCase):
    
    def setUp(self):
        # Generate raw mock candles for testing
        self.mock_candles_m1 = []
        base_time = 1783300000
        
        # Bullish trending mock series
        for i in range(100):
            self.mock_candles_m1.append({
                "time": base_time + (i * 60),
                "open": 2300.0 + (i * 0.1),
                "high": 2300.5 + (i * 0.1),
                "low": 2299.8 + (i * 0.1),
                "close": 2300.2 + (i * 0.1),
                "tick_volume": 100 + i,
                "spread": 15,
                "real_volume": 120 + i
            })
            
        self.mock_candles_m3 = []
        for i in range(100):
            self.mock_candles_m3.append({
                "time": base_time + (i * 180),
                "open": 2300.0 + (i * 0.3),
                "high": 2301.2 + (i * 0.3),
                "low": 2299.5 + (i * 0.3),
                "close": 2300.8 + (i * 0.3),
                "tick_volume": 200 + i,
                "spread": 15,
                "real_volume": 220 + i
            })

        self.mock_candles_m5 = []
        for i in range(100):
            self.mock_candles_m5.append({
                "time": base_time + (i * 300),
                "open": 2300.0 + (i * 0.5),
                "high": 2302.0 + (i * 0.5),
                "low": 2299.0 + (i * 0.5),
                "close": 2301.5 + (i * 0.5),
                "tick_volume": 300 + i,
                "spread": 15,
                "real_volume": 320 + i
            })

    def test_session_manager(self):
        """Tests manual global session clock calculations."""
        # 14:00 UTC should map to London & New York active
        custom_dt = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
        sess = SessionManager.get_current_sessions(custom_dt)
        
        self.assertIn("London", sess["active_sessions"])
        self.assertIn("New York", sess["active_sessions"])
        self.assertIn("London-New York Overlap", sess["overlaps"])

    def test_economic_calendar(self):
        """Tests economic event checks."""
        # NFP is calculated on first Friday. Let's find first Friday of July 2026
        # July 1, 2026 is Wednesday. First Friday is July 3, 2026.
        first_fri = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
        events = EconomicCalendar.get_scheduled_high_impact_news(first_fri.date())
        
        titles = [e["title"] for e in events]
        self.assertTrue(any("Non-Farm" in t for t in titles))
        
        # Test lock activation
        is_locked, minutes_left, title = EconomicCalendar.check_news_lock(first_fri)
        self.assertTrue(is_locked)
        self.assertIn("Non-Farm", title)

    def test_regime_classifier(self):
        """Tests market State Classification."""
        regime = RegimeClassifier.classify_market_state(self.mock_candles_m5, self.mock_candles_m1, 15)
        self.assertIsNotNone(regime["regime"])
        # Should detect trending behavior because close increases
        self.assertIn(regime["regime"], ["Strong Trend", "Weak Trend", "Breakout"])
        self.assertEqual(regime["direction"], "BULLISH")

    def test_strategy_confluence(self):
        """Tests multi timeframe indicators and confluences."""
        confluences = TechAnalysis.analyze_multi_timeframe(
            self.mock_candles_m1, self.mock_candles_m3, self.mock_candles_m5
        )
        self.assertIn("trend_aligned", confluences)
        self.assertTrue(confluences["trend_aligned"])
        self.assertEqual(confluences["macro_trend"], "BULLISH")

    def test_decision_engine(self):
        """Tests score compiles and outputs BUY/SELL/WAIT actions."""
        confluences = {
            "trend_aligned": True,
            "macro_trend": "BULLISH",
            "conf_trend": "BULLISH",
            "exec_trend": "BULLISH",
            "m1_metrics": {
                "ema_aligned": True,
                "rsi": 30.0, # Oversold supporting buy
                "macd_bullish": True,
                "bos": True,
                "choch": False,
                "ob_touched": True,
                "fvg_touched": True,
                "atr_14": 1.5,
                "close": 2350.0
            },
            "m3_metrics": {
                "bos": True,
                "choch": False
            },
            "regime": "Strong Trend",
            "regime_direction": "BULLISH",
            "volatility": "NORMAL"
        }
        
        settings = {
            "risk_pct": 1.0,
            "max_daily_loss": 3.0,
            "max_concurrent_positions": 2,
            "trading_session": "London-New York Overlap",
            "confidence_threshold": 0.70,
            "atr_multiplier": 1.5,
            "tp_multiplier": 1.5,
            "news_lock": True,
            "spread_limit": 300,
            "slippage_limit": 30,
            "auto_trade": True
        }
        
        decision = DecisionEngine.evaluate_setup(
            confluence_payload=confluences,
            spread_pts=150,
            news_lock_active=False,
            session_active=True,
            risk_reward_est=1.5,
            settings=settings
        )
        
        self.assertEqual(decision["decision"], "BUY")
        self.assertGreaterEqual(decision["score"], 80)

if __name__ == "__main__":
    unittest.main()
