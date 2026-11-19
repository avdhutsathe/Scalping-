import streamlit as st
import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import ta
import time
from datetime import datetime

# पेज सेटअप
st.set_page_config(page_title="9/21 EMA Scalping Algo - Zerodha", layout="wide")
st.title("⚡ 9 & 21 EMA Multi-Index Scalping Algo (NIFTY | BANKNIFTY | SENSEX)")

# सेशन स्टेट इनिशियलायझेशन
if "kite" not in st.session_state:
    st.session_state.kite = None
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "active_trade" not in st.session_state:
    st.session_state.active_trade = None
if "trade_logs" not in st.session_state:
    st.session_state.trade_logs = []

# --- SIDEBAR: ZERODHA LOGIN & SETTINGS ---
st.sidebar.header("🔐 1. Zerodha Kite API Login")
api_key = st.sidebar.text_input("API Key", type="password")
api_secret = st.sidebar.text_input("API Secret", type="password")

if api_key:
    kite_temp = KiteConnect(api_key=api_key)
    login_url = kite_temp.login_url()
    st.sidebar.markdown(f"[👉 येथे क्लिक करून Zerodha Login करा]({login_url})")

request_token = st.sidebar.text_input("URL मधील Request Token पेस्ट करा")

if st.sidebar.button("Connect to Zerodha"):
    try:
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        st.session_state.kite = kite
        st.session_state.access_token = data["access_token"]
        kite.set_access_token(data["access_token"])
        st.sidebar.success("✅ Zerodha यशस्वीरित्या कनेक्ट झाले!")
    except Exception as e:
        st.sidebar.error(f"❌ लॉगिन त्रुटी: {e}")

st.sidebar.divider()
st.sidebar.header("⚙️ 2. Trading Mode & Risk")
trading_mode = st.sidebar.radio("Trading Mode निवडा:", ["🟡 PAPER TRADING", "🔴 LIVE TRADING"])
lots = st.sidebar.number_input("Lots संख्या (प्रत्येक ट्रेडसाठी)", min_value=1, max_value=50, value=1)
timeframe = st.sidebar.selectbox("Candle Timeframe", ["minute", "3minute"], index=0)
strike_preference = st.sidebar.selectbox("Strike Selection", ["1-Step ITM (Best for Scalping)", "ATM (At-The-Money)"])

# --- MARKET DATA & STRATEGY ENGINE ---
def get_market_regime(df):
    """OI आणि Price वरून Short Covering, Long Unwinding किंवा Strong Trend ओळखणे"""
    price_change = df['close'].iloc[-1] - df['close'].iloc[-2]
    oi_change = df['oi'].iloc[-1] - df['oi'].iloc[-2]

    if price_change > 0 and oi_change < 0:
        return "SHORT_COVERING", 5.0  # टार्गेट 1:5
    elif price_change < 0 and oi_change < 0:
        return "LONG_UNWINDING", 1.5  # छोटे व जलद टार्गेट 1:1.5
    elif df['adx'].iloc[-1] > 30:
        return "STRONG_TREND", 2.5    # मजबूत ट्रेंड 1:2.5
    else:
        return "NORMAL_TREND", 1.5    # सामान्य स्कॅल्पिंग 1:1.5

def analyze_index(df, index_name):
    """9/21 EMA + ADX + Volume 20 MA + OI तपासणी"""
    df['ema9'] = ta.trend.ema_indicator(df['close'], window=9)
    df['ema21'] = ta.trend.ema_indicator(df['close'], window=21)
    df['adx'] = ta.trend.adx(df['high'], df['low'], df['close'], window=14)
    df['vol_ma20'] = df['volume'].rolling(window=20).mean()

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Sideways Filter (ADX 20 पेक्षा कमी असल्यास ट्रेड टाळा)
    if curr['adx'] < 20:
        return None

    # Volume Confirmation (Volume 20 MA पेक्षा जास्त हवा)
    if curr['volume'] <= curr['vol_ma20']:
        return None

    regime, rr_ratio = get_market_regime(df)
    signal_strength = curr['adx'] + (curr['volume'] / curr['vol_ma20']) * 10

    # Bullish Pullback Setup (CALL BUY)
    if curr['ema9'] > curr['ema21']:
        touched_ema = (prev['low'] <= prev['ema9']) or (curr['low'] <= curr['ema9'])
        bullish_candle = curr['close'] > curr['open'] and curr['close'] > curr['ema9']
        if touched_ema and bullish_candle:
            sl_points = max(curr['close'] - curr['low'], 10)
            return {
                "index": index_name,
                "direction": "CE (BUY)",
                "price": curr['close'],
                "sl_points": sl_points,
                "rr_ratio": rr_ratio,
                "regime": regime,
                "strength": signal_strength,
                "adx": round(curr['adx'], 2)
            }

    # Bearish Pullback Setup (PUT BUY)
    elif curr['ema9'] < curr['ema21']:
        touched_ema = (prev['high'] >= prev['ema9']) or (curr['high'] >= prev['ema9'])
        bearish_candle = curr['close'] < curr['open'] and curr['close'] < curr['ema9']
        if touched_ema and bearish_candle:
            sl_points = max(curr['high'] - curr['close'], 10)
            return {
                "index": index_name,
                "direction": "PE (BUY)",
                "price": curr['close'],
                "sl_points": sl_points,
                "rr_ratio": rr_ratio,
                "regime": regime,
                "strength": signal_strength,
                "adx": round(curr['adx'], 2)
            }

    return None

def get_option_symbol(instruments_df, index_name, spot_price, option_type, pref):
    """ATM किंवा 1-Step ITM स्ट्राइक निवडणे"""
    step = 50 if index_name == "NIFTY" else 100
    atm_strike = round(spot_price / step) * step

    if pref == "1-Step ITM (Best for Scalping)":
        target_strike = atm_strike - step if option_type == "CE" else atm_strike + step
    else:
        target_strike = atm_strike

    opts = instruments_df[
        (instruments_df['name'] == index_name) &
        (instruments_df['strike'] == target_strike) &
        (instruments_df['instrument_type'] == option_type)
    ].sort_values('expiry')

    first_opt = opts.iloc[0]
    return first_opt['tradingsymbol'], int(first_opt['lot_size']), first_opt['exchange']

# --- MAIN DASHBOARD UI ---
col1, col2, col3 = st.columns(3)
col1.metric("Current Mode", trading_mode)
col2.metric("Active Trade", st.session_state.active_trade['symbol'] if st.session_state.active_trade else "कोणताही ट्रेड नाही")
col3.metric("Total Trades Today", len(st.session_state.trade_logs))

run_algo = st.toggle("🚀 Start / Stop Auto-Scalping Engine")
status_placeholder = st.empty()

if run_algo:
    if not st.session_state.kite:
        st.error("⚠️ कृपया आधी डाव्या बाजूला Zerodha Kite Login करा!")
    else:
        kite = st.session_state.kite
        try:
            nfo_df = pd.DataFrame(kite.instruments("NFO"))
            bfo_df = pd.DataFrame(kite.instruments("BFO"))
            all_inst = pd.concat([nfo_df, bfo_df], ignore_index=True)

            # चालू महिन्याचे Futures Tokens मिळवणे
            indices = ["NIFTY", "BANKNIFTY", "SENSEX"]
            fut_tokens = {}
            for idx in indices:
                futs = all_inst[(all_inst['name'] == idx) & (all_inst['instrument_type'] == 'FUT')].sort_values('expiry')
                if not futs.empty:
                    fut_tokens[idx] = int(futs.iloc[0]['instrument_token'])

            while run_algo:
                now = datetime.now().strftime("%H:%M:%S")
                signals = []

                # १. तिन्ही इंडेक्स स्कॅन करणे
                to_date = datetime.now()
                from_date = to_date - pd.Timedelta(days=2)

                for idx, token in fut_tokens.items():
                    hist = kite.historical_data(token, from_date, to_date, timeframe, oi=True)
                    df_hist = pd.DataFrame(hist)
                    if len(df_hist) > 25:
                        sig = analyze_index(df_hist, idx)
                        if sig:
                            signals.append(sig)

                # २. जर कोणताही ट्रेड चालू नसेल, तर सर्वात मजबूत सिग्नलमध्ये एन्ट्री घेणे
                if st.session_state.active_trade is None and signals:
                    best_signal = max(signals, key=lambda x: x['strength'])
                    opt_type = "CE" if "CE" in best_signal['direction'] else "PE"
                    symbol, lot_size, exch = get_option_symbol(
                        all_inst, best_signal['index'], best_signal['price'], opt_type, strike_preference
                    )
                    qty = lot_size * lots
                    ltp_data = kite.ltp(f"{exch}:{symbol}")
                    entry_price = ltp_data[f"{exch}:{symbol}"]['last_price']

                    # Option प्रीमियमसाठी Delta नुसार SL आणि Target ठरवणे (Delta ~ 0.55)
                    opt_sl_pts = round(best_signal['sl_points'] * 0.55, 1)
                    opt_sl_pts = max(opt_sl_pts, 5.0)
                    sl_price = round(entry_price - opt_sl_pts, 1)
                    target_price = round(entry_price + (opt_sl_pts * best_signal['rr_ratio']), 1)

                    if trading_mode == "🔴 LIVE TRADING":
                        kite.place_order(
                            variety=kite.VARIETY_REGULAR,
                            exchange=exch,
                            tradingsymbol=symbol,
                            transaction_type=kite.TRANSACTION_TYPE_BUY,
                            quantity=qty,
                            product=kite.PRODUCT_MIS,
                            order_type=kite.ORDER_TYPE_MARKET
                        )

                    st.session_state.active_trade = {
                        "symbol": symbol,
                        "exchange": exch,
                        "qty": qty,
                        "entry": entry_price,
                        "sl": sl_price,
                        "initial_sl_pts": opt_sl_pts,
                        "target": target_price,
                        "regime": best_signal['regime'],
                        "highest_price": entry_price,
                        "time": now
                    }
                    status_placeholder.success(
                        f"🔥 NEW ENTRY ({best_signal['regime']}): {symbol} @ ₹{entry_price} | Target: ₹{target_price} | SL: ₹{sl_price}"
                    )

                # ३. चालू ट्रेडचे Trailing Stop Loss आणि Target मॅनेज करणे
                elif st.session_state.active_trade is not None:
      
