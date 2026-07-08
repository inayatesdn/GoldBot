import unittest
import numpy as np
from datetime import datetime, timezone

from Titan.market.intelligence.regime import MarketRegimeEngine
from Titan.market.intelligence.structure import StructureEngine
from Titan.market.intelligence.liquidity import LiquidityEngine
from Titan.market.intelligence.smc import SmartMoneyEngine
from Titan.market.intelligence.session import SessionEngine
from Titan.market.intelligence.volume import VolumeEngine
from Titan.market.intelligence.momentum import MomentumEngine
from Titan.market.intelligence.mtf import MultiTimeframeEngine
from Titan.market.intelligence.confluence import ConfluenceEngine
from Titan.market.intelligence.decision import DecisionEngine

class TestMarketIntelligenceEngines(unittest.TestCase):
    
    def setUp(self):
        # Create standard test candle series (length 40)
        self.base_time = 1783300000
        self.mock_candles = []
        for i in range(40):
            # Up trending series
            self.mock_candles.append({
                "time": self.base_time + (i * 60),
                "open": 2000.0 + (i * 0.5),
                "high": 2001.0 + (i * 0.5),
                "low": 1999.5 + (i * 0.5),
                "close": 2000.5 + (i * 0.5),
                "tick_volume": 100 + i
            })
            
    def test_market_regime_classification(self):
        """Tests MarketRegimeEngine defaults and trending classifications."""
        res = MarketRegimeEngine.classify(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("confidence", res)
        self.assertIn("reason", res)
        self.assertIn("metrics", res)
        
    def test_structure_classification(self):
        """Tests StructureEngine for swings and BOS."""
        res = StructureEngine.analyze(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("confidence", res)
        self.assertIn("metrics", res)
        
    def test_liquidity_classification(self):
        """Tests LiquidityEngine for pools and sweeps."""
        res = LiquidityEngine.analyze(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("metrics", res)
        
    def test_smc_classification(self):
        """Tests SmartMoneyEngine for FVG and OB detection."""
        res = SmartMoneyEngine.analyze(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("metrics", res)
        
    def test_session_hours_and_ranges(self):
        """Tests SessionEngine UTC calendar bounds."""
        res = SessionEngine.analyze(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("metrics", res)
        self.assertGreater(res["metrics"]["today_high"], 1999.0)
        
    def test_volume_indicators(self):
        """Tests VolumeEngine RVOL and pressure levels."""
        res = VolumeEngine.analyze(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("metrics", res)
        self.assertGreater(res["metrics"]["rvol"], 0.0)
        
    def test_momentum_indicators(self):
        """Tests MomentumEngine RSI and MACD structures."""
        res = MomentumEngine.analyze(self.mock_candles)
        self.assertIn("state", res)
        self.assertIn("metrics", res)
        
    def test_multi_timeframe_aggregator(self):
        """Tests MultiTimeframeEngine aggregate alignment checks."""
        mtf_data = {
            "M1": self.mock_candles,
            "M3": self.mock_candles,
            "M5": self.mock_candles,
            "M15": self.mock_candles,
            "M30": self.mock_candles,
            "H1": self.mock_candles
        }
        res = MultiTimeframeEngine.analyze(mtf_data)
        self.assertIn("state", res)
        self.assertIn("metrics", res)
        
    def test_confluence_math_weightings(self):
        """Tests ConfluenceEngine weighting totals."""
        regime = {"state": "Trending", "confidence": 0.85, "metrics": {"volatility_ratio": 1.0}}
        struct = {"state": "BULL_BOS", "confidence": 0.80}
        liq = {"state": "RESTING", "confidence": 0.60}
        mom = {"state": "BULL_ACCELERATING", "confidence": 0.80}
        vol = {"state": "VOLUME_SPIKE", "confidence": 0.70}
        sess = {"state": "London", "confidence": 0.90}
        
        c_score = ConfluenceEngine.calculate(regime, struct, liq, mom, vol, sess, False)
        self.assertIn("confidence", c_score)
        self.assertGreater(c_score["metrics"]["total_score"], 50.0)
        
    def test_decision_flow_instructions(self):
        """Tests DecisionEngine final instruction evaluation."""
        confluence_res = {"confidence": 0.82}
        regime_res = {"state": "Trending", "reason": "Bullish alignment", "metrics": {"volatility_ratio": 1.0, "is_expansion": True}}
        struct_res = {"state": "BULL_BOS", "reason": "BOS detected"}
        liq_res = {"state": "RESTING", "reason": "Untouched pools"}
        mom_res = {"state": "NEUTRAL"}
        vol_res = {"state": "VOLUME_SPIKE"}
        sess_res = {"state": "London Session"}
        smc_res = {"state": "NEUTRAL", "reason": "Price equilibrium"}
        
        settings = {"confidence_threshold": 0.70, "spread_limit": 300, "tp_multiplier": 1.5}
        
        decision = DecisionEngine.evaluate(
            confluence_res, regime_res, struct_res, liq_res, mom_res, vol_res, sess_res, smc_res,
            spread_pts=50, is_news_locked=False, settings=settings, last_close=2000.0, m1_atr=1.0
        )
        self.assertIn("decision", decision)
        self.assertIn("reasons_list", decision)
        self.assertIn("entry_details", decision)
