// Project Titan V2 Engine Controls Interface
let chart = null;
let candleSeries = null;
let emergencyHalted = false;

document.addEventListener("DOMContentLoaded", () => {
    initChart();
    fetchSettings();
    runUpdateLoop();
    setInterval(runUpdateLoop, 3000);  // Sync stats every 3 seconds
    setInterval(updateClock, 1000);   // Keep clocks in sync
});

function initChart() {
    const chartContainer = document.getElementById("tv-chart");
    if (!chartContainer) return;

    chart = LightweightCharts.createChart(chartContainer, {
        layout: {
            background: { type: 'solid', color: '#07080a' },
            textColor: '#90a4ae',
            fontSize: 11,
            fontFamily: 'Outfit, sans-serif'
        },
        grid: {
            vertLines: { color: '#12141a' },
            horzLines: { color: '#12141a' }
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: { color: '#c5a880', width: 1, style: 2 },
            horzLine: { color: '#c5a880', width: 1, style: 2 }
        },
        rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.05)',
            visible: true,
        },
        timeScale: {
            borderColor: 'rgba(255, 255, 255, 0.05)',
            timeVisible: true,
            secondsVisible: false,
        }
    });

    candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: '#00e676',
        downColor: '#ff1744',
        borderDownColor: '#ff1744',
        borderUpColor: '#00e676',
        wickDownColor: '#ff1744',
        wickUpColor: '#00e676',
    });

    window.addEventListener('resize', () => {
        chart.resize(chartContainer.clientWidth, 380);
    });

    fetchCandles();
}

async function fetchCandles() {
    try {
        const res = await fetch("/api/candles?count=150");
        const data = await res.json();
        if (data && data.length > 0) {
            candleSeries.setData(data);
            chart.timeScale().fitContent();
        }
    } catch (e) {
        console.error("Failed to load chart candles:", e);
    }
}

async function runUpdateLoop() {
    await fetchTelemetry();
    await fetchPositions();
    await fetchDecision();
    await fetchHistory();
    await fetchStats();
    await fetchCandles();
}

function updateClock() {
    const lblClock = document.getElementById("lbl-clock");
    if (lblClock) {
        const now = new Date();
        lblClock.innerText = now.toUTCString().replace("GMT", "UTC");
    }
}

async function fetchSettings() {
    try {
        const res = await fetch("/api/settings");
        const s = await res.json();
        
        document.getElementById("cfg-risk").value = s.risk_pct;
        document.getElementById("cfg-drawdown").value = s.max_daily_loss;
        document.getElementById("cfg-maxpos").value = s.max_concurrent_positions;
        document.getElementById("cfg-session").value = s.trading_session;
        document.getElementById("cfg-confidence").value = s.confidence_threshold;
        document.getElementById("lbl-cfg-conf").innerText = Math.round(s.confidence_threshold * 100) + "%";
        document.getElementById("cfg-spread").value = s.spread_limit;
        document.getElementById("cfg-atrmult").value = s.atr_multiplier;
        document.getElementById("cfg-tpmult").value = s.tp_multiplier;
        document.getElementById("cfg-newslock").checked = s.news_lock;
        document.getElementById("cfg-autotrade").checked = s.auto_trade;
        
        updateAutoTradeIndicator(s.auto_trade);
    } catch (e) {
        console.error("Failed to fetch parameter settings:", e);
    }
}

function updateAutoTradeIndicator(active) {
    const inc = document.getElementById("auto-trade-indicator");
    const lbl = document.getElementById("lbl-auto-trade");
    if (active) {
        inc.className = "indicator-group connected";
        lbl.innerText = "AUTO TRADE ACTIVE";
    } else {
        inc.className = "indicator-group disabled";
        lbl.innerText = "AUTO TRADE OFF";
    }
}

async function saveSettings() {
    const payload = {
        risk_pct: parseFloat(document.getElementById("cfg-risk").value),
        max_daily_loss: parseFloat(document.getElementById("cfg-drawdown").value),
        max_concurrent_positions: parseInt(document.getElementById("cfg-maxpos").value),
        trading_session: document.getElementById("cfg-session").value,
        confidence_threshold: parseFloat(document.getElementById("cfg-confidence").value),
        atr_multiplier: parseFloat(document.getElementById("cfg-atrmult").value),
        tp_multiplier: parseFloat(document.getElementById("cfg-tpmult").value),
        news_lock: document.getElementById("cfg-newslock").checked,
        spread_limit: parseInt(document.getElementById("cfg-spread").value),
        slippage_limit: 30, // Default Slippage
        auto_trade: document.getElementById("cfg-autotrade").checked
    };
    
    try {
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message || "Settings updated!");
        updateAutoTradeIndicator(payload.auto_trade);
    } catch (e) {
        console.error("Failed saving parameter configurations:", e);
        alert("Failed to save settings configurations.");
    }
}

async function fetchTelemetry() {
    try {
        const res = await fetch("/api/telemetry");
        const data = await res.json();
        
        const eleStatus = document.getElementById("connection-indicator");
        const lblStatus = document.getElementById("lbl-status");
        
        const valBalance = document.getElementById("val-balance");
        const valEquity = document.getElementById("val-equity");
        const valMarginFree = document.getElementById("val-margin-free");
        const valMarginLevel = document.getElementById("val-margin-level");
        const valPnl = document.getElementById("val-pnl");
        const btnHalt = document.getElementById("btn-halt");

        if (data.status === "CONNECTED") {
            eleStatus.className = "indicator-group connected";
            lblStatus.innerText = `CONNECTED : ${data.account} (${data.server})`;
            
            // Populate live price details on manual panel
            const askPrice = document.getElementById("ask-price");
            const bidPrice = document.getElementById("bid-price");
            const lblLivePrice = document.getElementById("lbl-live-price");
            
            if (askPrice && data.ask) askPrice.innerText = data.ask.toFixed(2);
            if (bidPrice && data.bid) bidPrice.innerText = data.bid.toFixed(2);
            if (lblLivePrice && data.bid && data.ask) {
                lblLivePrice.innerText = `XAUUSD: ${data.bid.toFixed(2)} / ${data.ask.toFixed(2)}`;
            }
        } else {
            eleStatus.className = "indicator-group disconnected";
            lblStatus.innerText = "DISCONNECTED";
        }

        const fmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: data.currency || 'USD' });
        valBalance.innerText = fmt.format(data.balance);
        const balTitle = valBalance.previousElementSibling;
        if (balTitle) balTitle.innerHTML = `Account Balance <span style="font-size:0.75rem; color:#90a4ae; font-weight:normal; margin-left:5px;">(DD: ${data.current_drawdown_pct}%)</span>`;
        
        valEquity.innerText = fmt.format(data.equity);
        const eqTitle = valEquity.previousElementSibling;
        if (eqTitle) eqTitle.innerHTML = `Equity <span style="font-size:0.75rem; color:#90a4ae; font-weight:normal; margin-left:5px;">(Risk: ${fmt.format(data.daily_risk_used)})</span>`;
        
        valMarginFree.innerText = fmt.format(data.margin_free);
        const mfTitle = valMarginFree.previousElementSibling;
        if (mfTitle) mfTitle.innerHTML = `Margin Free <span style="font-size:0.75rem; color:#90a4ae; font-weight:normal; margin-left:5px;">(Active: ${data.open_trades_count})</span>`;
        
        valMarginLevel.innerText = data.margin_level ? `${data.margin_level.toFixed(2)}%` : "0.00%";
        const mlTitle = valMarginLevel.previousElementSibling;
        if (mlTitle) mlTitle.innerHTML = `Margin Level <span style="font-size:0.72rem; color:#90a4ae; font-weight:normal; margin-left:5px;">(Spread: ${data.current_spread} | ATR: ${data.current_atr})</span>`;
        
        // Render detailed profit block inside Card P/L
        const closedPnlFmt = fmt.format(data.today_closed_pnl);
        const openPnlFmt = fmt.format(data.today_open_pnl);
        valPnl.innerHTML = `
            ${closedPnlFmt}
            <div style="font-size:0.72rem; color:#90a4ae; font-weight:normal; margin-top:5px; display:flex; justify-content:space-between; width: 100%;">
                <span>Floating: <strong>${openPnlFmt}</strong></span>
                <span>Win: <strong>${data.win_rate}%</strong></span>
            </div>
        `;
        if (data.today_closed_pnl > 0) {
            valPnl.className = "metric-value font-numeric profit-positive";
        } else if (data.today_closed_pnl < 0) {
            valPnl.className = "metric-value font-numeric profit-negative";
        } else {
            valPnl.className = "metric-value font-numeric";
        }

        // Auto trade badge
        const eleAutoTrade = document.getElementById("auto-trade-indicator");
        const lblAutoTrade = document.getElementById("lbl-auto-trade");
        if (eleAutoTrade && lblAutoTrade) {
            if (data.auto_trade) {
                eleAutoTrade.className = "indicator-group enabled";
                lblAutoTrade.innerText = "AUTO TRADE ON";
            } else {
                eleAutoTrade.className = "indicator-group disabled";
                lblAutoTrade.innerText = "AUTO TRADE OFF";
            }
        }

        emergencyHalted = data.emergency_halt;
        if (emergencyHalted) {
            btnHalt.innerText = "RESUME ALGO TRADING";
            btnHalt.className = "btn btn-gold";
        } else {
            btnHalt.innerText = "EMERGENCY HALT";
            btnHalt.className = "btn btn-danger";
        }

        // Update footer Engine Status
        const footer = document.querySelector("footer.app-footer");
        if (footer && data.system_status) {
            footer.innerHTML = `
                <p>Project Titan V2 — Automated Quantitative Investment Engine.</p>
                <div style="font-size: 0.72rem; color: #90a4ae; margin-top: 10px; display: flex; justify-content: center; gap: 15px; flex-wrap: wrap;">
                    <span>🌐 Market Feed: <strong style="color: ${data.system_status.market_feed === 'Connected' ? '#4caf50' : '#f44336'};">${data.system_status.market_feed}</strong></span>
                    <span>🏢 Broker: <strong style="color: ${data.system_status.broker === 'Connected' ? '#4caf50' : '#f44336'};">${data.system_status.broker}</strong></span>
                    <span>⚡ Execution: <strong style="color: ${data.system_status.execution_engine === 'Running' ? '#4caf50' : '#f44336'};">${data.system_status.execution_engine}</strong></span>
                    <span>🛡️ Risk Engine: <strong style="color: ${data.system_status.risk_engine === 'Running' ? '#4caf50' : '#f44336'};">${data.system_status.risk_engine}</strong></span>
                    <span>🧠 Learning: <strong style="color: ${data.system_status.learning_engine === 'Running' ? '#4caf50' : '#f44336'};">${data.system_status.learning_engine}</strong></span>
                    <span>📊 Position Mgr: <strong style="color: ${data.system_status.position_manager === 'Running' ? '#4caf50' : '#f44336'};">${data.system_status.position_manager}</strong></span>
                    <span>📝 Journal: <strong style="color: ${data.system_status.journal === 'Running' ? '#4caf50' : '#f44336'};">${data.system_status.journal}</strong></span>
                    <span>⏱️ Latency: <strong style="color: #ffeb3b;">${data.system_status.latency_ms}ms</strong></span>
                    <span>🔄 Cycle: <strong style="color: #e0e0e0;">${data.system_status.eval_cycle}</strong></span>
                </div>
            `;
        }

    } catch (e) {
        console.error("Telemetry fetch error:", e);
    }
}

// ── Tab switcher ─────────────────────────────────────────
function switchTradeTab(name) {
    const panes = ['open', 'history', 'profit', 'loss'];
    panes.forEach(p => {
        const pane = document.getElementById(`pane-${p}`);
        const tab  = document.getElementById(`tab-${p}`);
        if (p === name) {
            pane.style.display = 'block';
            tab.classList.add('active');
        } else {
            pane.style.display = 'none';
            tab.classList.remove('active');
        }
    });
}

async function fetchPositions() {
    try {
        const res = await fetch("/api/positions");
        const data = await res.json();

        // Update the Open Trades tab badge
        document.getElementById("badge-open").innerText = data.length;

        const tbody = document.getElementById("body-open-trades");
        tbody.innerHTML = "";

        if (data.length === 0) {
            tbody.innerHTML = `<tr><td class="empty-state" colspan="8">No open positions. Waiting for qualified setup...</td></tr>`;
            return;
        }

        const fmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
        data.forEach(p => {
            const tr = document.createElement("tr");
            const sideClass = p.type === "BUY" ? "profit-positive" : "profit-negative";
            const profitClass = p.profit >= 0 ? "profit-positive" : "profit-negative";
            const trailBadge = p.trailing_active === 'ACTIVE'
                ? `<span style="font-size:0.62rem; color:#ffd54f; margin-left:4px;">⟳ Trail</span>` : '';
            const beBadge = p.be_active === 'ACTIVE'
                ? `<span style="font-size:0.62rem; color:#00e676; margin-left:4px;">✓ BE</span>` : '';

            tr.innerHTML = `
                <td><strong>${p.symbol}</strong><br>
                    <span style="font-size:0.68rem;color:#90a4ae;">#${p.ticket}</span></td>
                <td><span class="${sideClass}" style="font-weight:700;">${p.type}</span>${trailBadge}${beBadge}</td>
                <td class="font-numeric">${p.volume.toFixed(2)}</td>
                <td class="font-numeric">${p.price_open.toFixed(3)}</td>
                <td class="font-numeric">${p.price_current.toFixed(3)}</td>
                <td class="font-numeric" style="font-size:0.73rem; color:#90a4ae;">
                    SL: ${p.sl > 0 ? p.sl.toFixed(3) : '—'}<br>
                    TP: ${p.tp > 0 ? p.tp.toFixed(3) : '—'}
                </td>
                <td><span class="${profitClass} font-numeric" style="font-weight:700;">${fmt.format(p.profit)}</span><br>
                    <span style="font-size:0.65rem;color:#90a4ae;">swap: ${fmt.format(p.swap)}</span></td>
                <td style="font-size:0.73rem;color:#90a4ae;">${p.strategy_name || 'Titan Scalper'}<br>
                    <span style="color:#ffd54f;" title="${p.ai_explanation}">conf: ${Math.round((p.confidence || 0.7) * 100)}% 💡</span></td>
                <td><button class="btn-close-pos" onclick="closeSinglePosition(${p.ticket})">Close</button></td>
            `;
            tbody.appendChild(tr);
        });

    } catch (e) {
        console.error("Positions fetch error:", e);
    }
}

async function fetchDecision() {
    try {
        const res = await fetch("/api/decision");
        const data = await res.json();
        
        const score = data.score;
        document.getElementById("lbl-score").innerText = score;
        
        const ring = document.getElementById("ring-score-fill");
        const offset = 314 - (314 * score) / 100;
        ring.style.strokeDashoffset = offset;

        const badge = document.getElementById("lbl-decision-badge");
        badge.innerText = data.decision;
        badge.className = `decision-badge ${data.decision.toLowerCase()}`;

        const confidencePct = Math.round(data.confidence * 100);
        document.getElementById("lbl-confidence").innerText = `${confidencePct}%`;
        document.getElementById("bar-confidence").style.width = `${confidencePct}%`;

        document.getElementById("val-market-state").innerText = data.regime || "N/A";
        document.getElementById("val-trend-state").innerText = data.trend || "N/A";
        document.getElementById("val-momentum-state").innerText = data.momentum || "N/A";
        document.getElementById("val-volatility-state").innerText = data.volatility || "N/A";
        document.getElementById("val-structure-state").innerText = data.structure || "N/A";
        document.getElementById("val-liquidity-state").innerText = data.liquidity || "N/A";
        
        document.getElementById("val-entry").innerText = data.entry ? data.entry.toFixed(2) : "0.00";
        document.getElementById("val-sl").innerText = data.sl ? data.sl.toFixed(2) : "0.00";
        document.getElementById("val-tp").innerText = data.tp ? data.tp.toFixed(2) : "0.00";
        document.getElementById("val-expected-rr").innerText = data.expected_rr || "-";
        document.getElementById("val-expected-hold").innerText = data.expected_hold || "-";
        
        if (data.decision === "WAIT") {
            document.getElementById("val-reason-text").innerHTML = `
                <strong>Next setup window:</strong> ${data.next_setup}<br>
                <strong>Evaluation Cycle Remaining:</strong> ${data.time_until_next}<br><br>
                ${data.reason}
            `;
        } else {
            document.getElementById("val-reason-text").innerText = data.reason;
        }

    } catch (e) {
        console.error("Decision intelligence query options failure:", e);
    }
}

async function fetchHistory() {
    try {
        const res = await fetch("/api/history");
        const data = await res.json();

        const fmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

        // Split into profit / loss buckets
        const profits = data.filter(h => (h.pnl || 0) > 0);
        const losses  = data.filter(h => (h.pnl || 0) < 0);

        // Update badges
        document.getElementById("badge-history").innerText = data.length;
        document.getElementById("badge-profit").innerText  = profits.length;
        document.getElementById("badge-loss").innerText    = losses.length;

        // ── Helper to build a closed-trade row ──────────────
        const buildRow = (h, cols) => {
            const sideClass   = h.direction === "BUY" ? "profit-positive" : "profit-negative";
            const profitClass = h.pnl > 0 ? "profit-positive" : h.pnl < 0 ? "profit-negative" : "";
            const pnlFmt      = h.pnl ? fmt.format(h.pnl) : "$0.00";
            const tr = document.createElement("tr");

            if (cols === 'full') {
                tr.innerHTML = `
                    <td class="font-numeric" style="font-size:0.73rem;color:#90a4ae;">#${h.ticket}</td>
                    <td><strong>${h.symbol}</strong><br>
                        <span style="font-size:0.63rem;color:#90a4ae;">${h.strategy_name || 'Titan'}</span></td>
                    <td><span class="${sideClass}">${h.direction}</span></td>
                    <td class="font-numeric">${h.volume.toFixed(2)}</td>
                    <td class="font-numeric">${h.entry_price.toFixed(3)}</td>
                    <td class="font-numeric">${h.close_price ? h.close_price.toFixed(3) : '—'}</td>
                    <td style="font-size:0.72rem;color:#90a4ae;">${h.open_time || '—'}</td>
                    <td style="font-size:0.72rem;color:#90a4ae;">${h.close_time || '—'}</td>
                    <td><span class="${profitClass} font-numeric" style="font-weight:600;">${pnlFmt}</span><br>
                        <span style="font-size:0.63rem;color:#90a4ae;">net: ${fmt.format(h.net_pnl)}</span></td>
                    <td style="font-size:0.72rem;color:#90a4ae;">${ h.exit_reason || 'TP/SL' }<br>
                        <span style="color:#ffd54f;">conf: ${Math.round((h.confidence_at_entry || 0.7)*100)}%</span></td>
                `;
            } else {
                tr.innerHTML = `
                    <td class="font-numeric" style="font-size:0.73rem;color:#90a4ae;">#${h.ticket}</td>
                    <td><strong>${h.symbol}</strong></td>
                    <td><span class="${sideClass}">${h.direction}</span></td>
                    <td class="font-numeric">${h.volume.toFixed(2)}</td>
                    <td class="font-numeric">${h.entry_price.toFixed(3)}</td>
                    <td class="font-numeric">${h.close_price ? h.close_price.toFixed(3) : '—'}</td>
                    <td style="font-size:0.72rem;color:#90a4ae;">${h.close_time || '—'}</td>
                    <td><span class="${profitClass} font-numeric" style="font-weight:700;">${pnlFmt}</span></td>
                `;
            }
            return tr;
        };

        // ── Populate History pane ──────────────────────────
        const histBody = document.getElementById("body-history");
        histBody.innerHTML = "";
        if (data.length === 0) {
            histBody.innerHTML = `<tr><td class="empty-state" colspan="10">No historical outcomes found.</td></tr>`;
        } else {
            data.forEach(h => histBody.appendChild(buildRow(h, 'full')));
        }

        // ── Populate Profit pane ───────────────────────────
        const profBody = document.getElementById("body-profit");
        profBody.innerHTML = "";
        if (profits.length === 0) {
            profBody.innerHTML = `<tr><td class="empty-state" colspan="8">No profitable trades recorded yet.</td></tr>`;
        } else {
            profits.forEach(h => profBody.appendChild(buildRow(h, 'short')));
        }

        // ── Populate Loss pane ─────────────────────────────
        const lossBody = document.getElementById("body-loss");
        lossBody.innerHTML = "";
        if (losses.length === 0) {
            lossBody.innerHTML = `<tr><td class="empty-state" colspan="8">No losing trades recorded yet.</td></tr>`;
        } else {
            losses.forEach(h => lossBody.appendChild(buildRow(h, 'short')));
        }

    } catch (e) {
        console.error("History fetch error:", e);
    }
}

async function fetchStats() {
    try {
        const res = await fetch("/api/stats");
        const data = await res.json();
        
        const list = document.getElementById("feedback-list");
        list.innerHTML = "";

        if (data.status === "insufficient_data" || !data.total_trades) {
            list.innerHTML = `<div class="empty-state">No pattern recommendations generated. Awaiting outcome data.</div>`;
            return;
        }

        // Render detailed specs box
        const statsBox = document.createElement("div");
        statsBox.style.cssText = "display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 20px; background: rgba(255, 215, 0, 0.03); padding: 12px; border-radius: 8px; border: 1px solid rgba(255, 215, 0, 0.1); font-size: 0.8rem; color: #e0e0e0;";
        statsBox.innerHTML = `
            <div>🏆 Win Rate: <strong style="color: #4caf50;">${data.win_rate}%</strong></div>
            <div>📊 Profit Factor: <strong style="color: #ffd54f;">${data.profit_factor}</strong></div>
            <div>🎲 Expectancy: <strong style="color: #ffd54f;">$${data.expectancy}</strong></div>
            <div>📈 Avg Winner: <strong style="color: #4caf50;">$${data.avg_winner}</strong></div>
            <div>📉 Avg Loser: <strong style="color: #f44336;">$${data.avg_loser}</strong></div>
            <div>⏱️ Avg Hold: <strong>${Math.round(data.avg_hold_time_seconds / 60)} min</strong></div>
            <div>🔥 Max DD: <strong style="color: #f44336;">$${data.max_drawdown}</strong></div>
            <div>☀️ Best Session: <strong>${data.best_session}</strong></div>
            <div>🌙 Worst Session: <strong>${data.worst_session}</strong></div>
            <div>🚀 Best TF: <strong>${data.best_timeframe}</strong></div>
            <div>🐢 Worst TF: <strong>${data.worst_timeframe}</strong></div>
            <div>📐 Best Setup: <strong style="color: #ffd54f;">${data.best_setup}</strong></div>
        `;
        list.appendChild(statsBox);

        if (!data.recommendations || data.recommendations.length === 0) {
            const item = document.createElement("div");
            item.className = "empty-state";
            item.innerText = "No specific optimizing actions suggested yet.";
            list.appendChild(item);
            return;
        }

        data.recommendations.forEach(rec => {
            const item = document.createElement("div");
            item.className = "advisory-item";
            item.innerHTML = `
                <div class="advisory-header">${rec.type || "ADVISORY"} Performance Notification</div>
                <div class="advisory-desc"><strong>Trigger:</strong> ${rec.reason}<br><strong>Action:</strong> ${rec.suggestion}</div>
            `;
            list.appendChild(item);
        });
    } catch (e) {
        console.error("Stats/Learning query diagnostics failure:", e);
    }
}

async function toggleHalt() {
    const url = emergencyHalted ? "/api/resume" : "/api/halt";
    try {
        const res = await fetch(url, { method: "POST" });
        const data = await res.json();
        alert(data.message);
        fetchTelemetry();
    } catch (e) {
        console.error("Halt toggle action failure:", e);
    }
}

async function triggerBacktest() {
    const symbol = document.getElementById("bt-symbol").value;
    const rangeSelect = document.getElementById("bt-range").value;
    const btn = document.getElementById("btn-backtest");
    const summaryBox = document.getElementById("backtest-pnl-summary");

    btn.innerText = "Running Replay...";
    btn.disabled = true;
    summaryBox.style.display = "none";

    try {
        const res = await fetch("/api/backtest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol, preset_range: rangeSelect })
        });
        
        const data = await res.json();
        
        if (res.status === 200) {
            document.getElementById("bt-trades").innerText = data.total_trades;
            document.getElementById("bt-winrate").innerText = `${data.win_rate}%`;
            document.getElementById("bt-pf").innerText = data.profit_factor;
            document.getElementById("bt-drawdown").innerText = `${data.max_drawdown}%`;
            document.getElementById("bt-r").innerText = data.avg_r;
            document.getElementById("bt-sharpe").innerText = data.sharpe;
            document.getElementById("bt-expectancy").innerText = data.expectancy;
            document.getElementById("bt-hold").innerText = data.avg_hold_mins;
            document.getElementById("bt-balance").innerText = `$${data.final_balance.toLocaleString()}`;
            summaryBox.style.display = "block";
        } else {
            alert(`Simulation Failed: ${data.detail || "Unknown error"}`);
        }
    } catch (e) {
        alert(`Backtest execution error: ${e}`);
    } finally {
        btn.innerText = "Run Simulation Replay";
        btn.disabled = false;
    }
}

// ── Manual controls interface implementations ─────────────────────────
function adjustLot(amount) {
    const lotInput = document.getElementById("mt-lot");
    if (!lotInput) return;
    let val = parseFloat(lotInput.value) || 0.01;
    val = Math.max(0.01, Math.min(100.0, val + amount));
    lotInput.value = val.toFixed(2);
}

function setLot(val) {
    const lotInput = document.getElementById("mt-lot");
    if (lotInput) {
        lotInput.value = val.toFixed(2);
    }
}

async function placeManualOrder(action) {
    const lotInput = document.getElementById("mt-lot");
    const statusMsg = document.getElementById("mt-status");
    if (!lotInput || !statusMsg) return;
    
    const volume = parseFloat(lotInput.value) || 0.01;
    statusMsg.innerText = `Submitting manual ${action} for ${volume} lots...`;
    statusMsg.className = "mt-status-msg";
    
    try {
        const res = await fetch("/api/manual_trade", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: action, volume: volume })
        });
        const data = await res.json();
        if (res.status === 200 && !data.error) {
            statusMsg.innerText = `Success: Ticket #${data.ticket}`;
            statusMsg.className = "mt-status-msg success";
            runUpdateLoop();
        } else {
            statusMsg.innerText = `Error: ${data.detail || data.error || "Unknown Failure"}`;
            statusMsg.className = "mt-status-msg error";
        }
    } catch (e) {
        console.error("Manual order execution error:", e);
        statusMsg.innerText = `Execution code failed: ${e}`;
        statusMsg.className = "mt-status-msg error";
    }
}

async function closeSinglePosition(ticket) {
    if (!confirm(`Are you sure you want to close position #${ticket}?`)) return;
    try {
        const res = await fetch(`/api/close_position?ticket=${ticket}`, {
            method: "POST"
        });
        const data = await res.json();
        if (res.status === 200) {
            alert(`Closed position #${ticket} successfully.`);
            runUpdateLoop();
        } else {
            alert(`Failed to close position: ${data.detail || data.message || "Unknown error"}`);
        }
    } catch (e) {
        console.error(e);
        alert(`Error closing position: ${e}`);
    }
}

async function closeAllPositions() {
    if (!confirm("Are you sure you want to LIQUIDATE and close all active positions?")) return;
    try {
        const res = await fetch("/api/close_all", {
            method: "POST"
        });
        const data = await res.json();
        alert(data.message || "Close all requested.");
        runUpdateLoop();
    } catch (e) {
        console.error(e);
        alert(`Error liquidating positions: ${e}`);
    }
}
