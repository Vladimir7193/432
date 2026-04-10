"""
=============================================================
app.py — Красивый дашборд мультипарного сканера
=============================================================
Запуск: py -3.12 -m streamlit run app.py
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config as cfg

# ─────────────────────────────────────────────────────────────────────────────
#  РЕАЛЬНЫЙ БАЛАНС С BYBIT
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def get_live_balance():
    """Получить реальный баланс и позиции с Bybit."""
    try:
        from pybit.unified_trading import HTTP
        session = HTTP(testnet=False, api_key=cfg.API_KEY, api_secret=cfg.API_SECRET)

        # Баланс
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        account = resp["result"]["list"][0]
        equity_raw = account.get("totalEquity") or "0"
        avail_raw  = account.get("totalAvailableBalance") or "0"
        equity  = float(equity_raw) if equity_raw not in ("", None) else 0.0
        available = float(avail_raw) if avail_raw not in ("", None) else 0.0

        # Открытые позиции
        pos_resp = session.get_positions(category="linear", settleCoin="USDT")
        positions = []
        total_upnl = 0.0
        for p in pos_resp["result"]["list"]:
            size = float(p.get("size", 0))
            if size > 0:
                upnl = float(p.get("unrealisedPnl", 0))
                total_upnl += upnl
                positions.append({
                    "symbol": p["symbol"],
                    "side": p["side"],
                    "size": size,
                    "entry": float(p.get("avgPrice", 0)),
                    "upnl": upnl,
                    "sl": float(p.get("stopLoss", 0)),
                    "tp": float(p.get("takeProfit", 0)),
                })

        return {
            "equity": equity,
            "available": available,
            "upnl": total_upnl,
            "positions": positions,
            "ok": True,
        }
    except Exception as e:
        return {"equity": 0.0, "available": 0.0, "upnl": 0.0, "positions": [], "ok": False, "error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
#  СТРАНИЦА
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CryptoBot Scanner",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap');

.stApp { background: #060a0f; font-family: 'Syne', sans-serif; }
.block-container { padding: 1.5rem 2rem 2rem 2rem !important; max-width: 100% !important; }

.main-header {
    background: linear-gradient(135deg, #0d1f2d 0%, #0a1628 50%, #0d1a0a 100%);
    border: 1px solid #1a3a2a; border-radius: 16px;
    padding: 24px 32px; margin-bottom: 20px;
    display: flex; align-items: center; justify-content: space-between;
    position: relative; overflow: hidden;
}
.main-header::before {
    content: ''; position: absolute; top:0;left:0;right:0;bottom:0;
    background: radial-gradient(ellipse at 20% 50%, rgba(0,255,100,0.05) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 50%, rgba(0,150,255,0.05) 0%, transparent 60%);
    pointer-events: none;
}
.header-title { font-family:'Syne',sans-serif; font-size:2rem; font-weight:800; color:#fff; letter-spacing:-0.02em; margin:0; }
.header-title span { color:#00ff64; }
.header-subtitle { font-family:'Space Mono',monospace; font-size:0.7rem; color:#4a7a5a; margin-top:4px; letter-spacing:0.08em; text-transform:uppercase; }
.live-badge { display:inline-flex; align-items:center; gap:6px; background:rgba(0,255,100,0.1); border:1px solid rgba(0,255,100,0.3); border-radius:20px; padding:6px 14px; font-family:'Space Mono',monospace; font-size:0.7rem; color:#00ff64; text-transform:uppercase; letter-spacing:0.1em; }
.live-dot { width:7px;height:7px;border-radius:50%;background:#00ff64;animation:pulse 1.5s infinite; }
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.4;transform:scale(0.7)} }

.kpi-row { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px; }
.kpi-card { background:#0d1520; border:1px solid #1a2a3a; border-radius:12px; padding:18px 20px; position:relative; overflow:hidden; }
.kpi-card::after { content:''; position:absolute; top:0;left:0;right:0; height:2px; border-radius:12px 12px 0 0; }
.kpi-card.green::after  { background:linear-gradient(90deg,#00ff64,transparent); }
.kpi-card.blue::after   { background:linear-gradient(90deg,#00aaff,transparent); }
.kpi-card.yellow::after { background:linear-gradient(90deg,#ffcc00,transparent); }
.kpi-card.red::after    { background:linear-gradient(90deg,#ff4466,transparent); }
.kpi-label { font-family:'Space Mono',monospace; font-size:0.62rem; color:#3a5a6a; text-transform:uppercase; letter-spacing:0.12em; margin-bottom:8px; }
.kpi-value { font-family:'Syne',sans-serif; font-size:1.9rem; font-weight:800; color:#fff; line-height:1; }
.kpi-value.green  { color:#00ff64; }
.kpi-value.blue   { color:#00aaff; }
.kpi-value.yellow { color:#ffcc00; }
.kpi-value.red    { color:#ff4466; }
.kpi-sub { font-family:'Space Mono',monospace; font-size:0.62rem; color:#2a4a5a; margin-top:6px; }

.sec-title { font-family:'Syne',sans-serif; font-size:0.8rem; font-weight:700; color:#4a6a7a; text-transform:uppercase; letter-spacing:0.15em; margin:20px 0 12px 0; display:flex; align-items:center; gap:10px; }
.sec-title::after { content:''; flex:1; height:1px; background:linear-gradient(90deg,#1a2a3a,transparent); }

.tbl-wrap { background:#0a1018; border:1px solid #1a2a3a; border-radius:12px; overflow:hidden; }
.tbl-row { display:grid; grid-template-columns:130px 90px 100px 80px 80px 80px 1fr 110px; align-items:center; padding:10px 20px; border-bottom:1px solid #0d1420; transition:background 0.15s; font-family:'Space Mono',monospace; font-size:0.7rem; }
.tbl-row:hover { background:#0d1520; }
.tbl-row:last-child { border-bottom:none; }
.tbl-head { background:#0d1822; color:#2a4a5a; font-size:0.6rem; text-transform:uppercase; letter-spacing:0.1em; border-bottom:1px solid #1a2a3a !important; }
.tbl-long  { border-left:3px solid #00ff64; }
.tbl-short { border-left:3px solid #ff4466; }
.tbl-hold  { border-left:3px solid #1a2a3a; }
.sym-name { color:#fff; font-weight:700; font-size:0.78rem; }
.sym-quote { color:#2a4a5a; font-size:0.6rem; }
.badge { display:inline-block; padding:3px 10px; border-radius:20px; font-size:0.62rem; font-weight:700; letter-spacing:0.06em; text-transform:uppercase; }
.badge-long  { background:rgba(0,255,100,0.12); color:#00ff64; border:1px solid rgba(0,255,100,0.25); }
.badge-short { background:rgba(255,68,102,0.12); color:#ff4466; border:1px solid rgba(255,68,102,0.25); }
.badge-hold  { background:rgba(40,60,80,0.2);   color:#3a5a6a;  border:1px solid rgba(40,60,80,0.3); }
.bar-wrap { position:relative; height:3px; background:#111820; border-radius:2px; margin:2px 4px; }
.bar-fill { height:100%; border-radius:2px; }
.bar-long  { background:#00ff64; }
.bar-short { background:#ff4466; }
.c-pos { color:#00ff64; } .c-neg { color:#ff4466; } .c-neu { color:#3a5a6a; }

.whale-card { background:#0a1018; border:1px solid #1a2a3a; border-radius:10px; padding:14px 18px; display:flex; align-items:center; gap:14px; margin-bottom:8px; }
.whale-card:hover { border-color:#ffcc00; }

.stTabs [data-baseweb="tab-list"] { background:#0a1018; border-radius:10px; padding:4px; gap:4px; border:1px solid #1a2a3a; }
.stTabs [data-baseweb="tab"] { background:transparent; color:#3a5a6a; border-radius:8px; font-family:'Space Mono',monospace; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.08em; padding:8px 16px; }
.stTabs [aria-selected="true"] { background:#0d1822 !important; color:#00ff64 !important; }
div[data-testid="stMetric"] { display:none; }
::-webkit-scrollbar{width:4px} ::-webkit-scrollbar-track{background:#0a1018} ::-webkit-scrollbar-thumb{background:#1a3a2a;border-radius:2px}
</style>
""", unsafe_allow_html=True)

st_autorefresh(interval=cfg.DASHBOARD_REFRESH_SEC * 1000, key="refresh")


# ─────────────────────────────────────────────────────────────────────────────
#  ДАННЫЕ
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=cfg.DASHBOARD_REFRESH_SEC)
def load_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["ts"])

def load_meta():
    if os.path.exists(cfg.MODEL_META_PATH):
        with open(cfg.MODEL_META_PATH) as f:
            return json.load(f)
    return {}

def demo_signals():
    rng = np.random.default_rng(int(time.time()) // 15)
    prices = {
        "BTCUSDT":67500,"ETHUSDT":3480,"SOLUSDT":172,"BNBUSDT":590,
        "XRPUSDT":0.62,"DOGEUSDT":0.165,"ADAUSDT":0.48,"AVAXUSDT":38,
        "LINKUSDT":17.2,"DOTUSDT":7.8,"MATICUSDT":0.72,"LTCUSDT":84,
        "UNIUSDT":9.4,"ATOMUSDT":8.1,"NEARUSDT":7.2,"OPUSDT":2.8,
        "ARBUSDT":1.12,"APTUSDT":9.6,"SUIUSDT":1.85,"SEIUSDT":0.58,
        "TIAUSDT":7.4,"INJUSDT":28,"WLDUSDT":4.8,"FETUSDT":2.1,
        "RENDERUSDT":8.9,"JUPUSDT":0.95,"PYTHUSDT":0.42,"STRKUSDT":1.3,
        "ONDOUSDT":1.05,"ENAUSDT":0.88,
    }
    rows = []
    for sym in cfg.SYMBOLS:
        price  = prices.get(sym, 1.0) * (1 + rng.uniform(-0.015, 0.015))
        p_long  = float(rng.uniform(0.08, 0.80))
        p_short = float(rng.uniform(0.05, max(0.06, 0.80 - p_long)))
        p_hold  = max(0, 1 - p_long - p_short)
        sig = 1 if p_long >= cfg.LONG_PROB_THRESH else (2 if p_short >= cfg.SHORT_PROB_THRESH else 0)
        rows.append(dict(
            symbol=sym, signal=sig,
            p_long=round(p_long,3), p_short=round(p_short,3), p_hold=round(p_hold,3),
            close=round(price,4), atr=round(price*0.008,4),
            sm_bias=int(rng.choice([-1,0,0,1])),
            ob_imbalance=round(float(rng.uniform(-0.5,0.5)),3),
            change_1h=round(float(rng.uniform(-0.04,0.04)),4),
            ts=datetime.now(tz=timezone.utc),
        ))
    return pd.DataFrame(rows)

signals_raw = load_csv(cfg.SIGNAL_LOG_CSV)
trades_raw  = load_csv(cfg.TRADE_LOG_CSV)
whales_raw  = load_csv(cfg.WHALE_LOG_CSV)
meta        = load_meta()
live        = get_live_balance()

is_demo = signals_raw.empty
if is_demo:
    latest = demo_signals()
else:
    latest = signals_raw.sort_values("ts").groupby("symbol").last().reset_index()
    if "change_1h" not in latest.columns:
        latest["change_1h"] = 0.0

# ─────────────────────────────────────────────────────────────────────────────
#  ЗАГОЛОВОК
# ─────────────────────────────────────────────────────────────────────────────
now_str  = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
demo_tag = ' <span style="font-size:0.55rem;color:#ffcc00;background:rgba(255,200,0,0.1);border:1px solid rgba(255,200,0,0.3);padding:2px 8px;border-radius:10px;vertical-align:middle">DEMO</span>' if is_demo else ""

st.markdown(f"""
<div class="main-header">
  <div>
    <div class="header-title">🚀 Crypto<span>Bot</span> Scanner {demo_tag}</div>
    <div class="header-subtitle">Bybit Futures &nbsp;·&nbsp; CatBoost AI &nbsp;·&nbsp; {len(cfg.SYMBOLS)} пар &nbsp;·&nbsp; 1-минутные бары</div>
  </div>
  <div style="text-align:right">
    <div class="live-badge"><span class="live-dot"></span>Live &nbsp;·&nbsp; {now_str}</div>
    <div style="font-family:'Space Mono',monospace;font-size:0.6rem;color:#2a4a5a;margin-top:8px;">Обновление каждые {cfg.DASHBOARD_REFRESH_SEC}s</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  KPI
# ─────────────────────────────────────────────────────────────────────────────
n_long  = int((latest["signal"] == 1).sum())
n_short = int((latest["signal"] == 2).sum())
total_pnl = float(trades_raw["pnl_usdt"].sum()) if not trades_raw.empty else 0.0
win_rate  = float((trades_raw["pnl_usdt"] > 0).mean() * 100) if not trades_raw.empty else 0.0
n_trades  = len(trades_raw)
best_f1   = float(meta.get("best_f1", 0.0))

# Реальный баланс
equity    = live["equity"]
available = live["available"]
upnl      = live["upnl"]
n_pos     = len(live["positions"])
balance_ok = live["ok"]

upnl_c = "green" if upnl >= 0 else "red"
upnl_s = "+" if upnl >= 0 else ""
eq_color = "green" if equity > 0 else "red"

st.markdown(f"""
<div class="kpi-row">
  <div class="kpi-card {eq_color}">
    <div class="kpi-label">💰 Реальный баланс (Bybit)</div>
    <div class="kpi-value {eq_color}">${equity:.2f}</div>
    <div class="kpi-sub">Доступно: ${available:.2f} &nbsp;·&nbsp; {'✅ Live' if balance_ok else '❌ Нет связи'}</div>
  </div>
  <div class="kpi-card {upnl_c}">
    <div class="kpi-label">📊 Открытые позиции PnL</div>
    <div class="kpi-value {upnl_c}">{upnl_s}${upnl:.2f}</div>
    <div class="kpi-sub">{n_pos} позиций открыто сейчас</div>
  </div>
  <div class="kpi-card green">
    <div class="kpi-label">🟢 Сигналов LONG</div>
    <div class="kpi-value green">{n_long}</div>
    <div class="kpi-sub">из {len(cfg.SYMBOLS)} пар сейчас</div>
  </div>
  <div class="kpi-card blue">
    <div class="kpi-label">🧠 Точность AI (F1)</div>
    <div class="kpi-value blue">{best_f1:.3f}</div>
    <div class="kpi-sub">{'✓ Модель готова' if best_f1 > 0 else '⚠ Запусти pretrain.py'}</div>
  </div>
</div>
""", unsafe_allow_html=True)

# Открытые позиции блок
if live["positions"]:
    pos_html = ""
    for p in live["positions"]:
        side_c = "c-pos" if p["side"] == "Buy" else "c-neg"
        side_s = "▲ LONG" if p["side"] == "Buy" else "▼ SHORT"
        upnl_s2 = "+" if p["upnl"] >= 0 else ""
        upnl_c2 = "#00ff64" if p["upnl"] >= 0 else "#ff4466"
        sym_base = p["symbol"].replace("USDT", "")
        pos_html += f"""
        <div style="display:flex;align-items:center;gap:16px;padding:10px 20px;
                    background:#0a1018;border:1px solid #1a2a3a;border-radius:8px;
                    margin-bottom:6px;font-family:'Space Mono',monospace;font-size:0.72rem;">
          <span style="color:#fff;font-weight:700;min-width:80px">{sym_base}/USDT</span>
          <span class="{side_c}" style="min-width:60px">{side_s}</span>
          <span style="color:#4a6a7a">Size: <span style="color:#c0d0e0">{p['size']}</span></span>
          <span style="color:#4a6a7a">Entry: <span style="color:#c0d0e0">${p['entry']:.4f}</span></span>
          <span style="color:#4a6a7a">PnL: <span style="color:{upnl_c2};font-weight:700">{upnl_s2}${p['upnl']:.4f}</span></span>
          <span style="color:#4a6a7a">SL: <span style="color:#ff4466">${p['sl']:.4f}</span></span>
          <span style="color:#4a6a7a">TP: <span style="color:#00ff64">${p['tp']:.4f}</span></span>
        </div>"""
    st.markdown(f'<div class="sec-title">Открытые позиции (Bybit Live)</div>{pos_html}', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  ТАБЫ
# ─────────────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📡  Сканер — 30 пар",
    "💼  Мои сделки",
    "🐳  Активность китов",
    "🧠  AI Модель",
    "📊  Мультитаймфрейм",
    "🔬  Бэктест",
])


# ══════════════════════════════════════════════════════
#  ТАБ 1 — СКАНЕР
# ══════════════════════════════════════════════════════
with tab1:
    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        filt = st.selectbox("Фильтр", ["Все", "LONG 🟢", "SHORT 🔴", "HOLD ⬜"], label_visibility="collapsed")
    with c2:
        srt  = st.selectbox("Сортировка", ["По вероятности", "По символу", "По изменению"], label_visibility="collapsed")

    disp = latest.copy()
    if filt == "LONG 🟢":   disp = disp[disp["signal"] == 1]
    elif filt == "SHORT 🔴": disp = disp[disp["signal"] == 2]
    elif filt == "HOLD ⬜":  disp = disp[disp["signal"] == 0]

    if srt == "По вероятности":
        disp["_mp"] = disp[["p_long","p_short"]].max(axis=1)
        disp = disp.sort_values("_mp", ascending=False)
    elif srt == "По символу":
        disp = disp.sort_values("symbol")
    elif srt == "По изменению":
        disp = disp.sort_values("change_1h", ascending=False)

    st.markdown('<div class="sec-title">Текущие сигналы по всем парам</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="tbl-wrap">
    <div class="tbl-row tbl-head">
      <span>Пара</span><span>Сигнал</span><span>Цена</span>
      <span>Изм. 1ч</span><span>P(Long)</span><span>P(Short)</span>
      <span>Уверенность</span><span>Smart Money</span>
    </div>
    """, unsafe_allow_html=True)

    for _, row in disp.iterrows():
        sig    = int(row.get("signal", 0))
        sym    = str(row.get("symbol", ""))
        price  = float(row.get("close", 0))
        p_l    = float(row.get("p_long",  0))
        p_s    = float(row.get("p_short", 0))
        ch     = float(row.get("change_1h", 0))
        sm     = int(row.get("sm_bias", 0))
        ob     = float(row.get("ob_imbalance", 0))

        rc = {1:"tbl-long", 2:"tbl-short", 0:"tbl-hold"}.get(sig, "tbl-hold")
        sb = {1:'<span class="badge badge-long">▲ LONG</span>',
              2:'<span class="badge badge-short">▼ SHORT</span>',
              0:'<span class="badge badge-hold">— HOLD</span>'}.get(sig, "")

        ps  = f"${price:,.0f}" if price>=1000 else (f"${price:.2f}" if price>=1 else f"${price:.4f}")
        chs = f"{'+' if ch>0 else ''}{ch*100:.2f}%"
        cc  = "c-pos" if ch>0 else ("c-neg" if ch<0 else "c-neu")

        bl = int(p_l * 100)
        bs = int(p_s * 100)

        if sm > 0:   sm_html = "🐋 <span style='color:#00ff64'>Быки</span>"
        elif sm < 0: sm_html = "🐋 <span style='color:#ff4466'>Медведи</span>"
        elif abs(ob) > 0.25: sm_html = f"<span style='color:#ffcc00'>ОБ {ob:+.2f}</span>"
        else:        sm_html = "<span style='color:#1a2a3a'>—</span>"

        base = sym.replace("USDT","")
        st.markdown(f"""
        <div class="tbl-row {rc}">
          <span class="sym-name">{base}<span class="sym-quote">/USDT</span></span>
          <span>{sb}</span>
          <span style="color:#c0d0e0">{ps}</span>
          <span class="{cc}">{chs}</span>
          <span style="color:#00ff64;font-weight:700">{p_l:.2f}</span>
          <span style="color:#ff4466;font-weight:700">{p_s:.2f}</span>
          <div>
            <div class="bar-wrap"><div class="bar-fill bar-long" style="width:{bl}%"></div></div>
            <div class="bar-wrap"><div class="bar-fill bar-short" style="width:{bs}%"></div></div>
          </div>
          <span>{sm_html}</span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # Тепловая карта
    if not disp.empty:
        st.markdown('<div class="sec-title" style="margin-top:24px">Тепловая карта вероятности LONG</div>', unsafe_allow_html=True)
        hm = disp.set_index("symbol")[["p_long"]].copy()
        hm.index = hm.index.str.replace("USDT","")
        vals = hm["p_long"].values
        fig_h = go.Figure(go.Heatmap(
            z=[vals], x=hm.index.tolist(), y=["P(Long)"],
            colorscale=[[0,"#1a0a0a"],[0.3,"#2a1a1a"],[0.5,"#1a1a2a"],[0.62,"#0a2a1a"],[1,"#00ff64"]],
            zmin=0, zmax=1,
            text=[[f"{v:.2f}" for v in vals]], texttemplate="%{text}",
            showscale=True,
            colorbar=dict(thickness=10, tickfont=dict(color="#4a6a7a",size=9), outlinewidth=0, bgcolor="rgba(0,0,0,0)"),
        ))
        fig_h.update_layout(
            height=115, margin=dict(l=10,r=10,t=5,b=25),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Space Mono", color="#4a6a7a", size=9),
            xaxis=dict(showgrid=False, tickfont=dict(size=8,color="#4a6a7a")),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig_h, use_container_width=True, config={"displayModeBar":False})


# ══════════════════════════════════════════════════════
#  ТАБ 2 — СДЕЛКИ
# ══════════════════════════════════════════════════════
with tab2:
    if trades_raw.empty:
        st.markdown("""
        <div style="text-align:center;padding:80px 20px;">
          <div style="font-size:3.5rem;margin-bottom:16px">📭</div>
          <div style="font-family:'Syne',sans-serif;font-size:1.1rem;color:#3a5a6a;font-weight:700">Сделок пока нет</div>
          <div style="font-family:'Space Mono',monospace;font-size:0.7rem;color:#2a3a4a;margin-top:10px">
            Запусти бота: <code style="color:#00ff64">py -3.12 bybit_paper_bot.py</code>
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        cum = trades_raw["pnl_usdt"].cumsum()
        max_dd  = float((cum - cum.cummax()).min())
        avg_win = float(trades_raw.loc[trades_raw["pnl_usdt"]>0,"pnl_usdt"].mean()) if (trades_raw["pnl_usdt"]>0).any() else 0
        avg_los = float(trades_raw.loc[trades_raw["pnl_usdt"]<0,"pnl_usdt"].mean()) if (trades_raw["pnl_usdt"]<0).any() else -1
        rr = abs(avg_win / avg_los) if avg_los != 0 else 0

        pnl_c2 = "green" if total_pnl >= 0 else "red"
        st.markdown(f"""
        <div class="kpi-row">
          <div class="kpi-card {pnl_c2}">
            <div class="kpi-label">💰 Общий PnL</div>
            <div class="kpi-value {pnl_c2}">{('+' if total_pnl>=0 else '')}{total_pnl:.2f}$</div>
            <div class="kpi-sub">{n_trades} сделок всего</div>
          </div>
          <div class="kpi-card yellow">
            <div class="kpi-label">🎯 Процент прибыльных</div>
            <div class="kpi-value yellow">{win_rate:.1f}%</div>
            <div class="kpi-sub">сделок закрыто в плюс</div>
          </div>
          <div class="kpi-card red">
            <div class="kpi-label">📉 Максимальная просадка</div>
            <div class="kpi-value red">{max_dd:.2f}$</div>
            <div class="kpi-sub">от максимума капитала</div>
          </div>
          <div class="kpi-card blue">
            <div class="kpi-label">⚖️ Соотношение риск/прибыль</div>
            <div class="kpi-value blue">{rr:.2f}x</div>
            <div class="kpi-sub">средняя прибыль / средний убыток</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        cl, cr = st.columns([3,2])
        with cl:
            st.markdown('<div class="sec-title">Рост капитала</div>', unsafe_allow_html=True)
            eq = trades_raw.assign(cap=trades_raw["pnl_usdt"].cumsum() + 1000)
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=eq["ts"], y=eq["cap"], fill="tozeroy",
                fillcolor="rgba(0,255,100,0.05)",
                line=dict(color="#00ff64", width=2), name="Капитал",
            ))
            fig_eq.add_hline(y=1000, line_dash="dash", line_color="#1a3a2a", line_width=1)
            fig_eq.update_layout(height=270, margin=dict(l=0,r=0,t=10,b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
                xaxis=dict(showgrid=False, color="#2a4a5a"),
                yaxis=dict(showgrid=True, gridcolor="#0d1520", color="#2a4a5a"),
                font=dict(family="Space Mono", size=10))
            st.plotly_chart(fig_eq, use_container_width=True, config={"displayModeBar":False})

        with cr:
            st.markdown('<div class="sec-title">Прибыль / убыток</div>', unsafe_allow_html=True)
            fig_h2 = go.Figure()
            w = trades_raw[trades_raw["pnl_usdt"]>0]["pnl_usdt"]
            l = trades_raw[trades_raw["pnl_usdt"]<0]["pnl_usdt"]
            if not w.empty: fig_h2.add_trace(go.Histogram(x=w, nbinsx=20, name="Прибыль", marker_color="rgba(0,255,100,0.6)"))
            if not l.empty: fig_h2.add_trace(go.Histogram(x=l, nbinsx=20, name="Убыток",  marker_color="rgba(255,68,102,0.6)"))
            fig_h2.add_vline(x=0, line_color="#fff", line_width=1, line_dash="dot")
            fig_h2.update_layout(height=270, margin=dict(l=0,r=0,t=10,b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                barmode="overlay",
                legend=dict(font=dict(color="#4a6a7a",size=10),bgcolor="rgba(0,0,0,0)"),
                xaxis=dict(showgrid=False, color="#2a4a5a"),
                yaxis=dict(showgrid=True, gridcolor="#0d1520", color="#2a4a5a"),
                font=dict(family="Space Mono", size=10))
            st.plotly_chart(fig_h2, use_container_width=True, config={"displayModeBar":False})

        if "symbol" in trades_raw.columns:
            st.markdown('<div class="sec-title">PnL по каждой паре</div>', unsafe_allow_html=True)
            sp = trades_raw.groupby("symbol")["pnl_usdt"].sum().sort_values(ascending=True)
            fig_sp = go.Figure(go.Bar(
                x=sp.values, y=sp.index.str.replace("USDT",""), orientation="h",
                marker_color=["#00ff64" if v>=0 else "#ff4466" for v in sp.values],
                marker_line_width=0,
            ))
            fig_sp.update_layout(height=max(180, len(sp)*26), margin=dict(l=0,r=0,t=5,b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=True, gridcolor="#0d1520", color="#2a4a5a", zeroline=True, zerolinecolor="#1a2a3a"),
                yaxis=dict(showgrid=False, color="#c0d0e0", tickfont=dict(size=10)),
                font=dict(family="Space Mono", size=10))
            st.plotly_chart(fig_sp, use_container_width=True, config={"displayModeBar":False})

        st.markdown('<div class="sec-title">Последние 20 сделок</div>', unsafe_allow_html=True)
        t_show = trades_raw.tail(20).sort_values("ts", ascending=False).copy()
        t_show = t_show.rename(columns={"ts":"Время","symbol":"Пара","side":"Направление",
            "entry_price":"Вход $","exit_price":"Выход $","pnl_usdt":"PnL $","pnl_pct":"PnL %","exit_reason":"Причина"})
        cols_show = [c for c in ["Время","Пара","Направление","Вход $","Выход $","PnL $","PnL %","Причина"] if c in t_show.columns]
        st.dataframe(t_show[cols_show], use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════
#  ТАБ 3 — КИТЫ
# ══════════════════════════════════════════════════════
with tab3:
    st.markdown("""
    <div style="background:#0a1018;border:1px solid #1a2a3a;border-radius:12px;padding:20px 24px;margin-bottom:20px;">
      <div style="font-family:'Syne',sans-serif;font-weight:700;color:#ffcc00;font-size:0.85rem;margin-bottom:14px;letter-spacing:0.05em">
        ❓ Что такое «активность китов»?
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px">
        <div>
          <div style="font-size:1.8rem;margin-bottom:8px">🐋</div>
          <div style="font-family:'Syne',sans-serif;font-weight:700;color:#fff;font-size:0.85rem">Whale Bar</div>
          <div style="font-family:'Space Mono',monospace;font-size:0.63rem;color:#3a5a6a;margin-top:6px;line-height:1.8">
            Объём свечи в <span style="color:#ffcc00">3× больше</span> среднего<br>+ сильное движение цены.<br>
            <span style="color:#4a8a5a">→ Крупный игрок вошёл в рынок</span>
          </div>
        </div>
        <div>
          <div style="font-size:1.8rem;margin-bottom:8px">🧲</div>
          <div style="font-family:'Syne',sans-serif;font-weight:700;color:#fff;font-size:0.85rem">Absorption (Поглощение)</div>
          <div style="font-family:'Space Mono',monospace;font-size:0.63rem;color:#3a5a6a;margin-top:6px;line-height:1.8">
            Большой объём, но <span style="color:#ffcc00">маленькое тело</span> свечи.<br>
            Цена почти не сдвинулась.<br>
            <span style="color:#4a8a5a">→ Кто-то «поглощает» продавцов/покупателей</span>
          </div>
        </div>
        <div>
          <div style="font-size:1.8rem;margin-bottom:8px">🎯</div>
          <div style="font-family:'Syne',sans-serif;font-weight:700;color:#fff;font-size:0.85rem">Stop Hunt (Охота за стопами)</div>
          <div style="font-family:'Space Mono',monospace;font-size:0.63rem;color:#3a5a6a;margin-top:6px;line-height:1.8">
            Цена пробила уровень и сразу <span style="color:#ffcc00">вернулась обратно</span>.<br>
            Длинный «хвост» свечи.<br>
            <span style="color:#4a8a5a">→ Выбили стопы розничных трейдеров</span>
          </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if whales_raw.empty:
        st.markdown('<div style="text-align:center;padding:40px;color:#2a4a5a;font-family:Space Mono,monospace;font-size:0.75rem">Бот ещё не запущен — китов не зафиксировано</div>', unsafe_allow_html=True)
    else:
        wc = whales_raw["event_type"].value_counts()
        st.markdown(f"""
        <div class="kpi-row">
          <div class="kpi-card yellow">
            <div class="kpi-label">🐋 Whale Bars</div>
            <div class="kpi-value yellow">{wc.get('whale_bar',0)}</div>
            <div class="kpi-sub">крупных вхождений</div>
          </div>
          <div class="kpi-card blue">
            <div class="kpi-label">🧲 Поглощений</div>
            <div class="kpi-value blue">{wc.get('absorption',0)}</div>
            <div class="kpi-sub">absorption bars</div>
          </div>
          <div class="kpi-card red">
            <div class="kpi-label">🎯 Охот за стопами</div>
            <div class="kpi-value red">{wc.get('stop_hunt',0)}</div>
            <div class="kpi-sub">stop hunt events</div>
          </div>
          <div class="kpi-card green">
            <div class="kpi-label">📊 Всего событий</div>
            <div class="kpi-value green">{len(whales_raw)}</div>
            <div class="kpi-sub">за всё время</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="sec-title">Последние события</div>', unsafe_allow_html=True)
        icons = {"whale_bar":"🐋","absorption":"🧲","stop_hunt":"🎯"}
        for _, row in whales_raw.tail(12).sort_values("ts", ascending=False).iterrows():
            icon  = icons.get(str(row.get("event_type","")), "🔔")
            sym   = str(row.get("symbol","")).replace("USDT","")
            etype = str(row.get("event_type","")).replace("_"," ").title()
            price = float(row.get("price", 0))
            volm  = float(row.get("vol_mult", 0))
            ts_s  = pd.Timestamp(row["ts"]).strftime("%H:%M:%S")
            ps    = f"${price:,.0f}" if price>=1000 else (f"${price:.2f}" if price>=1 else f"${price:.4f}")
            st.markdown(f"""
            <div class="whale-card">
              <div style="font-size:1.8rem">{icon}</div>
              <div style="flex:1">
                <div style="font-family:'Syne',sans-serif;font-weight:700;color:#fff;font-size:0.9rem">
                  {sym}/USDT &nbsp;<span style="color:#3a5a6a;font-family:Space Mono,monospace;font-size:0.65rem;font-weight:400">· {etype}</span>
                </div>
                <div style="font-family:'Space Mono',monospace;font-size:0.62rem;color:#3a5a6a;margin-top:3px">
                  Цена: {ps} &nbsp;·&nbsp; {ts_s} UTC
                </div>
              </div>
              <div style="font-family:'Space Mono',monospace;font-size:0.85rem;color:#ffcc00;font-weight:700">{volm:.1f}× объём</div>
            </div>
            """, unsafe_allow_html=True)

        if "symbol" in whales_raw.columns:
            st.markdown('<div class="sec-title">Самые активные пары</div>', unsafe_allow_html=True)
            ws = whales_raw["symbol"].value_counts().head(15)
            fig_ws = go.Figure(go.Bar(
                x=ws.index.str.replace("USDT",""), y=ws.values,
                marker_color="#ffcc00", marker_line_width=0,
            ))
            fig_ws.update_layout(height=200, margin=dict(l=0,r=0,t=5,b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=False, color="#4a6a7a"),
                yaxis=dict(showgrid=True, gridcolor="#0d1520", color="#4a6a7a"),
                font=dict(family="Space Mono", size=10))
            st.plotly_chart(fig_ws, use_container_width=True, config={"displayModeBar":False})


# ══════════════════════════════════════════════════════
#  ТАБ 4 — МОДЕЛЬ
# ══════════════════════════════════════════════════════
with tab4:
    cl, cr = st.columns([1, 2])

    with cl:
        st.markdown('<div class="sec-title">Статус AI</div>', unsafe_allow_html=True)
        if os.path.exists(cfg.MODEL_PATH) and meta:
            st.markdown(f"""
            <div style="background:#0a1018;border:1px solid #1a3a2a;border-radius:12px;padding:22px">
              <div style="font-size:2.2rem;margin-bottom:10px">✅</div>
              <div style="font-family:'Syne',sans-serif;font-weight:800;color:#00ff64;font-size:1.1rem">Модель готова к работе</div>
              <div style="font-family:'Space Mono',monospace;font-size:0.63rem;color:#3a5a6a;margin-top:14px;line-height:2.2">
                F1 Score: <span style="color:#00aaff">{meta.get('best_f1',0):.4f}</span><br>
                Признаков: <span style="color:#fff">{meta.get('n_features',0)}</span><br>
                Данные: <span style="color:#fff">{meta.get('trained_on','—')}</span>
              </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#0a1018;border:1px solid #2a1a1a;border-radius:12px;padding:22px">
              <div style="font-size:2.2rem;margin-bottom:10px">⚠️</div>
              <div style="font-family:'Syne',sans-serif;font-weight:800;color:#ff4466;font-size:1rem">Модель не обучена</div>
              <div style="font-family:'Space Mono',monospace;font-size:0.63rem;color:#3a5a6a;margin-top:14px;line-height:2.2">
                Открой терминал и запусти:<br>
                <code style="color:#00ff64;font-size:0.75rem">py -3.12 pretrain.py</code><br><br>
                Займёт ~1-2 минуты
              </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown('<div class="sec-title" style="margin-top:20px">Как бот принимает решения</div>', unsafe_allow_html=True)
        st.markdown("""
        <div style="background:#0a1018;border:1px solid #1a2a3a;border-radius:12px;padding:18px;
                    font-family:'Space Mono',monospace;font-size:0.63rem;color:#3a5a6a;line-height:2.3">
          <span style="color:#00ff64;font-size:0.75rem">1.</span> Каждую минуту скачивает свечи<br>
          <span style="color:#00ff64;font-size:0.75rem">2.</span> Считает 30+ индикаторов (RSI, MACD...)<br>
          <span style="color:#00ff64;font-size:0.75rem">3.</span> AI выдаёт три вероятности:<br>
          &nbsp;&nbsp;&nbsp; <span style="color:#fff">P(hold) / P(long) / P(short)</span><br>
          <span style="color:#00ff64;font-size:0.75rem">4.</span> P(long) > <span style="color:#fff">0.62</span> → открыть <span style="color:#00ff64">лонг</span><br>
          &nbsp;&nbsp;&nbsp; P(short) > <span style="color:#fff">0.62</span> → открыть <span style="color:#ff4466">шорт</span><br>
          <span style="color:#00ff64;font-size:0.75rem">5.</span> Каждые 500 баров — попытка переобучения<br>
          &nbsp;&nbsp;&nbsp; <span style="color:#ffcc00">Новая модель принимается ТОЛЬКО если</span><br>
          &nbsp;&nbsp;&nbsp; <span style="color:#ffcc00">точность выросла минимум на 0.2%</span>
        </div>
        """, unsafe_allow_html=True)

    with cr:
        st.markdown('<div class="sec-title">Какие признаки важны для AI</div>', unsafe_allow_html=True)
        if os.path.exists(cfg.MODEL_PATH):
            try:
                from signal_engine import ModelManager
                mgr = ModelManager()
                fi  = mgr.feature_importance()
                if not fi.empty:
                    fi20 = fi.head(20)
                    fig_fi = go.Figure(go.Bar(
                        x=fi20["importance"], y=fi20["feature"], orientation="h",
                        marker=dict(
                            color=fi20["importance"],
                            colorscale=[[0,"#0a2a1a"],[0.5,"#006633"],[1,"#00ff64"]],
                            line_width=0,
                        ),
                    ))
                    fig_fi.update_layout(height=480, margin=dict(l=0,r=0,t=5,b=0),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(showgrid=True, gridcolor="#0d1520", color="#2a4a5a"),
                        yaxis=dict(showgrid=False, color="#c0d0e0", tickfont=dict(size=10)),
                        font=dict(family="Space Mono", size=10))
                    st.plotly_chart(fig_fi, use_container_width=True, config={"displayModeBar":False})
            except Exception as e:
                st.warning(f"Ошибка загрузки: {e}")
        else:
            st.markdown('<div style="text-align:center;padding:80px;color:#2a4a5a;font-family:Space Mono,monospace;font-size:0.72rem">Сначала запусти pretrain.py</div>', unsafe_allow_html=True)

        st.markdown('<div class="sec-title">Расшифровка признаков</div>', unsafe_allow_html=True)
        st.markdown("""
        <div style="background:#0a1018;border:1px solid #1a2a3a;border-radius:12px;padding:16px 20px;
                    display:grid;grid-template-columns:1fr 1fr;gap:6px;
                    font-family:'Space Mono',monospace;font-size:0.62rem;color:#3a5a6a;line-height:2">
          <div><span style="color:#00ff64">rsi</span> — перекупленность / перепроданность</div>
          <div><span style="color:#00ff64">macd_hist</span> — сила и направление тренда</div>
          <div><span style="color:#00ff64">bb_pos</span> — место в полосах Боллинджера</div>
          <div><span style="color:#00ff64">atr_pct</span> — текущая волатильность рынка</div>
          <div><span style="color:#00ff64">cmf</span> — Chaikin: куда идут деньги</div>
          <div><span style="color:#00ff64">vwap_dist</span> — далеко ли от справедливой цены</div>
          <div><span style="color:#00ff64">vol_zscore</span> — аномально высокий объём</div>
          <div><span style="color:#00ff64">adx</span> — насколько силён тренд сейчас</div>
          <div><span style="color:#00ff64">stoch_k/d</span> — стохастик (моментум)</div>
          <div><span style="color:#00ff64">ob_imbalance</span> — давление покупок/продаж</div>
        </div>
        """, unsafe_allow_html=True)

# ── Футер ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:32px;padding:16px 0;border-top:1px solid #0d1520;text-align:center;
            font-family:'Space Mono',monospace;font-size:0.58rem;color:#1a2a3a;letter-spacing:0.1em">
  CRYPTOBOT SCANNER · BYBIT TESTNET · ТОЛЬКО ДЛЯ ОБУЧЕНИЯ · НЕ ЯВЛЯЕТСЯ ФИНАНСОВЫМ СОВЕТОМ
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  ТАБ 5 — МУЛЬТИТАЙМФРЕЙМ
# ══════════════════════════════════════════════════════
with tab5:
    st.markdown('<div class="sec-title">Анализ по таймфреймам (5m / 15m / 1h / 4h)</div>', unsafe_allow_html=True)

    sel_sym = st.selectbox(
        "Выбери монету",
        options=cfg.SYMBOLS,
        format_func=lambda s: s.replace("USDT", "/USDT"),
        key="mtf_sym",
    )

    if st.button("🔍 Анализировать", key="mtf_btn"):
        with st.spinner(f"Загружаем данные для {sel_sym}..."):
            try:
                from multi_model_manager import MultiModelManager as _MMM
                from mtf_analyzer import analyze_symbol_mtf
                _mgr = _MMM()
                mtf_res = analyze_symbol_mtf(sel_sym, _mgr)

                if not mtf_res.signals:
                    st.warning("Нет данных или модель не обучена для этой пары.")
                else:
                    # Summary banner
                    dir_map = {0: "HOLD ⬜", 1: "▲ LONG 🟢", 2: "▼ SHORT 🔴"}
                    best_label = dir_map.get(mtf_res.best_signal, "—")
                    conf_color = "#00ff64" if mtf_res.best_signal == 1 else ("#ff4466" if mtf_res.best_signal == 2 else "#3a5a6a")
                    st.markdown(f"""
                    <div style="background:#0d1520;border:1px solid {conf_color}33;border-radius:12px;
                                padding:18px 24px;margin-bottom:20px;display:flex;gap:32px;align-items:center">
                      <div>
                        <div style="font-family:'Space Mono',monospace;font-size:0.6rem;color:#3a5a6a;text-transform:uppercase;letter-spacing:0.1em">Лучший ТФ</div>
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:{conf_color}">{mtf_res.best_tf or '—'}</div>
                      </div>
                      <div>
                        <div style="font-family:'Space Mono',monospace;font-size:0.6rem;color:#3a5a6a;text-transform:uppercase;letter-spacing:0.1em">Сигнал</div>
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:{conf_color}">{best_label}</div>
                      </div>
                      <div>
                        <div style="font-family:'Space Mono',monospace;font-size:0.6rem;color:#3a5a6a;text-transform:uppercase;letter-spacing:0.1em">Уверенность</div>
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:#fff">{mtf_res.best_prob:.0%}</div>
                      </div>
                      <div>
                        <div style="font-family:'Space Mono',monospace;font-size:0.6rem;color:#3a5a6a;text-transform:uppercase;letter-spacing:0.1em">Совпадений ТФ</div>
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:#ffcc00">{mtf_res.confluence}/{len(mtf_res.signals)}</div>
                      </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Per-TF cards
                    cols = st.columns(len(mtf_res.signals))
                    for col, sig in zip(cols, mtf_res.signals):
                        sc = "#00ff64" if sig.signal == 1 else ("#ff4466" if sig.signal == 2 else "#3a5a6a")
                        sl = {0: "HOLD", 1: "LONG ▲", 2: "SHORT ▼"}.get(sig.signal, "—")
                        ps = f"${sig.price:,.0f}" if sig.price >= 1000 else (f"${sig.price:.4f}" if sig.price < 1 else f"${sig.price:.2f}")
                        tp_s = f"${sig.tp:,.0f}" if sig.tp >= 1000 else (f"${sig.tp:.4f}" if sig.tp < 1 else f"${sig.tp:.2f}")
                        sl_s = f"${sig.sl:,.0f}" if sig.sl >= 1000 else (f"${sig.sl:.4f}" if sig.sl < 1 else f"${sig.sl:.2f}")
                        with col:
                            st.markdown(f"""
                            <div style="background:#0a1018;border:1px solid {sc}44;border-radius:12px;padding:16px;text-align:center">
                              <div style="font-family:'Syne',sans-serif;font-size:1.1rem;font-weight:800;color:#fff">{sig.label}</div>
                              <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:{sc};margin:8px 0">{sl}</div>
                              <div style="font-family:'Space Mono',monospace;font-size:0.6rem;color:#3a5a6a;line-height:2.2">
                                Цена: <span style="color:#c0d0e0">{ps}</span><br>
                                P(long): <span style="color:#00ff64">{sig.p_long:.2f}</span><br>
                                P(short): <span style="color:#ff4466">{sig.p_short:.2f}</span><br>
                                <span style="color:#00ff64">TP: {tp_s} (+{sig.tp_pct:.2f}%)</span><br>
                                <span style="color:#ff4466">SL: {sl_s} (-{sig.sl_pct:.2f}%)</span><br>
                                R/R: <span style="color:#ffcc00">{sig.rr:.2f}x</span>
                              </div>
                            </div>
                            """, unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Ошибка: {e}")
    else:
        st.markdown("""
        <div style="text-align:center;padding:60px;color:#2a4a5a;font-family:Space Mono,monospace;font-size:0.72rem">
          Выбери монету и нажми «Анализировать»<br><br>
          Бот загрузит данные по 4 таймфреймам и покажет<br>
          сигнал, TP и SL для каждого
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  ТАБ 6 — БЭКТЕСТ
# ══════════════════════════════════════════════════════
with tab6:
    st.markdown('<div class="sec-title">Бэктест на реальных исторических данных</div>', unsafe_allow_html=True)

    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        bt_syms = st.multiselect(
            "Пары для бэктеста",
            options=cfg.SYMBOLS,
            default=cfg.SYMBOLS[:5],
            format_func=lambda s: s.replace("USDT", "/USDT"),
            key="bt_syms",
        )
    with bc2:
        bt_bars = st.slider("Баров истории", min_value=200, max_value=1000, value=500, step=100, key="bt_bars")
    with bc3:
        bt_equity = st.number_input("Начальный капитал $", min_value=100, max_value=100000, value=1000, step=100, key="bt_eq")

    if st.button("▶ Запустить бэктест", key="bt_run") and bt_syms:
        with st.spinner("Загружаем данные и считаем..."):
            try:
                import config as _cfg
                _cfg.BACKTEST_INITIAL_EQUITY = float(bt_equity)
                from backtester import run_backtest, BacktestResult

                bt_results: dict[str, BacktestResult] = run_backtest(symbols=bt_syms, limit=bt_bars)

                if not bt_results:
                    st.warning("Нет результатов. Проверь что модели обучены.")
                else:
                    # Summary KPIs
                    all_trades = [t for r in bt_results.values() for t in r.trades]
                    total_pnl_bt = sum(t.pnl for t in all_trades)
                    wr_bt = sum(1 for t in all_trades if t.pnl > 0) / max(len(all_trades), 1)
                    best_sym = max(bt_results, key=lambda s: bt_results[s].total_pnl)
                    worst_sym = min(bt_results, key=lambda s: bt_results[s].total_pnl)
                    pnl_c_bt = "green" if total_pnl_bt >= 0 else "red"

                    st.markdown(f"""
                    <div class="kpi-row">
                      <div class="kpi-card {pnl_c_bt}">
                        <div class="kpi-label">💰 Итого PnL</div>
                        <div class="kpi-value {pnl_c_bt}">{('+' if total_pnl_bt>=0 else '')}{total_pnl_bt:.2f}$</div>
                        <div class="kpi-sub">{len(all_trades)} сделок</div>
                      </div>
                      <div class="kpi-card yellow">
                        <div class="kpi-label">🎯 Win Rate</div>
                        <div class="kpi-value yellow">{wr_bt:.0%}</div>
                        <div class="kpi-sub">прибыльных сделок</div>
                      </div>
                      <div class="kpi-card green">
                        <div class="kpi-label">🏆 Лучшая пара</div>
                        <div class="kpi-value green" style="font-size:1.2rem">{best_sym.replace('USDT','')}</div>
                        <div class="kpi-sub">+{bt_results[best_sym].total_pnl:.2f}$</div>
                      </div>
                      <div class="kpi-card red">
                        <div class="kpi-label">📉 Худшая пара</div>
                        <div class="kpi-value red" style="font-size:1.2rem">{worst_sym.replace('USDT','')}</div>
                        <div class="kpi-sub">{bt_results[worst_sym].total_pnl:.2f}$</div>
                      </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Equity curves
                    st.markdown('<div class="sec-title">Кривые капитала по парам</div>', unsafe_allow_html=True)
                    fig_bt = go.Figure()
                    colors = ["#00ff64","#00aaff","#ffcc00","#ff4466","#aa44ff",
                              "#ff8800","#00ffcc","#ff44aa","#44ffaa","#ffaa00"]
                    for idx, (sym, res) in enumerate(bt_results.items()):
                        if res.equity_curve:
                            fig_bt.add_trace(go.Scatter(
                                y=res.equity_curve,
                                name=sym.replace("USDT",""),
                                line=dict(color=colors[idx % len(colors)], width=1.5),
                            ))
                    fig_bt.add_hline(y=float(bt_equity), line_dash="dash", line_color="#1a3a2a", line_width=1)
                    fig_bt.update_layout(
                        height=320, margin=dict(l=0,r=0,t=10,b=0),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        legend=dict(font=dict(color="#4a6a7a",size=9), bgcolor="rgba(0,0,0,0)"),
                        xaxis=dict(showgrid=False, color="#2a4a5a", title="Сделки"),
                        yaxis=dict(showgrid=True, gridcolor="#0d1520", color="#2a4a5a", title="Капитал $"),
                        font=dict(family="Space Mono", size=10),
                    )
                    st.plotly_chart(fig_bt, use_container_width=True, config={"displayModeBar": False})

                    # Results table
                    st.markdown('<div class="sec-title">Детальная статистика</div>', unsafe_allow_html=True)
                    rows_bt = []
                    for sym, res in bt_results.items():
                        rows_bt.append({
                            "Пара": sym.replace("USDT","/USDT"),
                            "Сделок": res.n_trades,
                            "Win%": f"{res.win_rate:.0%}",
                            "PnL $": f"{res.total_pnl:+.2f}",
                            "Max DD%": f"{res.max_drawdown*100:.1f}%",
                            "Sharpe": f"{res.sharpe:.2f}",
                            "Profit Factor": f"{res.profit_factor:.2f}",
                        })
                    st.dataframe(pd.DataFrame(rows_bt), use_container_width=True, hide_index=True)

            except Exception as e:
                st.error(f"Ошибка бэктеста: {e}")
    else:
        st.markdown("""
        <div style="text-align:center;padding:60px;color:#2a4a5a;font-family:Space Mono,monospace;font-size:0.72rem">
          Выбери пары, количество баров и нажми «Запустить бэктест»<br><br>
          Бот загрузит реальные данные с Bybit и прогонит<br>
          через обученные модели — покажет PnL, Win Rate, Sharpe
        </div>
        """, unsafe_allow_html=True)


# ── Футер ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:32px;padding:16px 0;border-top:1px solid #0d1520;text-align:center;
            font-family:'Space Mono',monospace;font-size:0.58rem;color:#1a2a3a;letter-spacing:0.1em">
  CRYPTOBOT SCANNER v2 · BYBIT TESTNET · ТОЛЬКО ДЛЯ ОБУЧЕНИЯ · НЕ ЯВЛЯЕТСЯ ФИНАНСОВЫМ СОВЕТОМ
</div>
""", unsafe_allow_html=True)
