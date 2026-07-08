//+------------------------------------------------------------------+
//|                                           TitanBasketScalper.mq5|
//|                        Project Titan V2 Quantitative Desk        |
//|                                  https://github.com/TitanQuant   |
//+------------------------------------------------------------------+
#property copyright "Project Titan V2 Quantitative Desk"
#property link      "https://github.com/TitanQuant"
#property version   "2.00"
#property strict

// Include standard trade library
#include <Trade\Trade.mqh>
CTrade trade;

//--- INPUT GROUPS ---

//--- Group 1: Signal Parameters
input group "=== SIGNAL PARAMETERS ==="
input int      InpEMA20_Period   = 20;       // EMA Fast Period
input int      InpEMA50_Period   = 50;       // EMA Slow Period
input int      InpRSI_Period     = 14;       // RSI Period
input double   InpRSI_Bullish    = 55.0;     // RSI Bullish Threshold
input double   InpRSI_Bearish    = 45.0;     // RSI Bearish Threshold
input int      InpMACD_Fast      = 12;       // MACD Fast EMA
input int      InpMACD_Slow      = 26;       // MACD Slow EMA
input int      InpMACD_Signal    = 9;        // MACD Signal SMA
input int      InpBB_Period      = 20;       // Bollinger Bands Period
input double   InpBB_Deviation   = 2.0;      // Bollinger Bands Deviation
input int      InpBB_Shift       = 0;        // Bollinger Bands Shift
input int      InpSwingLookback   = 20;       // Swing High/Low lookback (candles)
input int      InpBaseThreshold  = 65;       // Required score (out of 100)
input int      InpMinLeadScore   = 15;       // Required lead margin over opposite

//--- Group 2: Basket Management
input group "=== BASKET PARAMETERS ==="
input int      InpMaxPositions   = 7;        // Max concurrent basket positions
enum ENUM_PROFIT_MODE {
   PROFIT_MODE_FIXED,   // Fixed Currency Target ($)
   PROFIT_MODE_EQUITY   // Percentage of Equity (%)
};
input ENUM_PROFIT_MODE InpProfitMode = PROFIT_MODE_FIXED; // Profit Target Mode
input double   InpProfitTarget   = 15.00;    // Target Value (Cash value or Equity %)
input ENUM_PROFIT_MODE InpLossMode = PROFIT_MODE_FIXED; // Stop Loss Mode
input double   InpMaxDrawdown    = 150.00;   // Max drawdown margin (Cash value or Equity %)
input int      InpMaxHoldMinutes = 10;       // Time cap to close basket (Minutes)
input bool     InpCloseOnSignalFlip = false; // Close basket immediately if signal flips opposite

//--- Group 3: Adaptive Learning
input group "=== ADAPTIVE LEARNING ==="
input bool     InpEnableAdvisory = true;     // Enable rolling-window self-tuning
input int      InpConsecutiveThreshold = 3;  // Consec wins/losses to trigger tuning
input int      InpScoreAdjustment = 5;       // Entry score increase after loss streak
input double   InpSizeAdjustment  = 0.75;    // Lot multiplier after loss streak

//--- Group 4: Risk Guards
input group "=== HARD RISK GUARDS ==="
input double   InpMaxDailyLossPct = 3.0;     // Max Daily Loss % of Equity (Kill-switch)
input int      InpMaxSpreadPts   = 300;      // Max Spread Filter in Points
input int      InpMaxBasketsDay  = 10;       // Max completed baskets per day
enum ENUM_SESSION_FILTER {
   SESSION_ALL,         // All Sessions
   SESSION_LON_NY,      // London-NY Overlap Only (12:00-16:00 UTC)
   SESSION_LONDON,      // London Session (07:00-15:00 UTC)
   SESSION_NY           // New York Session (12:00-20:00 UTC)
};
input ENUM_SESSION_FILTER InpSessionFilter = SESSION_ALL; // Active Session Filter

//--- Group 5: Position Sizing
input group "=== POSITION SIZING ==="
enum ENUM_SIZING_MODE {
   SIZING_FIXED,        // Fixed Lot size
   SIZING_AUTO_RISK     // Auto Risk %
};
input ENUM_SIZING_MODE InpSizingMode = SIZING_FIXED; // Position Sizing Mode
input double   InpFixedLotSize   = 0.01;     // Fixed Lot size
input double   InpBasketRiskPct  = 1.0;      // Risk% of Equity per basket

//--- Group 6: Magic Setup
input int      MagicNumber       = 2026888;  // Unique Magic Number

//--- GLOBAL SYSTEM STATE ---
int      IndicatorEMA20_M5   = INVALID_HANDLE;
int      IndicatorEMA50_M5   = INVALID_HANDLE;
int      IndicatorEMA20_M15  = INVALID_HANDLE;
int      IndicatorEMA50_M15  = INVALID_HANDLE;
int      IndicatorRSI        = INVALID_HANDLE;
int      IndicatorMACD       = INVALID_HANDLE;
int      IndicatorBB         = INVALID_HANDLE;

datetime LastCandleCheck     = 0;
int      BasketHistory[20];                  // Rolling-window execution log (1=Win, 0=Loss)
int      HistoryCount        = 0;

// Adaptive state overrides
int      AdaptiveScoreOffset = 0;
double   AdaptiveLotScale    = 1.0;
int      AdaptiveMaxPositionsLimit = 0;
double   AdaptiveProfitTargetScale = 1.0;

// Counter telemetry
int      BasketsCompletedToday = 0;
datetime LastTelemetryDay     = 0;
double   StartingEquityToday = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Seed trade parameters
   trade.SetExpertMagicNumber(MagicNumber);
   
   // Initialize timeframe indicators
   IndicatorEMA20_M5  = iMA(_Symbol, PERIOD_M5, InpEMA20_Period, 0, MODE_EMA, PRICE_CLOSE);
   IndicatorEMA50_M5  = iMA(_Symbol, PERIOD_M5, InpEMA50_Period, 0, MODE_EMA, PRICE_CLOSE);
   IndicatorEMA20_M15 = iMA(_Symbol, PERIOD_M15, InpEMA20_Period, 0, MODE_EMA, PRICE_CLOSE);
   IndicatorEMA50_M15 = iMA(_Symbol, PERIOD_M15, InpEMA50_Period, 0, MODE_EMA, PRICE_CLOSE);
   
   IndicatorRSI       = iRSI(_Symbol, PERIOD_CURRENT, InpRSI_Period, PRICE_CLOSE);
   IndicatorMACD      = iMACD(_Symbol, PERIOD_CURRENT, InpMACD_Fast, InpMACD_Slow, InpMACD_Signal, PRICE_CLOSE);
   IndicatorBB        = iBands(_Symbol, PERIOD_CURRENT, InpBB_Period, InpBB_Shift, InpBB_Deviation, PRICE_CLOSE);
   
   if(IndicatorEMA20_M5  == INVALID_HANDLE || IndicatorEMA50_M5  == INVALID_HANDLE ||
      IndicatorEMA20_M15 == INVALID_HANDLE || IndicatorEMA50_M15 == INVALID_HANDLE ||
      IndicatorRSI       == INVALID_HANDLE || IndicatorMACD      == INVALID_HANDLE ||
      IndicatorBB        == INVALID_HANDLE)
   {
      Print("[Titan EA] Error initializing technical indicator handles. Retrying at next tick.");
      return(INIT_FAILED);
   }
   
   // Initialize state overrides
   AdaptiveScoreOffset = 0;
   AdaptiveLotScale    = 1.0;
   AdaptiveMaxPositionsLimit = InpMaxPositions;
   AdaptiveProfitTargetScale = 1.0;
   
   ArrayInitialize(BasketHistory, -1);
   HistoryCount = 0;
   
   ResetDailyMetrics();
   
   Print("[Titan EA] Initialized successfully. Magic=", MagicNumber, " Basket size limits=", InpMaxPositions);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(IndicatorEMA20_M5);
   IndicatorRelease(IndicatorEMA50_M5);
   IndicatorRelease(IndicatorEMA20_M15);
   IndicatorRelease(IndicatorEMA50_M15);
   IndicatorRelease(IndicatorRSI);
   IndicatorRelease(IndicatorMACD);
   IndicatorRelease(IndicatorBB);
   
   Print("[Titan EA] Deinitialized. ReasonCode=", reason);
}

//+------------------------------------------------------------------+
//| Daily Metrics Reset Check                                        |
//+------------------------------------------------------------------+
void ResetDailyMetrics()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   
   if(dt.day != LastTelemetryDay)
   {
      StartingEquityToday = AccountInfoDouble(ACCOUNT_EQUITY);
      BasketsCompletedToday = 0;
      LastTelemetryDay = dt.day;
      Print("[Titan EA] Daily metrics reset. Opening Equity: $", DoubleToString(StartingEquityToday, 2));
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Reset telemetry values at day boundaries
   ResetDailyMetrics();
   
   // Handle real-time basket floating liquidation checks
   MonitorBasketLiquidation();
   
   // Signal calculation and trade triggers occur strictly on candle close boundaries
   if(!IsNewCandle()) return;
   
   EvaluateMarketTriggers();
}

//+------------------------------------------------------------------+
//| Candle Boundaries Monitor                                        |
//+------------------------------------------------------------------+
bool IsNewCandle()
{
   datetime current = (datetime)SeriesInfoInteger(_Symbol, PERIOD_CURRENT, SERIES_LASTBAR_DATE);
   if(current != LastCandleCheck)
   {
      LastCandleCheck = current;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Calculate dynamic pip distance / target values                   |
//+------------------------------------------------------------------+
double GetPointsValue(double price)
{
   return SymbolInfoDouble(_Symbol, SYMBOL_POINT);
}

//+------------------------------------------------------------------+
//| Monitor Open Basket Telemetry & Perform Exits                    |
//+------------------------------------------------------------------+
void MonitorBasketLiquidation()
{
   int total_positions = 0;
   double total_profit = 0.0;
   datetime earliest_time = 0;
   
   // Sum indicators of magic number tickets
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         {
            total_positions++;
            total_profit += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP) + PositionGetDouble(POSITION_COMMISSION);
            
            datetime pos_time = (datetime)PositionGetInteger(POSITION_TIME);
            if(earliest_time == 0 || pos_time < earliest_time)
            {
               earliest_time = pos_time;
            }
         }
      }
   }
   
   if(total_positions == 0) return;
   
   // Determine target boundaries dynamically
   double target_currency = 0;
   double loss_currency = 0;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   
   if(InpProfitMode == PROFIT_MODE_FIXED)
      target_currency = InpProfitTarget * AdaptiveProfitTargetScale;
   else
      target_currency = (InpProfitTarget * AdaptiveProfitTargetScale / 100.0) * equity;
      
   if(InpLossMode == PROFIT_MODE_FIXED)
      loss_currency = InpMaxDrawdown;
   else
      loss_currency = (InpMaxDrawdown / 100.0) * equity;
      
   // Trigger 1: Profit Target hit
   if(total_profit >= target_currency)
   {
      Print("[Titan EA] Basket Target Profit met ($", DoubleToString(total_profit, 2), " >= $", DoubleToString(target_currency, 2), "). Liquidating basket.");
      CloseAllBasketPositions(true); // Win
      return;
   }
   
   // Trigger 2: Max Drawdown hit
   if(total_profit <= -loss_currency)
   {
      Print("[Titan EA] Basket Maximum Drawdown hit ($", DoubleToString(total_profit, 2), " <= -$", DoubleToString(loss_currency, 2), "). Liquidating basket in loss.");
      CloseAllBasketPositions(false); // Loss
      return;
   }
   
   // Trigger 3: Time Cap limit hit
   double elapsed_mins = (double)(TimeCurrent() - earliest_time) / 60.0;
   if(elapsed_mins >= InpMaxHoldMinutes)
   {
      Print("[Titan EA] Basket execution window timed out (", DoubleToString(elapsed_mins, 1), " mins >= ", InpMaxHoldMinutes, " mins). Liquidating basket.");
      CloseAllBasketPositions(total_profit > 0);
      return;
   }
}

//+------------------------------------------------------------------+
//| Close all positions of open basket and update statistics        |
//+------------------------------------------------------------------+
void CloseAllBasketPositions(bool was_profitable)
{
   Print("[Titan EA] Closing all basket items. Profitable=", was_profitable);
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         {
            trade.PositionClose(ticket);
         }
      }
   }
   
   // Maintain rolling learning memory
   RecordBasketOutcome(was_profitable ? 1 : 0);
}

//+------------------------------------------------------------------+
//| Record outcome inside rolling window memory                      |
//+------------------------------------------------------------------+
void RecordBasketOutcome(int outcome)
{
   // Shift rolling metrics
   for(int i = 19; i > 0; i--)
   {
      BasketHistory[i] = BasketHistory[i - 1];
   }
   BasketHistory[0] = outcome;
   if(HistoryCount < 20) HistoryCount++;
   
   BasketsCompletedToday++;
   
   // Print metrics and execute self-calibration routines
   Print("[Titan EA] Basket recorded! Result Code: ", (outcome == 1 ? "WIN" : "LOSS"), " Baskets completed today: ", BasketsCompletedToday);
   
   if(InpEnableAdvisory)
   {
      AdaptiveCalibrate();
   }
}

//+------------------------------------------------------------------+
//| Process performance checks & tune risk                           |
//+------------------------------------------------------------------+
void AdaptiveCalibrate()
{
   if(HistoryCount < InpConsecutiveThreshold) return;
   
   // Check consecutive streaks
   int consecutive_losses = 0;
   int consecutive_wins = 0;
   
   for(int i = 0; i < InpConsecutiveThreshold; i++)
   {
      if(BasketHistory[i] == 0) consecutive_losses++;
      else if(BasketHistory[i] == 1) consecutive_wins++;
   }
   
   if(consecutive_losses == InpConsecutiveThreshold)
   {
      // Apply risk contraction overlays
      AdaptiveScoreOffset = InpScoreAdjustment;
      AdaptiveLotScale = InpSizeAdjustment;
      AdaptiveMaxPositionsLimit = MathMax(2, InpMaxPositions - 3);
      AdaptiveProfitTargetScale = 0.8; // Lower profit target to exit faster
      
      Print("[Titan EA] Adaptive Advisory: Risk Contraction engaged. Required Score: ", InpBaseThreshold + AdaptiveScoreOffset, 
            " | Lot scale: ", DoubleToString(AdaptiveLotScale, 2), " | Max concurrent: ", AdaptiveMaxPositionsLimit);
   }
   else if(consecutive_wins == InpConsecutiveThreshold)
   {
      // Reset back to baseline values
      AdaptiveScoreOffset = 0;
      AdaptiveLotScale = 1.0;
      AdaptiveMaxPositionsLimit = InpMaxPositions;
      AdaptiveProfitTargetScale = 1.0;
      
      Print("[Titan EA] Adaptive Advisory: Streak recovered. Standard parameters restored.");
   }
}

//+------------------------------------------------------------------+
//| Execute structural and momentum indicators checks                |
//+------------------------------------------------------------------+
void EvaluateMarketTriggers()
{
   // Guard 1: Daily loss kill-switch bounds check
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(StartingEquityToday > 0)
   {
      double loss = StartingEquityToday - equity;
      double max_loss_amount = StartingEquityToday * (InpMaxDailyLossPct / 100.0);
      if(loss >= max_loss_amount)
      {
         Print("[Titan EA] Hard Risk Guard: Daily Limit reached! (Loss: $", DoubleToString(loss, 2), " >= Limit: $", DoubleToString(max_loss_amount, 2), "). Auto trading locked.");
         return;
      }
   }
   
   // Guard 2: Max spread check
   int current_spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(current_spread > InpMaxSpreadPts)
   {
      Print("[Titan EA] Hard Risk Override: Spread exceeds threshold limit (", current_spread, " > ", InpMaxSpreadPts, "). Skipping candle check.");
      return;
   }
   
   // Guard 3: Max baskets limit
   if(BasketsCompletedToday >= InpMaxBasketsDay)
   {
      Print("[Titan EA] Hard Risk Override: Daily basket activity cap reached (", BasketsCompletedToday, "/", InpMaxBasketsDay, "). Skipping trade search.");
      return;
   }
   
   // Guard 4: Active session constraints
   if(!IsSessionActive())
   {
      Print("[Titan EA] Session filter is active. Filtered session timeframe currently blockaded. Skipping search.");
      return;
   }
   
   // Fetch indicator confluences
   int bull_score = 0;
   int bear_score = 0;
   
   // 1. EMA Cross M5 + M15 (20 pts max)
   double ema20_m5  = GetMAValue(IndicatorEMA20_M5, 1);
   double ema50_m5  = GetMAValue(IndicatorEMA50_M5, 1);
   double ema20_m15 = GetMAValue(IndicatorEMA20_M15, 1);
   double ema50_m15 = GetMAValue(IndicatorEMA50_M15, 1);
   
   if(ema20_m5 > ema50_m5 && ema20_m15 > ema50_m15)
      bull_score += 20;
   else if(ema20_m5 < ema50_m5 && ema20_m15 < ema50_m15)
      bear_score += 20;
   else
   {
      if(ema20_m5 > ema50_m5) bull_score += 10;
      else bear_score += 10;
      
      if(ema20_m15 > ema50_m15) bull_score += 10;
      else bear_score += 10;
   }
   
   // 2. RSI Direction (20 pts max)
   double rsi_val = GetRSIValue(1);
   if(rsi_val >= InpRSI_Bullish)
   {
      bull_score += 20;
   }
   else if(rsi_val <= InpRSI_Bearish)
   {
      bear_score += 20;
   }
   else if(rsi_val > 50.0)
   {
      bull_score += 10;
   }
   else
   {
      bear_score += 10;
   }
   
   // 3. MACD Direction and Slope (20 pts max)
   double macd_main0 = GetMACDValue(0, 1);
   double macd_sig0  = GetMACDValue(1, 1);
   double macd_main1 = GetMACDValue(0, 2);
   
   if(macd_main0 > macd_sig0 && macd_main0 > macd_main1)
      bull_score += 20;
   else if(macd_main0 < macd_sig0 && macd_main0 < macd_main1)
      bear_score += 20;
   else
   {
      if(macd_main0 > macd_sig0) bull_score += 10;
      else bear_score += 10;
      
      if(macd_main0 > macd_main1) bull_score += 10;
      else bear_score += 10;
   }
   
   // 4. Bollinger Band Breakout (20 pts max)
   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double bb_up  = GetBBValue(IndicatorBB, 1, 1);
   double bb_lo  = GetBBValue(IndicatorBB, 2, 1);
   
   if(close1 >= bb_up)
      bull_score += 20;
   else if(close1 <= bb_lo)
      bear_score += 20;
   
   // 5. Swing high/low retests (20 pts max)
   int swing_high_idx = iHighest(_Symbol, PERIOD_CURRENT, MODE_HIGH, InpSwingLookback, 2);
   int swing_low_idx  = iLowest(_Symbol, PERIOD_CURRENT, MODE_LOW, InpSwingLookback, 2);
   double swing_high  = iHigh(_Symbol, PERIOD_CURRENT, swing_high_idx);
   double swing_low   = iLow(_Symbol, PERIOD_CURRENT, swing_low_idx);
   
   double pad = 100 * GetPointsValue(close1); // 10 pips Gold buffer
   
   if(close1 >= swing_high - pad)
      bull_score += 20;
   else if(close1 <= swing_low + pad)
      bear_score += 20;
      
   // Evaluate entries based on scores
   int required_entry_score = InpBaseThreshold + AdaptiveScoreOffset;
   int open_buys = 0;
   int open_sells = 0;
   
   CountActivePositions(open_buys, open_sells);
   
   bool signal_buy = (bull_score >= required_entry_score && (bull_score - bear_score) >= InpMinLeadScore);
   bool signal_sell = (bear_score >= required_entry_score && (bear_score - bull_score) >= InpMinLeadScore);
   
   // Check Signal Flip Immediate Liquidation
   if(InpCloseOnSignalFlip)
   {
      if(open_buys > 0 && signal_sell)
      {
         Print("[Titan EA] Signal Flip detected (BUY basket open, but SELL signal generated). Liquidating basket.");
         CloseAllBasketPositions(false);
         open_buys = 0;
      }
      else if(open_sells > 0 && signal_buy)
      {
         Print("[Titan EA] Signal Flip detected (SELL basket open, but BUY signal generated). Liquidating basket.");
         CloseAllBasketPositions(false);
         open_sells = 0;
      }
   }
   
   // Execution flow triggers
   if(signal_buy)
   {
      if(open_sells == 0 && (open_buys < AdaptiveMaxPositionsLimit))
      {
         double volume = CalculatePositionSizing(InpFixedLotSize, true);
         if(volume > 0)
         {
            Print("[Titan EA] Entering BUY position. Confluence score: ", bull_score, " volume=", DoubleToString(volume, 2));
            trade.Buy(volume, _Symbol, 0.0, 0.0, 0.0, "Titan Bull Basket Entry");
         }
      }
   }
   else if(signal_sell)
   {
      if(open_buys == 0 && (open_sells < AdaptiveMaxPositionsLimit))
      {
         double volume = CalculatePositionSizing(InpFixedLotSize, false);
         if(volume > 0)
         {
            Print("[Titan EA] Entering SELL position. Confluence score: ", bear_score, " volume=", DoubleToString(volume, 2));
            trade.Sell(volume, _Symbol, 0.0, 0.0, 0.0, "Titan Bear Basket Entry");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Calculate precise Lot sizing                                     |
//+------------------------------------------------------------------+
double CalculatePositionSizing(double base_vol, bool is_buy)
{
   double final_lot = base_vol;
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   
   if(InpSizingMode == SIZING_AUTO_RISK)
   {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double risk_limit = equity * (InpBasketRiskPct / 100.0);
      
      // Since it's a basket and total exit is a fixed drawdown, the risk is capped by the basket SL:
      double loss_currency = 0;
      if(InpLossMode == PROFIT_MODE_FIXED)
         loss_currency = InpMaxDrawdown;
      else
         loss_currency = (InpMaxDrawdown / 100.0) * equity;
         
      // Split the maximum loss capacity across the allowed position count
      double cash_risk_per_trade = loss_currency / InpMaxPositions;
      
      // Calculate equivalent lot sizing. XAUUSD contract size is 100, standard tick size 0.01, tick value 0.1
      // Risk in price points roughly corresponds to:
      // profit = delta_price * contract_size * volume
      // Let's assume a protection stop of 150 points for calculation
      double est_pts = 150.0;
      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      
      double risk_per_lot = (est_pts * point / tick_size) * tick_value;
      if(risk_per_lot > 0)
      {
         final_lot = cash_risk_per_trade / risk_per_lot;
      }
   }
   
   // Apply adaptive contraction scale
   final_lot *= AdaptiveLotScale;
   
   // Round to step sizing
   int steps = (int)MathRound(final_lot / step_lot);
   final_lot = steps * step_lot;
   
   if(final_lot < min_lot) final_lot = min_lot;
   if(final_lot > max_lot) final_lot = max_lot;
   
   return final_lot;
}

//+------------------------------------------------------------------+
//| Count active positions owned by Magic Number                     |
//+------------------------------------------------------------------+
void CountActivePositions(int &buys_count, int &sells_count)
{
   buys_count = 0;
   sells_count = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         {
            long type = PositionGetInteger(POSITION_TYPE);
            if(type == POSITION_TYPE_BUY) buys_count++;
            else if(type == POSITION_TYPE_SELL) sells_count++;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check session window restrictions                                |
//+------------------------------------------------------------------+
bool IsSessionActive()
{
   if(InpSessionFilter == SESSION_ALL) return true;
   
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   
   int hour = dt.hour;
   
   if(InpSessionFilter == SESSION_LON_NY)
   {
      return (hour >= 12 && hour < 16);
   }
   else if(InpSessionFilter == SESSION_LONDON)
   {
      return (hour >= 7 && hour < 15);
   }
   else if(InpSessionFilter == SESSION_NY)
   {
      return (hour >= 12 && hour < 20);
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Helpers to copy indicator buffer values safely                   |
//+------------------------------------------------------------------+
double GetMAValue(int handle, int shift)
{
   double arr[1];
   if(CopyBuffer(handle, 0, shift, 1, arr) < 1) return 0.0;
   return arr[0];
}

double GetRSIValue(int shift)
{
   double arr[1];
   if(CopyBuffer(IndicatorRSI, 0, shift, 1, arr) < 1) return 50.0;
   return arr[0];
}

double GetMACDValue(int buffer, int shift)
{
   double arr[1];
   if(CopyBuffer(IndicatorMACD, buffer, shift, 1, arr) < 1) return 0.0;
   return arr[0];
}

double GetBBValue(int handle, int buffer, int shift)
{
   double arr[1];
   if(CopyBuffer(handle, buffer, shift, 1, arr) < 1) return 0.0;
   return arr[0];
}
