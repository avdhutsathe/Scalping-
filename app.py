import streamlit as st
import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import ta
import time
from datetime import datetime

st.set_page_config(page_title="9/21 EMA Scalping Algo - Zerodha", layout="wide")
st.title("⚡ 9 & 21 EMA Multi-Index Scalping Algo")

if "kite" not in st.session_state:
    st.session_state.kite = None
if "active_trade" not in st.session_state:
    st.session_state.active_trade = None
if "trade_logs" not in st.session_state:
    st.session_state.trade_logs = []

# --- SIDEBAR ---
st.sidebar.header("🔐 1. Zerodha Kite Login")
api_key = st.sidebar.text_input("API Key", type="password")
api_secret = st.sidebar.text_input("API Secret", type="password")

if api_key:
    kite_temp = KiteConnect(api_key=api_key)
    st.sidebar.markdown(f"[👉 येथे क्लिक करून Zerodha Login करा]({kite_temp.login_url()})")

request_token = st.sidebar.text_input("Request Token पेस्ट करा")

if st.sidebar.button("Connect to Zerodha"):
    try:
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        kite.set_access_token(data["access_token"])
        st.session_state.kite = kite
        st.sidebar.success("✅ Zerodha यशस्वीरित्या कनेक्ट झाले!")
    except Exception as e:
        st.sidebar.error(f"❌ लॉगिन त्रुटी: {e}")

st.sidebar.divider()
st.sidebar.header("⚙️ 2. Settings")
trading_mode = st.sidebar.radio("Mode निवडा:", ["🟡 PAPER TRADING", "🔴 LIVE TRADING"])
lots = st.sidebar.number_input("Lots संख्या", min_value=1, max_value=50, value=1)
timeframe = st.sidebar.selectbox("Timeframe", ["minute", "3minute"], index=0)
strike_pref = st.sidebar.selectbox("Strike", ["1-Step ITM", "ATM"])

# --- STRATEGY FUNCTIONS ---
def get_market_regime(df):
    price_chg = df['close'].iloc[-1] - df['close'].iloc[-2]
    oi_chg = df['oi'].iloc[-1] - df['oi'].iloc[-2]
    if price_chg > 0 and oi_chg < 0:
        return "SHORT_COVERING", 5.0
    elif price_chg < 0 and oi_chg < 0:
        return "LONG_UNWINDING", 1.5
    elif df['adx'].iloc[-1] > 30:
        return "STRONG_TREND", 2.5
    return "NORMAL_TREND", 1.5

def analyze_index(df, index_name):
    df['ema9'] = ta.trend.ema_indicator(df['close'], window=9)
    df['ema21'] = ta.trend.ema_indicator(df['close'], window=21)
    df['adx'] = ta.trend.adx(df['high'], df['low'], df['close'], window=14)
    df['vol_ma20'] = df['volume'].rolling(window=20).mean()

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    if curr['adx'] < 20 or curr['volume'] <= curr['vol_ma20']:
        return None

    regime, rr_ratio = get_market_regime(df)
    strength = curr['adx'] + (curr['volume'] / curr['vol_ma20']) * 10

    if curr['ema9'] > curr['ema21']:
        if (prev['low'] <= prev['ema9'] or curr['low'] <= curr['ema9']) and curr['close'] > curr['open'] and curr['close'] > curr['ema9']:
            sl_pts = max(curr['close'] - curr['low'], 10)
            return {"index": index_name, "dir": "CE", "price": curr['close'], "sl_pts": sl_pts, "rr": rr_ratio, "regime": regime, "strength": strength}

    if curr['ema9'] < curr['ema21']:
        if (prev['high'] >= prev['ema9'] or curr['high'] >= curr['ema9']) and curr['close'] < curr['open'] and curr['close'] < curr['ema9']:
            sl_pts = max(curr['high'] - curr['close'], 10)
            return {"index": index_name, "dir": "PE", "price": curr['close'], "sl_pts": sl_pts, "rr": rr_ratio, "regime": regime, "strength": strength}

    return None

def get_option_symbol(all_inst, index_name, spot_price, opt_type, pref):
    step = 50 if index_name == "NIFTY" else 100
    atm = round(spot_price / step) * step
    target_strike = (atm - step if opt_type == "CE" else atm + step) if pref == "1-Step ITM" else atm
    opts = all_inst[(all_inst['name'] == index_name) & (all_inst['strike'] == target_strike) & (all_inst['instrument_type'] == opt_type)].sort_values('expiry')
    row = opts.iloc
    return row['tradingsymbol'], int(row['lot_size']), row['exchange']

# --- DASHBOARD ---
c1, c2, c3 = st.columns(3)
c1.metric("Mode", trading_mode)
c2.metric("Active Trade", st.session_state.active_trade['symbol'] if st.session_state.active_trade else "None")
c3.metric("Total Trades", len(st.session_state.trade_logs))

run_algo = st.toggle("🚀 Start / Stop Algo Engine")
status_box = st.empty()

if run_algo and not st.session_state.kite:
    st.error("⚠️ कृपया आधी डाव्या बाजूला Zerodha Login करा!")

if run_algo and st.session_state.kite:
    kite = st.session_state.kite
    nfo = pd.DataFrame(kite.instruments("NFO"))
    bfo = pd.DataFrame(kite.instruments("BFO"))
    all_inst = pd.concat([nfo, bfo], ignore_index=True)

    fut_tokens = {}
    for idx in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        futs = all_inst[(all_inst['name'] == idx) & (all_inst['instrument_type'] == 'FUT')].sort_values('expiry')
        if not futs.empty:
            fut_tokens[idx] = int(futs.iloc['instrument_token'])

    while run_algo:
        now = datetime.now().strftime("%H:%M:%S")
        signals = []
        to_dt = datetime.now()
        from_dt = to_dt - pd.Timedelta(days=2)

        for idx, token in fut_tokens.items():
            hist = kite.historical_data(token, from_dt, to_dt, timeframe, oi=True)
            df_h = pd.DataFrame(hist)
            if len(df_h) > 25:
                sig = analyze_index(df_h, idx)
                if sig:
                    signals.append(sig)

        if st.session_state.active_trade is None and signals:
            best = max(signals, key=lambda x: x['strength'])
            sym, lot_sz, exch = get_option_symbol(all_inst, best['index'], best['price'], best['dir'], strike_pref)
            qty = lot_sz * lots
            entry_p = kite.ltp(f"{exch}:{sym}")[f"{exch}:{sym}"]['last_price']
            sl_pts = max(round(best['sl_pts'] * 0.55, 1), 5.0)
            sl_p = round(entry_p - sl_pts, 1)
            tgt_p = round(entry_p + (sl_pts * best['rr']), 1)

            if trading_mode == "🔴 LIVE TRADING":
                kite.place_order(variety=kite.VARIETY_REGULAR, exchange=exch, tradingsymbol=sym, transaction_type=kite.TRANSACTION_TYPE_BUY, quantity=qty, product=kite.PRODUCT_MIS, order_type=kite.ORDER_TYPE_MARKET)

            st.session_state.active_trade = {"symbol": sym, "exchange": exch, "qty": qty, "entry": entry_p, "sl": sl_p, "sl_pts": sl_pts, "target": tgt_p, "regime": best['regime'], "high": entry_p}
            status_box.success(f"🔥 ENTRY ({best['regime']}): {sym} @ ₹{entry_p} | Tgt: ₹{tgt_p} | SL: ₹{sl_p}")

        elif st.session_state.active_trade is not None:
            tr = st.session_state.active_trade
            ltp = kite.ltp(f"{tr['exchange']}:{tr['symbol']}")[f"{tr['exchange']}:{tr['symbol']}"]['last_price']

            if ltp > tr['high']:
                tr['high'] = ltp
                gap = tr['sl_pts'] * (0.6 if tr['regime'] in ["SHORT_COVERING", "STRONG_TREND"] else 0.8)
                new_sl = round(ltp - gap, 1)
                if new_sl > tr['sl']:
                    tr['sl'] = new_sl

            reason = "🎯 TARGET HIT" if ltp >= tr['target'] else ("🛑 TRAILING SL HIT" if ltp <= tr['sl'] else None)
            if reason:
                if trading_mode == "🔴 LIVE TRADING":
                    kite.place_order(variety=kite.VARIETY_REGULAR, exchange=tr['exchange'], tradingsymbol=tr['symbol'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=tr['qty'], product=kite.PRODUCT_MIS, order_type=kite.ORDER_TYPE_MARKET)
                pnl = round((ltp - tr['entry']) * tr['qty'], 2)
                st.session_state.trade_logs.append({"Time": now, "Symbol": tr['symbol'], "Regime": tr['regime'], "Entry": tr['entry'], "Exit": ltp, "PnL": pnl, "Reason": reason})
                st.session_state.active_trade = None
                status_box.warning(f"{reason}: {tr['symbol']} @ ₹{ltp} | PnL: ₹{pnl}")
            else:
                status_box.info(f"⏳ {tr['symbol']} | LTP: ₹{ltp} | Trail SL: ₹{tr['sl']} | Tgt: ₹{tr['target']} ({tr['regime']})")
        else:
            status_box.write(f"🔍 [{now}] NIFTY, BANKNIFTY, SENSEX स्कॅन चालू आहे...")

        time.sleep(2)

if st.session_state.trade_logs:
    st.subheader("📜 Trade Logs")
    st.dataframe(pd.DataFrame(st.session_state.trade_logs), use_container_width=True)
