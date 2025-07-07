import os
import requests
import pandas as pd
import numpy as np
import ta
from datetime import datetime
import heapq
import warnings
import logging
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Suppress warnings
warnings.filterwarnings("ignore")

# Telegram configuration - Use environment variables only
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logger.error("Telegram credentials not found in environment variables")
    exit(1)

# Asset symbol to name mapping
ASSET_NAMES = {
    "SB=F": "Sugar Futures",
    "SI=F": "Silver Futures",
    "HG=F": "Copper Futures",
    "NG=F": "Natural Gas Futures",
    "ZC=F": "Corn Futures",
    "LMT": "Lockheed Martin",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "IWM": "Russell 2000 ETF",
    "^RUT": "Russell 2000 Index",
    "DIA": "DIA (SPDR Dow Jones ETF)",  
    "QQQ": "QQQ (Invesco QQQ Trust or NASDAQ 100 Index)",
    "ADAUSDT": "Cardano",
    "BNBUSDT": "Binance Coin"
}

# List of symbols to scan
PAIRS = list(ASSET_NAMES.keys())

# Fetch 4h data with retry mechanism
def fetch_4h(symbol, limit=50, retries=3):
    for attempt in range(retries):
        try:
            if symbol.endswith('USDT'):
                url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval=4h&limit={limit}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                if isinstance(data, list):
                    df = pd.DataFrame(data, columns=[
                        "open_time", "open", "high", "low", "close", "volume", 
                        "close_time", "quote_asset_volume", "trades", 
                        "taker_buy_base", "taker_buy_quote", "ignore"
                    ])
                    df["dt"] = pd.to_datetime(df["open_time"], unit='ms')
                    return df.set_index("dt")[["open", "high", "low", "close", "volume"]].astype(float)
                return pd.DataFrame()
            else:
                import yfinance as yf
                df = yf.download(symbol, period="50d", interval="4h", progress=False).dropna()
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                df.columns = ["open", "high", "low", "close", "volume"]
                df.index.name = "dt"
                return df
                
        except (requests.exceptions.RequestException, ConnectionError) as e:
            logger.warning(f"Attempt {attempt+1}/{retries} failed for {symbol}: {str(e)}")
            time.sleep(2 ** attempt)  # Exponential backoff
    logger.error(f"Failed to fetch data for {symbol} after {retries} attempts")
    return pd.DataFrame()

# Custom TEMA
def tema(series, window):
    ema1 = series.ewm(span=window, adjust=False).mean()
    ema2 = ema1.ewm(span=window, adjust=False).mean()
    ema3 = ema2.ewm(span=window, adjust=False).mean()
    return 3 * (ema1 - ema2) + ema3

# Custom CMO
def cmo(series, window):
    """Chande Momentum Oscillator (Vectorized Version)"""
    try:
        # Calculate price changes
        delta = series.diff()
        
        # Separate upward and downward movements
        up = delta.where(delta > 0, 0.0)
        down = (-delta).where(delta < 0, 0.0)
        
        # Calculate rolling sums
        sum_up = up.rolling(window=window, min_periods=1).sum()
        sum_down = down.rolling(window=window, min_periods=1).sum()
        
        # Calculate denominator (sum of up and down movements)
        denom = sum_up + sum_down
        
        # Calculate CMO with safe division
        result = 100 * (sum_up - sum_down) / denom
        
        # Handle division by zero cases
        return result.fillna(0)
    except Exception as e:
        logger.error(f"Error calculating CMO: {str(e)}")
        return pd.Series(np.zeros(len(series)), index=series.index)

# Add indicators with validation
def add_indicators(df):
    if df.empty:
        return df
        
    df["ATR"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"])
    df["ATR_MA20"] = df["ATR"].rolling(20).mean()
    df["Volume_MA50"] = df["volume"].rolling(50).mean()
    df["ADX"] = ta.trend.adx(df["high"], df["low"], df["close"])
    df["VW_MACD"] = ta.trend.macd_diff(df["close"])
    df["tema_20"] = tema(df["close"], window=20)
    df["cmo_14"] = cmo(df["close"], window=14)
    df["supertrend_dir"] = np.where(df["close"] > df["tema_20"], 1, -1)
    return df.dropna()

# Send Telegram message with error handling
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {str(e)}")
        return False

# Scan for signal with comprehensive validation
def scan(symbol):
    try:
        logger.info(f"Scanning {symbol}")
        df = fetch_4h(symbol)
        if len(df) < 50:
            logger.warning(f"Insufficient data for {symbol}")
            return None
            
        df = add_indicators(df)
        if df.empty:
            logger.warning(f"Empty DataFrame after indicators for {symbol}")
            return None
            
        last = df.iloc[-1]

        # Validate indicator values
        if any(np.isnan(last[['ADX', 'VW_MACD', 'tema_20', 'cmo_14']])):
            logger.warning(f"Invalid indicator values for {symbol}")
            return None

        if last['ADX'] < 20:
            return None

        indicators = {
            'macd': last['VW_MACD'] > 0,
            'supertrend': last['supertrend_dir'] == 1,
            'tema': last['close'] > last['tema_20'],
            'cmo': last['cmo_14'] > 0
        }

        long_score = sum(indicators.values())
        short_score = sum(not v for v in indicators.values())

        if long_score >= 3:
            direction = 'LONG'
            confidence = int((long_score / 4) * 100)
        elif short_score >= 3:
            direction = 'SHORT'
            confidence = int((short_score / 4) * 100)
        else:
            return None

        entry = last['close']
        atr = last['ATR']
        atr_multiplier = 1.5
        stop_loss = entry - atr * atr_multiplier if direction == 'LONG' else entry + atr * atr_multiplier
        
        # Calculate dynamic take profit levels
        if direction == 'LONG':
            take_profit = [
                entry * 1.02,
                entry * 1.035,
                entry * 1.05
            ]
        else:
            take_profit = [
                entry * 0.98,
                entry * 0.965,
                entry * 0.95
            ]

        return {
            'symbol': symbol,
            'name': ASSET_NAMES.get(symbol, symbol),
            'direction': direction,
            'confidence': confidence,
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'time': datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        }
    except Exception as e:
        logger.error(f"Error scanning {symbol}: {str(e)}", exc_info=True)
        return None

def main():
    logger.info("Starting commodity signal scanner")
    
    signals = []
    for symbol in PAIRS:
        signal = scan(symbol)
        if signal:
            signals.append(signal)
            time.sleep(1)  # Rate limiting between requests

    if not signals:
        logger.info("No signals found")
        return

    # Get top 3 signals by confidence
    top3 = heapq.nlargest(3, signals, key=lambda x: x['confidence'])

    for sig in top3:
        msg = (
        f"⏰ {sig['time']}\n"
        f"📈 *Commodity Signal Alert* ⚡\n\n"
        f"📈 *Direction:* {sig['direction']}\n"
        f"🔹 *Asset:* {sig['name']} ({sig['symbol']})\n"
        f"💰 *Entry Price:* {sig['entry']:.4f}\n"
        f"🛡️ *Stop Loss:* {sig['stop_loss']:.4f}\n"
        f"🎯 *Take Profit Targets:*\n"
        f"TP1: {sig['take_profit'][0]:.4f}\n"
        f"TP2: {sig['take_profit'][1]:.4f}\n"
        f"TP3: {sig['take_profit'][2]:.4f}\n"
        f"📊 *Confidence:* {sig['confidence']}%"
    )
        if send_telegram_message(msg):
            logger.info(f"Sent signal for {sig['symbol']}")
        else:
            logger.error(f"Failed to send signal for {sig['symbol']}")

    logger.info("Scan completed")

if __name__ == "__main__":
    main()
