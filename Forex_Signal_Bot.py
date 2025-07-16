import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import requests
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime
import pytz

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
    Calculate TP and SL in pips:
    TP: 28 pips
    SL: 20 pips
    """
    pip = get_pip_value(pair)
    if direction == 'BUY':
        tp = entry + (28 * pip)
        sl = entry - (20 * pip)
    else:
        tp = entry - (28 * pip)
        sl = entry + (20 * pip)
    return tp, sl

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
        # Calculate all indicators using pandas_ta
        df.ta.ema(length=EMA_FAST, append=True, col_names={'ema50'})
        df.ta.ema(length=EMA_SLOW, append=True, col_names={'ema200'})
        df.ta.bbands(length=BB_PERIOD, std=BB_STDDEV, append=True, 
                     col_names={'bb_lower', 'bb_middle', 'bb_upper'})
        df.ta.rsi(length=RSI_PERIOD, append=True, col_names={'rsi'})
        df.ta.adx(length=ADX_PERIOD, append=True, col_names={'adx'})
        df.ta.atr(length=ATR_PERIOD, append=True, col_names={'atr'})
        
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
                tp, sl = get_tp_sl(last['close'], 'BUY', pair)
                signal = {
                    'type': 'TREND FOLLOWING',
                    'direction': 'BUY',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': tp,
                    'reason': "Strong uptrend pullback to EMA50"
                }
            elif last['close'] > last['bb_upper'] and conf_last is not None and conf_last['close'] > conf_last['bb_upper']:
                tp, sl = get_tp_sl(last['close'], 'BUY', pair)
                signal = {
                    'type': 'BREAKOUT',
                    'direction': 'BUY',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': tp,
                    'reason': "Upper Bollinger breakout in strong trend"
                }
        elif bearish_trend:
            if last['close'] < last['ema50'] and prev['close'] > prev['ema50']:
                tp, sl = get_tp_sl(last['close'], 'SELL', pair)
                signal = {
                    'type': 'TREND FOLLOWING',
                    'direction': 'SELL',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': tp,
                    'reason': "Strong downtrend pullback to EMA50"
                }
            elif last['close'] < last['bb_lower'] and conf_last is not None and conf_last['close'] < conf_last['bb_lower']:
                tp, sl = get_tp_sl(last['close'], 'SELL', pair)
                signal = {
                    'type': 'BREAKDOWN',
                    'direction': 'SELL',
                    'entry': last['close'],
                    'stop_loss': sl,
                    'take_profit': tp,
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
            tp, sl = get_tp_sl(last['close'], 'BUY', pair)
            signal = {
                'type': 'REVERSAL',
                'direction': 'BUY',
                'entry': last['close'],
                'stop_loss': sl,
                'take_profit': tp,
                'reason': "Bollinger reversal with RSI confirmation"
            }
        elif sell_condition and conf_last is not None and conf_last['rsi'] < 70:
            tp, sl = get_tp_sl(last['close'], 'SELL', pair)
            signal = {
                'type': 'REVERSAL',
                'direction': 'SELL',
                'entry': last['close'],
                'stop_loss': sl,
                'take_profit': tp,
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
            f"*Forex Signal Alert*\n"
            f"📈 {signal['pair']} - {direction_symbol}\n"
            f"Type: {signal['type']}\n\n"
            f"📊 Entry: {signal['entry']:.5f}\n"
            f"🛑 Stop Loss: {signal['stop_loss']:.5f}\n"
            f"🎯 Take Profit: {signal['take_profit']:.5f}\n\n"
            f"🔍 Reason: {signal['reason']}\n"
            f"📈 Confidence: {signal['confidence']}%\n"
            f"📉 Volatility: {signal.get('volatility_ratio', 0.0):.2f}%"
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
    print(f"Take Profit: {signal['take_profit']:.5f}")
    try:
        risk = abs(signal['entry'] - signal['stop_loss'])
        reward = abs(signal['take_profit'] - signal['entry'])
        print(f"\nRisk-Reward Ratio: {reward/risk:.2f}:1")
    except:
        print("\nRisk-Reward Ratio: Calculation error")
    print("="*70 + "\n")

# ===== Main Execution =====
if __name__ == "__main__":
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
            confirmation_df = calculate_indicators(confirmation_data) if confirmation_data is not None else None

            trend_signal = detect_trend_following_signal(primary_df, confirmation_df, pair)
            reversal_signal = detect_reversal_signal(primary_df, confirmation_df, pair)

            signal_found = False
            if trend_signal:
                trend_signal['pair'] = pair
                last = primary_df.iloc[-1]
                trend_signal['volatility_ratio'] = last['atr'] / last['close'] * 100
                all_signals.append(trend_signal)
                print(f"  Trend signal detected for {pair} (Confidence: {trend_signal['confidence']}%)")
                signal_found = True
            if reversal_signal:
                reversal_signal['pair'] = pair
                last = primary_df.iloc[-1]
                reversal_signal['volatility_ratio'] = last['atr'] / last['close'] * 100
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
