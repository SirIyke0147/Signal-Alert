import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import requests
import pandas as pd
import time
from datetime import datetime
import pytz
import talib as ta

# ===== Configuration =====
TWELVEDATA_API_KEY = os.getenv('TWELVEDATA_API_KEY')
FOREX_PAIRS = ["EUR/USD", "USD/JPY", "GBP/USD", "AUD/USD", "USD/CAD",
               "USD/CHF", "NZD/USD", "EUR/JPY", "GBP/JPY", "EUR/GBP"]

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

if not all([TWELVEDATA_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    raise EnvironmentError("Missing required environment variables")

PRIMARY_TIMEFRAME = "4h"
CONFIRMATION_TIMEFRAME = "1h"
OUTPUT_SIZE = 200

# ===== Strategy Parameters =====
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STDDEV = 2
EMA_FAST = 50
EMA_SLOW = 200
ADX_PERIOD = 14
ADX_THRESHOLD = 20
ATR_PERIOD = 14

def get_pip_value(pair):
    """Return pip value for a pair (0.0001 or 0.01 for JPY pairs)"""
    return 0.01 if 'JPY' in pair else 0.0001

def get_tp_sl(entry, direction, pair):
    """
    Calculate strict TP1, TP2, SL in pips:
    TP1: 28 pips
    TP2: 35 pips
    SL: 25 pips
    """
    pip = get_pip_value(pair)
    if direction == 'BUY':
        tp1 = entry + (28 * pip)
        tp2 = entry + (35 * pip)
        sl = entry - (25 * pip)
    else:
        tp1 = entry - (28 * pip)
        tp2 = entry - (35 * pip)
        sl = entry + (25 * pip)
    return tp1, tp2, sl

def fetch_ohlc_data(pair, timeframe):
    url = f"https://api.twelvedata.com/time_series?symbol={pair}&interval={timeframe}&outputsize={OUTPUT_SIZE}&apikey={TWELVEDATA_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        time.sleep(10)  # Respect API rate limits

        if 'values' not in data:
            print(f"No data for {pair} ({timeframe})")
            return None

        df = pd.DataFrame(data['values'])
        df = df.iloc[::-1].reset_index(drop=True)

        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.dropna()
        return df
    except Exception as e:
        print(f"Error fetching data for {pair} ({timeframe}): {str(e)}")
        return None

def calculate_indicators(df):
    if df is None or len(df) < 50:
        return None
    try:
        df['ema50'] = ta.EMA(df['close'], timeperiod=EMA_FAST)
        df['ema200'] = ta.EMA(df['close'], timeperiod=EMA_SLOW)
        df['bb_upper'], df['bb_middle'], df['bb_lower'] = ta.BBANDS(
            df['close'], timeperiod=BB_PERIOD, nbdevup=BB_STDDEV, nbdevdn=BB_STDDEV)
        df['rsi'] = ta.RSI(df['close'], timeperiod=RSI_PERIOD)
        df['adx'] = ta.ADX(df['high'], df['low'], df['close'], timeperiod=ADX_PERIOD)
        df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=ATR_PERIOD)
        df = df.fillna(method='ffill').fillna(method='bfill')
        return df
    except Exception as e:
        print(f"Error calculating indicators: {str(e)}")
        return None

def calculate_confidence(signal_type, primary_last, conf_last=None):
    confidence = 0
    adx_score = min(30, max(0, (primary_last['adx'] - 20) * 1.5))
    confidence += adx_score
    if signal_type in ['TREND FOLLOWING', 'BREAKOUT']:
        if signal_type.startswith('BUY'):
            rsi_score = min(25, (40 - max(primary_last['rsi'], 30)) * 2.5)
        else:
            rsi_score = min(25, (min(primary_last['rsi'], 70) - 60) * 2.5)
    else:
        if signal_type == 'BUY':
            rsi_score = min(25, (35 - primary_last['rsi']) * 2.5)
        else:
            rsi_score = min(25, (primary_last['rsi'] - 65) * 2.5)
    confidence += rsi_score
    if primary_last['close'] > primary_last['ema50'] and primary_last['ema50'] > primary_last['ema200']:
        ema_score = 20
    elif primary_last['close'] < primary_last['ema50'] and primary_last['ema50'] < primary_last['ema200']:
        ema_score = 20
    else:
        ema_score = 10
    confidence += ema_score
    volatility_score = min(15, primary_last['atr'] * 100)
    if signal_type in ['TREND FOLLOWING', 'BREAKOUT']:
        confidence += volatility_score
    else:
        confidence += (15 - volatility_score)
    if conf_last:
        if signal_type in ['TREND FOLLOWING', 'BREAKOUT']:
            if (signal_type.startswith('BUY') and 
                conf_last['close'] > conf_last['ema50'] and 
                conf_last['rsi'] > 40):
                confidence += 10
            elif (signal_type.startswith('SELL') and 
                  conf_last['close'] < conf_last['ema50'] and 
                  conf_last['rsi'] < 60):
                confidence += 10
        else:
            if (signal_type == 'BUY' and conf_last['rsi'] > 30 and
                conf_last['close'] < conf_last['bb_lower'] * 1.005):
                confidence += 10
            elif (signal_type == 'SELL' and conf_last['rsi'] < 70 and
                  conf_last['close'] > conf_last['bb_upper'] * 0.995):
                confidence += 10
    return min(100, int(confidence))

def detect_trend_following_signal(primary_df, confirmation_df, pair):
    if primary_df is None or len(primary_df) < 3:
        return None
    try:
        last = primary_df.iloc[-1]
        prev = primary_df.iloc[-2]
        bullish_trend = (
            last['close'] > last['ema200'] and
            last['ema50'] > last['ema200'] and
            last['adx'] > 30
        )
        bearish_trend = (
            last['close'] < last['ema200'] and
            last['ema50'] < last['ema200'] and
            last['adx'] > 30
        )
        conf_last = confirmation_df.iloc[-1] if (confirmation_df is not None and len(confirmation_df) > 0) else None

        signal = None
        if bullish_trend:
            if last['close'] > last['ema50'] and prev['close'] < prev['ema50']:
                tp1, tp2, sl = get_tp_sl(last['close'], 'BUY', pair)
                signal = {
                    'type': 'TREND FOLLOWING',
                    'direction': 'BUY',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': [tp1, tp2],
                    'reason': "Strong uptrend pullback to EMA50"
                }
            elif last['close'] > last['bb_upper'] and conf_last is not None and conf_last['close'] > conf_last['bb_upper']:
                tp1, tp2, sl = get_tp_sl(last['close'], 'BUY', pair)
                signal = {
                    'type': 'BREAKOUT',
                    'direction': 'BUY',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': [tp1, tp2],
                    'reason': "Upper Bollinger breakout in strong trend"
                }
        elif bearish_trend:
            if last['close'] < last['ema50'] and prev['close'] > prev['ema50']:
                tp1, tp2, sl = get_tp_sl(last['close'], 'SELL', pair)
                signal = {
                    'type': 'TREND FOLLOWING',
                    'direction': 'SELL',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': [tp1, tp2],
                    'reason': "Strong downtrend pullback to EMA50"
                }
            elif last['close'] < last['bb_lower'] and conf_last is not None and conf_last['close'] < conf_last['bb_lower']:
                tp1, tp2, sl = get_tp_sl(last['close'], 'SELL', pair)
                signal = {
                    'type': 'BREAKDOWN',
                    'direction': 'SELL',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': [tp1, tp2],
                    'reason': "Lower Bollinger breakdown in strong trend"
                }
        if signal:
            signal['confidence'] = calculate_confidence(signal['type'], last, conf_last)
        return signal
    except Exception as e:
        print(f"Error in trend detection: {str(e)}")
        return None

def detect_reversal_signal(primary_df, confirmation_df, pair):
    if primary_df is None or len(primary_df) < 2:
        return None
    try:
        last = primary_df.iloc[-1]
        buy_condition = (
            last['close'] <= last['bb_lower'] and
            last['rsi'] < 35 and
            last['adx'] > ADX_THRESHOLD
        )
        sell_condition = (
            last['close'] >= last['bb_upper'] and
            last['rsi'] > 65 and
            last['adx'] > ADX_THRESHOLD
        )
        conf_last = confirmation_df.iloc[-1] if (confirmation_df is not None and len(confirmation_df) > 0) else None

        signal = None
        if buy_condition and conf_last is not None and conf_last['rsi'] > 30:
            tp1, tp2, sl = get_tp_sl(last['close'], 'BUY', pair)
            signal = {
                'type': 'REVERSAL',
                'direction': 'BUY',
                'entry': last['close'],
                'stop_loss': sl,
                'take_profit': [tp1, tp2],
                'reason': "Bollinger reversal with RSI confirmation"
            }
        elif sell_condition and conf_last is not None and conf_last['rsi'] < 70:
            tp1, tp2, sl = get_tp_sl(last['close'], 'SELL', pair)
            signal = {
                'type': 'REVERSAL',
                'direction': 'SELL',
                'entry': last['close'],
                'stop_loss': sl,
                'take_profit': [tp1, tp2],
                'reason': "Bollinger reversal with RSI confirmation"
            }
        if signal:
            signal['confidence'] = calculate_confidence(signal['direction'], last, conf_last)
        return signal
    except Exception as e:
        print(f"Error in reversal detection: {str(e)}")
        return None

def send_telegram_alert(signal):
    if not signal:
        return
    try:
        uk_time = datetime.now(pytz.timezone('Europe/London'))
        time_str = uk_time.strftime('%Y-%m-%d %H:%M')
        direction_symbol = "LONG ⬆️" if signal['direction'] == 'BUY' else "SHORT ⬇️"
        message = (
            f"⏰ {time_str} UK\n"
            f"*Forex signal *\n"
            f"📈 {signal['pair']} Signal - {direction_symbol} ⚡\n"
            f"Price Info:\n"
            f"• Current price : {signal['entry']:.5f}\n"
            f"• Stop Loss: {signal['stop_loss']:.5f}\n"
            f"• Volatility Ratio: {signal.get('volatility_ratio', 0.97):.2f}\n\n"
            f"🔰 Take Profit Targets:\n"
            f"• TP:1 {signal['take_profit'][0]:.5f} 🎯\n"
            f"• TP:2 {signal['take_profit'][1]:.5f} 🎯\n\n"
            f"📊 Confidence: {signal['confidence']}%"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        params = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.post(url, params=params)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram alert: {str(e)}")
        return False

def print_signal(signal):
    if not signal:
        return
    print("\n" + "="*70)
    print(f"🚨 FOREX SIGNAL DETECTED ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print("="*70)
    print(f"Pair: {signal['pair']}")
    print(f"Type: {signal['type']}")
    print(f"Direction: {signal['direction']}")
    print(f"Confidence: {signal['confidence']}%")
    print(f"Reason: {signal['reason']}")
    print(f"Entry Price: {signal['entry']:.5f}")
    print(f"Stop Loss: {signal['stop_loss']:.5f}")
    print("Take Profit Targets:")
    print(f"  TP1: {signal['take_profit'][0]:.5f}")
    print(f"  TP2: {signal['take_profit'][1]:.5f}")
    try:
        risk = abs(signal['entry'] - signal['stop_loss'])
        reward1 = abs(signal['take_profit'][0] - signal['entry'])
        reward2 = abs(signal['take_profit'][1] - signal['entry'])
        print("\nRisk-Reward Ratios:")
        print(f"  TP1: {reward1/risk:.2f}:1")
        print(f"  TP2: {reward2/risk:.2f}:1")
    except:
        print("\nRisk-Reward Ratios: Calculation error")
    print("="*70 + "\n")

# ===== Main Execution =====
print(f"Starting Enhanced Forex Scanner at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Primary TF: {PRIMARY_TIMEFRAME} | Confirmation TF: {CONFIRMATION_TIMEFRAME}")
print(f"Monitoring Pairs: {', '.join(FOREX_PAIRS)}")
print("Strategy: Trend Following + Reversals with Multi-TF confirmation")
print("="*70)

all_signals = []

for pair in FOREX_PAIRS:
    try:
        print(f"\nScanning {pair}...")
        primary_data = fetch_ohlc_data(pair, PRIMARY_TIMEFRAME)
        if primary_data is None or len(primary_data) == 0:
            print(f"  No primary data for {pair}")
            continue
        primary_df = calculate_indicators(primary_data)
        if primary_df is None or len(primary_df) == 0:
            print(f"  Failed to calculate indicators for {pair} (primary)")
            continue
        confirmation_data = fetch_ohlc_data(pair, CONFIRMATION_TIMEFRAME)
        confirmation_df = calculate_indicators(confirmation_data) if (confirmation_data is not None and len(confirmation_data) > 0) else None

        trend_signal = detect_trend_following_signal(primary_df, confirmation_df, pair)
        reversal_signal = detect_reversal_signal(primary_df, confirmation_df, pair)

        signal_found = False
        if trend_signal:
            trend_signal['pair'] = pair
            last = primary_df.iloc[-1]
            trend_signal['volatility_ratio'] = last['atr'] / last['close']
            all_signals.append(trend_signal)
            print(f"  Trend signal detected for {pair} (Confidence: {trend_signal['confidence']}%)")
            signal_found = True
        if reversal_signal:
            reversal_signal['pair'] = pair
            last = primary_df.iloc[-1]
            reversal_signal['volatility_ratio'] = last['atr'] / last['close']
            all_signals.append(reversal_signal)
            print(f"  Reversal signal detected for {pair} (Confidence: {reversal_signal['confidence']}%)")
            signal_found = True
        if not signal_found:
            print(f"  No signal for {pair}")
        try:
            last = primary_df.iloc[-1]
            trend_status = "Bullish" if last['close'] > last['ema200'] else "Bearish"
            print(f"  Trend Status: {trend_status}")
            print(f"  EMA50: {last['ema50']:.5f}, EMA200: {last['ema200']:.5f}")
            print(f"  ADX: {last['adx']:.1f} ({'Strong' if last['adx'] > 25 else 'Weak'} trend)")
        except Exception as e:
            print(f"  Error displaying trend status: {str(e)}")
    except Exception as e:
        print(f"Error processing {pair}: {str(e)}")

if all_signals:
    all_signals.sort(key=lambda x: x['confidence'], reverse=True)
    top_signals = all_signals[:3]
    print("\n" + "="*70)
    print(f"🔥 TOP {len(top_signals)} SIGNALS BY CONFIDENCE")
    print("="*70)
    for signal in top_signals:
        print_signal(signal)
        send_telegram_alert(signal)
else:
    print("\nNo signals detected in this scan")

print("\nScan completed. Waiting for next scheduled run...")
