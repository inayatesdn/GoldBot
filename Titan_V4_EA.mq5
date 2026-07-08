//+------------------------------------------------------------------+
//|                                              Titan_V4_EA.mq5      |
//|  Project Titan V4 — Advanced Gold Scalping Engine                |
//|  XAUUSD, high-performance low-timeframe execution (M1/M3/M5)     |
//|  Dynamic multi-TF confluence, volume breakout filters            |
//|  Institutional risk controls: Partial close, trailing, BE        |
//+------------------------------------------------------------------+
#property copyright "Titan"
#property version   "4.10"
#property strict

#include <Trade/Trade.mqh>
CTrade trade;

//================= INPUTS =====================================
input group "=== Timeframes & Alignment ==="
input ENUM_TIMEFRAMES ExecutionTimeframe    = PERIOD_M1;      // Entry Timeframe (M1/M3/M5)
input ENUM_TIMEFRAMES ConfirmationTimeframe = PERIOD_M3;      // Confirmation Timeframe (M3/M5/M15)
input ENUM_TIMEFRAMES MacroTrendTF1         = PERIOD_M15;     // Macro Trend TF 1 (M15/M30/H1)
input ENUM_TIMEFRAMES MacroTrendTF2         = PERIOD_H1;      // Macro Trend TF 2 (H1/H4/Daily)

input group "=== Lot Sizing & Risk ==="
input bool   UseAutoRiskLot      = true;    // true = Risk% auto-size, false = fixed lot
input double RiskPercent         = 1.0;     // % of equity risked per trade
input double FixedLot            = 0.01;    // used only if UseAutoRiskLot = false

input group "=== Entry Filters ==="
input int    EMA_Fast            = 20;
input int    EMA_Slow            = 50;
input int    RSI_Period          = 14;
input double RSI_UpperBias       = 60.0;    // RSI above this + rising = buy bias
input double RSI_LowerBias       = 40.0;    // RSI below this + falling = sell bias
input int    ATR_Period          = 14;
input double ATR_MinPoints       = 40;      // volatility floor (skip dead market) - lowered for M1/M3
input double ATR_MaxPoints       = 250;     // volatility ceiling (skip news spikes)
input int    ZoneLookback        = 15;      // bars back on Entry TF to find S/R zones
input double MinConfluenceToFire = 70.0;    // % confluence required to auto-trade
input bool   UseVolumeConfirmation = true;  // Require above-average volume for entry
input double VolumeSpikeMultiplier = 1.3;   // Current vol / Avg vol threshold

input group "=== Exit Settings ==="
input double SL_ATR_Multiplier   = 1.5;     // SL = ATR * multiplier
input double TP_RR_Ratio         = 1.8;     // TP = SL distance * this ratio
input bool   UseTrailing         = true;
input double TrailStartPoints    = 100;     // profit points before trailing begins
input double TrailStepPoints     = 50;      // trail distance behind price
input bool   UsePartialClose     = true;    // Close a percentage when partial target hit
input double PartialClosePoints  = 80;      // profit points to trigger partial close
input double PartialClosePercent = 50.0;    // percentage of volume to close (e.g. 50%)
input bool   UseBreakEven        = true;    // Move SL to Entry on partial close
input double BreakEvenBuffer     = 10;      // positive buffer in points on BE shift
input int    MaxHoldMinutes      = 15;      // exit or tighten if no progress in this time

input group "=== Risk Guards ==="
input double MaxDailyLossPercent = 3.0;     // Halt EA if daily loss exceeds this %
input int    MaxConcurrentTrades = 2;
input int    MaxConsecutiveLosses= 3;
input int    CooldownMinutesAfterLosses = 60;
input double MaxSpreadPoints     = 200;     // Spread filter (skip executions if exceeded)
input bool   UseSessionFilter    = true;
input int    SessionStartHourUTC = 11;      // London overlap open (UTC)
input int    SessionEndHourUTC   = 19;      // New York session close (UTC)

input group "=== General ==="
input ulong  MagicNumber         = 19310001;
input bool   AutoTradeEnabled    = true;    // Master switch for automatic execution

//================= GLOBALS =====================================
int handleEMA_Fast_T1, handleEMA_Slow_T1;
int handleEMA_Fast_T2, handleEMA_Slow_T2;
int handleEMA_Fast_Conf, handleEMA_Slow_Conf;
int handleRSI_Exec;
int handleATR_Exec;

datetime lastBarTime = 0;
double   dayStartEquity = 0;
datetime currentDay = 0;
int      consecutiveLosses = 0;
datetime cooldownUntil = 0;
int      lastDealsTotal = 0;

// Ticket tracking for partial closes
ulong closedTickets[];
int closedTicketsCount = 0;

//+------------------------------------------------------------------+
//| Initialize handles and environment variables                     |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Initialize dynamic indicators based on selected timeframes
   handleEMA_Fast_T1   = iMA(_Symbol, MacroTrendTF1, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA_Slow_T1   = iMA(_Symbol, MacroTrendTF1, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA_Fast_T2   = iMA(_Symbol, MacroTrendTF2, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA_Slow_T2   = iMA(_Symbol, MacroTrendTF2, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA_Fast_Conf = iMA(_Symbol, ConfirmationTimeframe, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA_Slow_Conf = iMA(_Symbol, ConfirmationTimeframe, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   handleRSI_Exec       = iRSI(_Symbol, ExecutionTimeframe, RSI_Period, PRICE_CLOSE);
   handleATR_Exec       = iATR(_Symbol, ExecutionTimeframe, ATR_Period);

   if(handleEMA_Fast_T1==INVALID_HANDLE || handleEMA_Slow_T1==INVALID_HANDLE ||
      handleEMA_Fast_T2==INVALID_HANDLE || handleEMA_Slow_T2==INVALID_HANDLE ||
      handleEMA_Fast_Conf==INVALID_HANDLE || handleEMA_Slow_Conf==INVALID_HANDLE ||
      handleRSI_Exec==INVALID_HANDLE || handleATR_Exec==INVALID_HANDLE)
     {
      Print("Titan V4: Indicator handle creation failed!");
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(MagicNumber);
   dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   currentDay = TimeCurrent() - (TimeCurrent() % 86400);
   lastDealsTotal = HistoryDealsTotal();
   
   Print("Titan V4 Expert Advisor successfully initialized. Symbol: ", _Symbol);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Cleanup indicator buffers on deinitialization                    |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleEMA_Fast_T1);
   IndicatorRelease(handleEMA_Slow_T1);
   IndicatorRelease(handleEMA_Fast_T2);
   IndicatorRelease(handleEMA_Slow_T2);
   IndicatorRelease(handleEMA_Fast_Conf);
   IndicatorRelease(handleEMA_Slow_Conf);
   IndicatorRelease(handleRSI_Exec);
   IndicatorRelease(handleATR_Exec);
  }

//+------------------------------------------------------------------+
//| Main tick check loop                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   CheckDayRollover();
   ManageOpenTrades();       // trailing, partial close, break-even checked every tick
   TrackClosedTrades();      // monitors outcome history

   // Entry decisions evaluated only once per completed bar of the execution timeframe
   datetime t = iTime(_Symbol, ExecutionTimeframe, 0);
   if(t == lastBarTime) return;
   lastBarTime = t;

   if(!AutoTradeEnabled) return;
   if(!PassesRiskGuards()) return;

   int    direction = 0;
   double confluence = EvaluateConfluence(direction);

   if(confluence >= MinConfluenceToFire && direction != 0)
     {
      OpenTrade(direction);
     }
  }

//+------------------------------------------------------------------+
//| Pull trends for configured timeframe using EMA Fast / Slow Stack  |
//+------------------------------------------------------------------+
int GetTrend(int handleFast, int handleSlow)
  {
   double fast[], slow[];
   if(CopyBuffer(handleFast, 0, 0, 2, fast) <= 0) return 0;
   if(CopyBuffer(handleSlow, 0, 0, 2, slow) <= 0) return 0;
   
   // Check current bar close alignment
   if(fast[0] > slow[0]) return 1;
   if(fast[0] < slow[0]) return -1;
   return 0;
  }

//+------------------------------------------------------------------+
//| Identifies breakout and retests of swing high/low bands           |
//+------------------------------------------------------------------+
int GetZoneSignal()
  {
   double highs[], lows[], closes[];
   int n = ZoneLookback;
   if(CopyHigh(_Symbol, ExecutionTimeframe, 1, n, highs) <= 0) return 0;
   if(CopyLow(_Symbol,  ExecutionTimeframe, 1, n, lows)  <= 0) return 0;
   if(CopyClose(_Symbol, ExecutionTimeframe, 0, 2, closes) <= 0) return 0;

   // Set as series to ensure index 0 is most recent completed bar (bar 0) and 1 is previous bar (bar 1)
   ArraySetAsSeries(closes, true);

   double zoneHigh = highs[ArrayMaximum(highs)];
   double zoneLow  = lows[ArrayMinimum(lows)];
   double lastClose = closes[0];
   double prevClose = closes[1];

   // Breakout confirmation
   if(prevClose <= zoneHigh && lastClose > zoneHigh) return 1;
   if(prevClose >= zoneLow  && lastClose < zoneLow)  return -1;

   // Retest confirmation inside support/resistance bounds
   double tolerance = 80 * _Point; // 8 pips for Gold
   if(MathAbs(lastClose - zoneHigh) < tolerance && lastClose <= zoneHigh) return 1;
   if(MathAbs(lastClose - zoneLow)  < tolerance && lastClose >= zoneLow)  return -1;

   return 0;
  }

//+------------------------------------------------------------------+
//| Volume filter: checks for volume breakout surges                |
//+------------------------------------------------------------------+
bool IsVolumeConfirmed()
  {
   if(!UseVolumeConfirmation) return true;
   
   long volume[];
   int copyCount = 11; // 10 previous bars + 1 current bar
   if(CopyTickVolume(_Symbol, ExecutionTimeframe, 0, copyCount, volume) < copyCount) return true;

   double sum = 0;
   for(int i = 0; i < copyCount - 1; i++)
     {
      sum += (double)volume[i];
     }
   double avgVolume = sum / (copyCount - 1);
   double currentVolume = (double)volume[copyCount - 1];

   if(currentVolume > avgVolume * VolumeSpikeMultiplier)
      return true;

   return false;
  }

//+------------------------------------------------------------------+
//| Multi-Timeframe Confluence Scorer                                |
//+------------------------------------------------------------------+
double EvaluateConfluence(int &direction)
  {
   direction = 0;

   // Volatility gate check
   double atrBuf[];
   if(CopyBuffer(handleATR_Exec, 0, 0, 1, atrBuf) <= 0) return 0;
   double atrPoints = atrBuf[0] / _Point;
   if(atrPoints < ATR_MinPoints || atrPoints > ATR_MaxPoints) return 0;

   int volScore = 15; // Volatility pass points

   // Trend directions
   int trendT1 = GetTrend(handleEMA_Fast_T1, handleEMA_Slow_T1);
   int trendT2 = GetTrend(handleEMA_Fast_T2, handleEMA_Slow_T2);
   int trendConf = GetTrend(handleEMA_Fast_Conf, handleEMA_Slow_Conf);

   int trendScore = 0;
   int trendDir = 0;

   if(trendT1 != 0 && trendT1 == trendT2)
     {
      trendScore = 30;
      trendDir = trendT1;
      
      if(trendConf == trendDir)
        {
         trendScore += 15;
        }
     }

   // Momentum index on execution TF
   double rsiBuf[];
   if(CopyBuffer(handleRSI_Exec, 0, 0, 2, rsiBuf) < 2) return 0;
   ArraySetAsSeries(rsiBuf, true);
   double rsi = rsiBuf[0];
   double rsiPrev = rsiBuf[1];

   int momentumScore = 0;
   int momentumDir = 0;
   if(rsi > RSI_UpperBias && rsi > rsiPrev)
     {
      momentumScore = 15;
      momentumDir = 1;
     }
   else if(rsi < RSI_LowerBias && rsi < rsiPrev)
     {
      momentumScore = 15;
      momentumDir = -1;
     }

   // Zone breakout checks
   int zoneDir = GetZoneSignal();
   int zoneScore = (zoneDir != 0) ? 10 : 0;

   // Volume confirmation
   bool volConfirmed = IsVolumeConfirmed();
   int volumeScore = volConfirmed ? 15 : 0;

   double totalScore = volScore + trendScore + momentumScore + zoneScore + volumeScore;

   // Direction alignment logic for fast/scalping entries:
   if(trendDir != 0)
     {
      // 1. High-probability continuation setups (Trend + Momentum + Zone Sweep)
      if(trendDir == momentumDir && (zoneDir == 0 || zoneDir == trendDir))
        {
         direction = trendDir;
        }
      // 2. High-momentum breakout breakout setups (Trend + Momentum + Volume spike)
      else if(trendDir == momentumDir && volConfirmed)
        {
         direction = trendDir;
        }
     }

   return totalScore;
  }

//+------------------------------------------------------------------+
//| Verify current safety metrics and broker limits                   |
//+------------------------------------------------------------------+
bool PassesRiskGuards()
  {
   // Daily equity protection limits
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double lossPercent = (dayStartEquity - equity) / dayStartEquity * 100.0;
   if(lossPercent >= MaxDailyLossPercent)
     {
      Comment("Titan V4: Daily drawdown threshold breached. Trading halted.");
      return false;
     }

   // Recovery cooldown
   if(TimeCurrent() < cooldownUntil)
     {
      Comment("Titan V4: EA cooling down. Time left: ", (int)(cooldownUntil - TimeCurrent()), "s");
      return false;
     }

   // Concurrent limitations
   if(CountOpenTrades() >= MaxConcurrentTrades)
      return false;

   // Spread validation Filter
   double spreadPoints = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spreadPoints > MaxSpreadPoints)
     {
      Comment("Titan V4: Market spread (", spreadPoints, ") exceeds maximum limit (", MaxSpreadPoints, ")");
      return false;
     }

   // Time session checking
   if(UseSessionFilter)
     {
      MqlDateTime dt;
      TimeToStruct(TimeGMT(), dt);
      if(dt.hour < SessionStartHourUTC || dt.hour >= SessionEndHourUTC)
        {
         Comment("Titan V4: Current hour (", dt.hour, " GMT) is outside session window.");
         return false;
        }
     }

   Comment("Titan V4 Execution: Active | Daily Loss: ", DoubleToString(lossPercent, 2), "%");
   return true;
  }

//+------------------------------------------------------------------+
//| Calculates optimal contract size based on equity risk metrics    |
//+------------------------------------------------------------------+
double CalculateLotSize(double slDistancePoints)
  {
   if(!UseAutoRiskLot) return FixedLot;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskAmount = equity * (RiskPercent / 100.0);

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double pointValue = tickValue * (_Point / tickSize);

   double lot = riskAmount / (slDistancePoints * pointValue);

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lot = MathFloor(lot / lotStep) * lotStep;
   lot = MathMax(minLot, MathMin(maxLot, lot));
   return lot;
  }

//+------------------------------------------------------------------+
//| Executes position placement orders with direct ATR parameters    |
//+------------------------------------------------------------------+
void OpenTrade(int direction)
  {
   double atrBuf[];
   if(CopyBuffer(handleATR_Exec, 0, 0, 1, atrBuf) <= 0) return;
   double atr = atrBuf[0];
   double slDistance = atr * SL_ATR_Multiplier;
   double tpDistance = slDistance * TP_RR_Ratio;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double slDistancePoints = slDistance / _Point;
   double lot = CalculateLotSize(slDistancePoints);
   if(lot <= 0) return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(direction == 1)
     {
      double sl = NormalizeDouble(ask - slDistance, digits);
      double tp = NormalizeDouble(ask + tpDistance, digits);
      trade.Buy(lot, _Symbol, ask, sl, tp, "Titan V4 Buy");
     }
   else if(direction == -1)
     {
      double sl = NormalizeDouble(bid + slDistance, digits);
      double tp = NormalizeDouble(bid - tpDistance, digits);
      trade.Sell(lot, _Symbol, bid, sl, tp, "Titan V4 Sell");
     }
  }

//+------------------------------------------------------------------+
//| Dynamic trade controller: manages TS, partial close and BE shifts|
//+------------------------------------------------------------------+
void ManageOpenTrades()
  {
   CleanClosedTickets();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long   type       = PositionGetInteger(POSITION_TYPE);
      double openPrice   = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL   = PositionGetDouble(POSITION_SL);
      double currentTP   = PositionGetDouble(POSITION_TP);
      double currentVol  = PositionGetDouble(POSITION_VOLUME);
      datetime openTime  = (datetime)PositionGetInteger(POSITION_TIME);
      int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

      double price = (type == POSITION_TYPE_BUY) ?
                      SymbolInfoDouble(_Symbol, SYMBOL_BID) :
                      SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double profitPoints = (type == POSITION_TYPE_BUY) ?
                             (price - openPrice) / _Point :
                             (openPrice - price) / _Point;

      // 1. Partial close profit lock
      if(UsePartialClose && profitPoints >= PartialClosePoints && !IsTicketPartiallyClosed(ticket))
        {
         double closeVol = NormalizeDouble(currentVol * (PartialClosePercent / 100.0), 2);
         double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
         double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
         closeVol = MathFloor(closeVol / lotStep) * lotStep;
         
         if(closeVol >= minLot && closeVol < currentVol)
           {
            if(trade.PositionClosePartial(ticket, closeVol))
              {
               AddClosedTicket(ticket);
               Print("Titan V4: Partially closed position ticket ", ticket, " for ", closeVol, " lots.");
               
               // 2. Break-Even scaling after partial close
               if(UseBreakEven)
                 {
                  double beSL;
                  if(type == POSITION_TYPE_BUY)
                     beSL = NormalizeDouble(openPrice + BreakEvenBuffer * _Point, digits);
                  else
                     beSL = NormalizeDouble(openPrice - BreakEvenBuffer * _Point, digits);
                     
                  bool improves = (type == POSITION_TYPE_BUY) ? (beSL > currentSL) : (currentSL == 0 || beSL < currentSL);
                  if(improves)
                    {
                     trade.PositionModify(ticket, beSL, currentTP);
                    }
                 }
              }
           }
        }

      // 3. Dynamic trailing stop trailing logic
      if(UseTrailing && profitPoints >= TrailStartPoints)
        {
         double newSL;
         if(type == POSITION_TYPE_BUY)
           {
            newSL = NormalizeDouble(price - TrailStepPoints * _Point, digits);
            if(newSL > currentSL)
               trade.PositionModify(ticket, newSL, currentTP);
           }
         else
           {
            newSL = NormalizeDouble(price + TrailStepPoints * _Point, digits);
            if(currentSL == 0 || newSL < currentSL)
               trade.PositionModify(ticket, newSL, currentTP);
           }
        }

      // 4. Time limit exit constraints for stale market positions
      int heldMinutes = (int)((TimeCurrent() - openTime) / 60);
      if(heldMinutes >= MaxHoldMinutes && profitPoints < TrailStartPoints)
        {
         double beSL = (type == POSITION_TYPE_BUY) ?
                       NormalizeDouble(openPrice + 20*_Point, digits) :
                       NormalizeDouble(openPrice - 20*_Point, digits);
         bool improves = (type == POSITION_TYPE_BUY) ? (beSL > currentSL) : (currentSL==0 || beSL < currentSL);
         if(improves)
           {
            trade.PositionModify(ticket, beSL, currentTP);
            Print("Titan V4: Trade stale (held ", heldMinutes, "m). Tightened SL to breakeven.");
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| Returns volume count of open positions managed by this EA        |
//+------------------------------------------------------------------+
int CountOpenTrades()
  {
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == (long)MagicNumber &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         count++;
     }
   return count;
  }

//+------------------------------------------------------------------+
//| Dynamic ticket tracker helpers for multi contract management      |
//+------------------------------------------------------------------+
void AddClosedTicket(ulong ticket)
  {
   int size = ArraySize(closedTickets);
   ArrayResize(closedTickets, size + 1);
   closedTickets[size] = ticket;
   closedTicketsCount = size + 1;
  }

bool IsTicketPartiallyClosed(ulong ticket)
  {
   for(int i = 0; i < closedTicketsCount; i++)
     {
      if(closedTickets[i] == ticket) return true;
     }
   return false;
  }

void CleanClosedTickets()
  {
   int activeCount = 0;
   ulong tempClosed[];
   ArrayResize(tempClosed, closedTicketsCount);
   
   for(int i = 0; i < closedTicketsCount; i++)
     {
      if(PositionSelectByTicket(closedTickets[i]))
        {
         tempClosed[activeCount] = closedTickets[i];
         activeCount++;
        }
     }
   
   ArrayResize(tempClosed, activeCount);
   ArrayCopy(closedTickets, tempClosed, 0, 0, WHOLE_ARRAY);
   ArrayResize(closedTickets, activeCount);
   closedTicketsCount = activeCount;
  }

//+------------------------------------------------------------------+
//| Dynamic history monitoring for drawdown limit calculations        |
//+------------------------------------------------------------------+
void TrackClosedTrades()
  {
   HistorySelect(0, TimeCurrent());
   int total = HistoryDealsTotal();
   if(total <= lastDealsTotal) return;

   for(int i = lastDealsTotal; i < total; i++)
     {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != (long)MagicNumber) continue;
      if(HistoryDealGetInteger(dealTicket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
      if(profit < 0)
        {
         consecutiveLosses++;
         if(consecutiveLosses >= MaxConsecutiveLosses)
           {
            cooldownUntil = TimeCurrent() + CooldownMinutesAfterLosses * 60;
            consecutiveLosses = 0;
            Print("Titan V4: consecutive loss limit hit, cooling down until ", TimeToString(cooldownUntil));
           }
        }
      else if(profit > 0)
        {
         consecutiveLosses = 0;
        }
     }
   lastDealsTotal = total;
  }

//+------------------------------------------------------------------+
//| Clears daily stats at day rollover                                |
//+------------------------------------------------------------------+
void CheckDayRollover()
  {
   datetime today = TimeCurrent() - (TimeCurrent() % 86400);
   if(today != currentDay)
     {
      currentDay = today;
      dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      consecutiveLosses = 0;
      cooldownUntil = 0;
     }
  }
