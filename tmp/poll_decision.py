import urllib.request, json, time

for i in range(3):
    req = urllib.request.urlopen("http://127.0.0.1:8555/api/decision")
    d = json.loads(req.read())
    req2 = urllib.request.urlopen("http://127.0.0.1:8555/api/telemetry")
    t = json.loads(req2.read())
    print(f"=== Poll {i+1} ===")
    print(f"Decision   : {d['decision']}  Score: {d['score']}  Confidence: {round(d['confidence']*100,1)}%")
    print(f"Regime     : {d['regime']}")
    print(f"Reason     : {d['reason'][:150]}")
    print(f"Account    : Balance={t['balance']}  Equity={t['equity']}  Open Trades={t['open_trades_count']}")
    print(f"Today PnL  : {t['today_closed_pnl']}  Win Rate: {t['win_rate']}%")
    print(f"Auto Trade : {t.get('auto_trade', False)}")
    print()
    if i < 2:
        time.sleep(3)
