import os
import requests
import pandas as pd
import numpy as np
import asyncio
import nest_asyncio
from datetime import datetime, timedelta
import pytz
import talib as ta
from telegram import Bot

# Apply nest_asyncio for Jupyter environments
nest_asyncio.apply()

# ===== Configuration =====
# Load credentials from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')  # Not used in current implementation

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise EnvironmentError("Missing Telegram credentials in environment variables")

# Trading parameters
CAPITAL = 100
RISK_PER_TRADE = 0.01
TOP_SIGNALS = 3
CONFIDENCE_BOOST = 10  # 10% boost for 1h confirmation

# Asset-specific parameters
ASSET_PARAMS = {
    "ETHUSDT": {
        "ema_window": 18,
        "atr_period": 12,
        "adx_window": 16,
        "rsi_window": 12,
        "volume_filter": 1.8
    },
    "BTCUSDT": {
        "ema_window": 22,
        "atr_period": 14,
        "adx_window": 18,
        "rsi_window": 14,
        "volume_filter": 1.5
    },
    "ADAUSDT": {
        "ema_window": 16,
        "atr_period": 10,
        "adx_window": 14,
        "rsi_window": 10,
        "volume_filter": 2.0
    },
    "BNBUSDT": {
        "ema_window": 20,
        "atr_period": 12,
        "adx_window": 16,
        "rsi_window": 12,
        "volume_filter": 1.7
    },
    "Gold": {
        "ema_window": 24,
        "atr_period": 18,
        "adx_window": 20,
        "rsi_window": 16,
        "volume_filter": 1.3
    },
    "Silver": {
        "ema_window": 20,
        "atr_period": 14,
        "adx_window": 18,
        "rsi_window": 14,
        "volume_filter": 1.4
    },
    "Microsoft": {
        "ema_window": 26,
        "atr_period": 16,
        "adx_window": 22,
        "rsi_window": 18,
        "volume_filter": 1.2
    }
}

# Asset groups
CRYPTO = ["ETHUSDT", "BTCUSDT", "ADAUSDT", "BNBUSDT"]
YAHOO_SYMBOLS = {"Gold": "GC=F", "Silver": "SI=F", "Microsoft": "MSFT"}

# Initialize Telegram bot
bot = Bot(token=TELEGRAM_BOT_TOKEN)

def fetch_binance_data(symbol, interval, lookback=500):
    """Fetch Binance market data (public endpoint)"""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={lookback}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "trades", 
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')
        df.set_index('datetime', inplace=True)
        return df[["open", "high", "low", "close", "volume"]].astype(float)
    except Exception as e:
        print(f"Error fetching Binance data for {symbol}: {str(e)}")
        return None

def fetch_yahoo_data(symbol, interval):
    """Fetch Yahoo Finance data using yfinance alternative method"""
    try:
        # Calculate timeframe
        end_date = datetime.utcnow()
        if interval == "1h":
            start_date = end_date - timedelta(days=60)
            period = "60d"
        else:  # 4h
            start_date = end_date - timedelta(days=120)
            period = "120d"
        
        # Use public Yahoo Finance API
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "period1": int(start_date.timestamp()),
            "period2": int(end_date.timestamp()),
            "interval": "1h" if interval == "1h" else "1d",
            "includePrePost": False
        }
        
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Parse response
        chart_data = data["chart"]["result"][0]
        timestamps = chart_data["timestamp"]
        quotes = chart_data["indicators"]["quote"][0]
        
        df = pd.DataFrame({
            "datetime": pd.to_datetime(timestamps, unit="s"),
            "open": quotes["open"],
            "high": quotes["high"],
            "low": quotes["low"],
            "close": quotes["close"],
            "volume": quotes["volume"]
        }).set_index("datetime")
        
        # Resample to 4h if needed
        if interval == "4h":
            df = df.resample("4h").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna()
            
        return df
    except Exception as e:
        print(f"Error fetching Yahoo data for {symbol}: {str(e)}")
        return None

def calculate_technical_indicators(df, params):
    """Calculate technical indicators using TA-Lib"""
    if df is None or len(df) < 50:
        return None
    
    try:
        # Calculate indicators
        df['ema'] = ta.EMA(df['close'], timeperiod=params['ema_window'])
        df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=params['atr_period'])
        df['adx'] = ta.ADX(df['high'], df['low'], df['close'], timeperiod=params['adx_window'])
        df['rsi'] = ta.RSI(df['close'], timeperiod=params['rsi_window'])
        
        # Additional calculations
        df['atr_ma'] = df['atr'].rolling(int(params['atr_period'] * 1.5)).mean()
        df['macd'] = df['ema'] - df['ema'].rolling(9).mean()
        df['momentum'] = df['close'] / df['close'].shift(1) - 1
        df['vol_ma'] = df['volume'].rolling(50).mean()
        df['vol_ratio'] = df['volume'] / df['vol_ma']
        
        return df.dropna()
    except Exception as e:
        print(f"Error calculating indicators: {str(e)}")
        return None

def detect_trading_signal(df, params):
    """Detect trading signals based on technical indicators"""
    if df is None or len(df) < 2:
        return None
    
    try:
        row = df.iloc[-1]
        
        # Volume confirmation
        vol_boost = 20 if row['vol_ratio'] > params['volume_filter'] else 0
        
        # ADX strength
        adx_boost = 20 if row['adx'] > 25 else 0
        
        # Momentum confirmation
        momentum_boost = 15 if row['momentum'] > 0 else -15
        
        # Base confidence
        base_confidence = 30 + vol_boost + adx_boost + momentum_boost
        
        # Long signal conditions
        long_conditions = (
            row['macd'] > 0 and
            row['close'] > row['ema'] and
            row['rsi'] > 50 and
            row['momentum'] > 0
        )
        
        # Short signal conditions
        short_conditions = (
            row['macd'] < 0 and
            row['close'] < row['ema'] and
            row['rsi'] < 50 and
            row['momentum'] < 0
        )
        
        if long_conditions:
            return ("long", base_confidence, row)
        elif short_conditions:
            return ("short", base_confidence, row)
        return None
    except Exception as e:
        print(f"Error detecting signals: {str(e)}")
        return None

def confirm_with_1h(symbol, direction, params, is_crypto=True):
    """Confirm signal with 1-hour timeframe"""
    try:
        if is_crypto:
            df_1h = fetch_binance_data(symbol, "1h", 200)
        else:
            df_1h = fetch_yahoo_data(YAHOO_SYMBOLS.get(symbol, symbol), "1h")
            
        if df_1h is None:
            return 0
            
        df_1h = calculate_technical_indicators(df_1h, params)
        signal = detect_trading_signal(df_1h, params)
        
        if signal and signal[0] == direction:
            return CONFIDENCE_BOOST
    except Exception as e:
        print(f"1h confirmation failed for {symbol}: {str(e)}")
    return 0

def format_telegram_message(asset, direction, confidence, row, params):
    """Format Telegram message with proper Markdown escaping"""
    # Escape special characters for MarkdownV2
    def escape_md(text):
        escape_chars = '_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))
    
    uk_time = datetime.now(pytz.timezone('Europe/London')).strftime("%Y-%m-%d %H:%M")
    entry = row['close']
    atr = row['atr']
    position_size = CAPITAL * RISK_PER_TRADE
    volatility_ratio = (row['atr'] / row['atr_ma']).round(2) if not pd.isna(row['atr_ma']) else 1.0
    
    # Calculate take profit levels
    tp_levels = []
    for i in range(3):
        multiplier = 0.5 + (i * 0.3)
        if direction == "long":
            tp = entry + (atr * multiplier)
        else:
            tp = entry - (atr * multiplier)
        tp_levels.append(tp)
    
    # Format message with MarkdownV2
    message = (
        f"⏰ *{escape_md(uk_time)} UK*\n"
        f"📈 *{escape_md(asset)} Signal \\- {direction.upper()}* ⚡\n\n"
        f"*Price Info:*\n"
        f"• Current price: `{entry:.4f}`\n"
        f"• Position: `${position_size*3:.2f}` (${position_size:.2f}/tier)\n"
        f"• Volatility Ratio: `{volatility_ratio:.2f}`\n\n"
        f"🔰 *Take Profit Targets:*\n"
        f"• TP1: `{tp_levels[0]:.4f}` 🎯\n"
        f"• TP2: `{tp_levels[1]:.4f}` 🎯\n"
        f"• TP3: `{tp_levels[2]:.4f}` 🎯\n\n"
        f"📊 *Confidence:* `{confidence:.0f}%`"
    )
    
    return message

async def send_telegram_alert(message):
    """Send formatted message to Telegram"""
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="MarkdownV2"
        )
        return True
    except Exception as e:
        print(f"Error sending Telegram message: {str(e)}")
        return False

async def scan_assets():
    """Main scanning function"""
    print("🚀 Starting asset scan...")
    signals = []
    
    # Scan crypto assets
    for symbol in CRYPTO:
        try:
            print(f"🔍 Analyzing {symbol} (Binance)")
            params = ASSET_PARAMS.get(symbol, {})
            df_4h = fetch_binance_data(symbol, "4h")
            if df_4h is None:
                continue
                
            df_4h = calculate_technical_indicators(df_4h, params)
            signal = detect_trading_signal(df_4h, params)
            
            if signal:
                direction, confidence, row = signal
                print(f"   • Found {direction.upper()} signal ({confidence}%)")
                
                # Add 1h confirmation
                confidence += confirm_with_1h(symbol, direction, params, is_crypto=True)
                
                if confidence >= 50:
                    msg = format_telegram_message(symbol, direction, confidence, row, params)
                    signals.append((confidence, msg))
                    print(f"   ✅ Confirmed signal ({confidence}%)")
        except Exception as e:
            print(f"⚠️ Error processing {symbol}: {str(e)}")
    
    # Scan Yahoo assets
    for asset_name in YAHOO_SYMBOLS:
        try:
            symbol = YAHOO_SYMBOLS[asset_name]
            print(f"🔍 Analyzing {asset_name} (Yahoo)")
            params = ASSET_PARAMS.get(asset_name, {})
            df_4h = fetch_yahoo_data(symbol, "4h")
            if df_4h is None:
                continue
                
            df_4h = calculate_technical_indicators(df_4h, params)
            signal = detect_trading_signal(df_4h, params)
            
            if signal:
                direction, confidence, row = signal
                print(f"   • Found {direction.upper()} signal ({confidence}%)")
                
                # Add 1h confirmation
                confidence += confirm_with_1h(asset_name, direction, params, is_crypto=False)
                
                if confidence >= 50:
                    msg = format_telegram_message(asset_name, direction, confidence, row, params)
                    signals.append((confidence, msg))
                    print(f"   ✅ Confirmed signal ({confidence}%)")
        except Exception as e:
            print(f"⚠️ Error processing {asset_name}: {str(e)}")
    
    # Process and send top signals
    if signals:
        signals.sort(key=lambda x: x[0], reverse=True)
        top_signals = signals[:TOP_SIGNALS]
        
        for confidence, message in top_signals:
            if await send_telegram_alert(message):
                print(f"📤 Sent Telegram alert for signal ({confidence}%)")
            await asyncio.sleep(1)
    else:
        alert = "⚠️ No qualified signals found. Market conditions not met."
        await send_telegram_alert(alert)
        print(alert)
    
    print("✅ Scan completed")

# Run the scanner
if __name__ == "__main__":
    asyncio.run(scan_assets())
