import re

with open(r'd:\Trading View Agenet\templates\index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Close ORB view and open Gold view
gold_html = """
        </div> <!-- end of orb-view -->

        <!-- ── GOLD VIEW ────────────────────────────────────────────── -->
        <div id="gold-view" style="display:none;">
            <div style="display:grid; grid-template-columns: 1fr 320px; gap:20px; align-items:start;">
                
                <!-- LEFT: Scanner matrix + signals table -->
                <div style="display:flex; flex-direction:column; gap:20px; min-width:0;">
                    
                    <!-- Gold Scanner header -->
                    <div class="card" style="padding:16px 20px;">
                        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:14px;">
                            <div>
                                <h2 style="font-size:14px; font-weight:700; color:var(--text-1);">Gold (XAU/USD) BOS Scanner</h2>
                                <p style="font-size:11px; color:var(--text-3); margin-top:2px;">Smart Money Concepts Break of Structure (15m timeframe)</p>
                            </div>
                            <div style="display:flex; align-items:center; gap:8px;">
                                <button class="btn btn-ghost btn-sm" onclick="openChart('GC=F')" style="font-size:12px;">
                                    <i class="ph ph-chart-line-up"></i> Open Chart
                                </button>
                                <button class="btn btn-primary btn-sm" onclick="triggerGoldScan()">
                                    <i class="ph ph-arrows-clockwise"></i> Force Scan
                                </button>
                            </div>
                        </div>

                        <!-- Current Structure -->
                        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-top:16px;">
                            <div style="background:var(--surface-2); padding:12px; border-radius:8px; border:1px solid var(--border);">
                                <div style="font-size:11px; color:var(--text-3); text-transform:uppercase; font-weight:700; margin-bottom:4px;">Market Bias</div>
                                <div id="gold-market-bias" style="font-size:16px; font-weight:700; color:var(--text-1);">—</div>
                            </div>
                            <div style="background:var(--surface-2); padding:12px; border-radius:8px; border:1px solid var(--border);">
                                <div style="font-size:11px; color:var(--text-3); text-transform:uppercase; font-weight:700; margin-bottom:4px;">Current Price</div>
                                <div id="gold-current-price" class="mono" style="font-size:16px; font-weight:700; color:var(--text-1);">—</div>
                            </div>
                        </div>
                    </div>

                    <!-- Signals table -->
                    <div class="card" style="padding:0; overflow:hidden;">
                        <div style="padding:16px 20px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between;">
                            <div>
                                <h2 style="font-size:14px; font-weight:700; color:var(--text-1);">Recent BOS Signals</h2>
                                <p style="font-size:11px; color:var(--text-3); margin-top:2px;">Break of Structure trade setups</p>
                            </div>
                        </div>
                        <div style="overflow-x:auto; max-height:320px; overflow-y:auto;">
                            <table style="width:100%; border-collapse:collapse;">
                                <thead style="position:sticky; top:0; background:var(--surface);">
                                    <tr style="font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:var(--text-3);">
                                        <th style="text-align:left; padding:8px 10px;">Time</th>
                                        <th style="text-align:left; padding:8px 10px;">Direction</th>
                                        <th style="text-align:right; padding:8px 10px; font-family:monospace;">Entry</th>
                                        <th style="text-align:right; padding:8px 10px; font-family:monospace;">SL</th>
                                        <th style="text-align:right; padding:8px 10px; font-family:monospace;">TP</th>
                                        <th style="text-align:right; padding:8px 10px; font-family:monospace;">Confidence</th>
                                    </tr>
                                </thead>
                                <tbody id="gold-signals-tbody">
                                    <tr><td colspan="6" style="text-align:center; padding:32px; font-size:12px; color:var(--text-3);">No Gold signals yet.</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <!-- RIGHT: Settings sidebar -->
                <div style="display:flex; flex-direction:column; gap:16px;">
                    <div class="card" style="padding:16px 20px;">
                        <h2 style="font-size:13px; font-weight:700; color:var(--text-1); margin-bottom:14px;">Gold Scanner Controls</h2>
                        
                        <div style="display:flex; flex-direction:column; gap:12px; margin-bottom:16px;">
                            <button id="gold-toggle-btn" onclick="toggleGoldScanner()" class="btn btn-primary" style="width:100%; justify-content:center;">
                                <i class="ph ph-play"></i> Start Gold Scanner
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
"""

# Replace the end of main to insert gold-view before </main>
html = html.replace('        </div>\n    </main>', gold_html + '\n    </main>')

# 2. Add JavaScript logic
js_code = """
// ── Gold Strategy Logic ──────────────────────────────────────────
let currentStrategy = 'orb'; // 'orb' or 'gold'
let goldScannerRunning = false;

function switchStrategy(strategy) {
    currentStrategy = strategy;
    
    // Update tabs
    document.getElementById('tab-orb').classList.toggle('active', strategy === 'orb');
    document.getElementById('tab-gold').classList.toggle('active', strategy === 'gold');
    
    // Toggle views
    document.getElementById('orb-view').style.display = strategy === 'orb' ? 'block' : 'none';
    document.getElementById('gold-view').style.display = strategy === 'gold' ? 'block' : 'none';
    
    // Fetch data immediately when switching
    if (strategy === 'gold') fetchGoldStatus();
}

async function fetchGoldStatus() {
    if (!idToken) return;
    try {
        const res = await fetch('/api/gold/status', {
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        if (!res.ok) return;
        const data = await res.json();
        
        // Update UI
        goldScannerRunning = data.scanner_running;
        const btn = document.getElementById('gold-toggle-btn');
        if (goldScannerRunning) {
            btn.innerHTML = '<i class="ph ph-stop"></i> Stop Gold Scanner';
            btn.className = 'btn btn-danger';
        } else {
            btn.innerHTML = '<i class="ph ph-play"></i> Start Gold Scanner';
            btn.className = 'btn btn-primary';
        }
        
        document.getElementById('gold-market-bias').textContent = data.market_bias || '—';
        document.getElementById('gold-market-bias').style.color = data.market_bias === 'BULLISH' ? 'var(--green)' : (data.market_bias === 'BEARISH' ? 'var(--red)' : 'var(--text-1)');
        document.getElementById('gold-current-price').textContent = data.current_price ? data.current_price.toFixed(2) : '—';
        
        // Render signals
        const tbody = document.getElementById('gold-signals-tbody');
        if (data.signals_history && data.signals_history.length > 0) {
            tbody.innerHTML = '';
            data.signals_history.forEach(sig => {
                const tr = document.createElement('tr');
                tr.className = 'signal-row';
                const color = sig.direction === 'BULLISH' ? 'var(--green)' : 'var(--red)';
                tr.innerHTML = `
                    <td>${sig.time}</td>
                    <td style="color:${color}; font-weight:600;">${sig.direction}</td>
                    <td style="text-align:right;">${sig.entry.toFixed(2)}</td>
                    <td style="text-align:right; color:var(--red);">${sig.sl.toFixed(2)}</td>
                    <td style="text-align:right; color:var(--green);">${sig.tp.toFixed(2)}</td>
                    <td style="text-align:right;">${sig.confidence.toFixed(1)}%</td>
                `;
                tbody.appendChild(tr);
            });
        }
        
    } catch (e) {
        console.error("Error fetching Gold status:", e);
    }
}

async function triggerGoldScan() {
    if (!idToken) return;
    try {
        await fetch('/api/gold/scan', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        fetchGoldStatus();
    } catch (e) { console.error(e); }
}

async function toggleGoldScanner() {
    if (!idToken) return;
    const endpoint = goldScannerRunning ? '/api/gold/stop' : '/api/gold/start';
    try {
        await fetch(endpoint, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${idToken}` }
        });
        fetchGoldStatus();
    } catch (e) { console.error(e); }
}
"""

# Insert JS before the closing script tag
html = html.replace('// ── Init ───────────────────────────────────────────────────────────────────', js_code + '\n// ── Init ───────────────────────────────────────────────────────────────────')

# Add fetchGoldStatus to the fetchStatus loop
html = html.replace('fetchStatus();\n    setInterval(fetchStatus, 3000);', 'fetchStatus();\n    setInterval(fetchStatus, 3000);\n    setInterval(() => { if(currentStrategy === "gold") fetchGoldStatus(); }, 5000);')

with open(r'd:\Trading View Agenet\templates\index.html', 'w', encoding='utf-8') as f:
    f.write(html)
