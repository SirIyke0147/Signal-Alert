import os
import requests
import pandas as pd
import numpy as np
import asyncio
import nest_asyncio
import time
import logging
from datetime import datetime, timedelta
import pytz
import pandas_ta as ta
from telegram import Bot

# Apply nest_asyncio for Jupyter environments
nest_asyncio.apply()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ===== Configuration =====
# Load credentials from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

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

def fetch_binance_data(symbol, interval='4h', limit=500):
    """Fetch Binance market data using direct URL"""
    try:
        # Construct URL with parameters
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        
        # Fetch data
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Parse into DataFrame
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "trades", 
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        
        # Convert to proper data types
        numeric_cols = ["open", "high", "low", "close", "volume"]
        df[numeric_cols] = df[numeric_cols].astype(float)
        df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')
        
        return df.set_index('datetime')[["open", "high", "low", "close", "volume"]]
    
    except requests.exceptions.RequestException as e:
        logger.error(f"API request error for {symbol}: {str(e)}")
    except Exception as e:
        logger.error(f"General error fetching {symbol}: {str(e)}")
    return None

def fetch_yahoo_data(symbol, interval='4h'):
    """Fetch Yahoo Finance data using direct URL"""
    try:
        # Calculate timeframe - reduce days to avoid rate limits
        end = int(datetime.utcnow().timestamp())
        start = int((datetime.utcnow() - timedelta(days=30)).timestamp())
        
        # Construct URL
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "period1": start,
            "period2": end,
            "interval": "1h" if interval == "1h" else "1d",
            "includePrePost": "false"
        }
        
        # Add headers to reduce rate limiting
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Fetch data with delay to avoid rate limiting
        time.sleep(1)
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Parse response
        chart = data["chart"]["result"][0]
        timestamps = chart["timestamp"]
        quotes = chart["indicators"]["quote"][0]
        
        # Create DataFrame
        df = pd.DataFrame({
            "datetime": pd.to_datetime(timestamps, unit="s"),
            "open": quotes["open"],
            "high": quotes["high"],
            "low": quotes["low"],
            "close": quotes["close"],
            "volume": quotes["volume"]
        }).set_index("datetime")
        
        # Resample if needed
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
        logger.error(f"Error fetching Yahoo data for {symbol}: {str(e)}")
        return None

def calculate_technical_indicators(df, params):
    """Calculate technical indicators using pandas_ta"""
    if df is None or len(df) < 50:
        return None
    
    try:
        # Calculate indicators using pandas_ta
        df['ema'] = ta.ema(df['close'], length=params['ema_window'])
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=params['atr_period'])
        
        # ADX returns a DataFrame with multiple columns
        adx_result = ta.adx(df['high'], df['low'], df['close'], length=params['adx_window'])
        if adx_result is not None:
            # Get the ADX column (e.g., 'ADX_16' for 16 period)
            adx_col = [col for col in adx_result.columns if 'ADX_' in col]
            if adx_col:
                df['adx'] = adx_result[adx_col[0]]
        
        df['rsi'] = ta.rsi(df['close'], length=params['rsi_window'])
        
        # Additional calculations
        df['atr_ma'] = df['atr'].rolling(int(params['atr_period'] * 1.5)).mean()
        df['macd'] = df['ema'] - df['ema'].rolling(9).mean()
        df['momentum'] = df['close'] / df['close'].shift(1) - 1
        df['vol_ma'] = df['volume'].rolling(50).mean()
        df['vol_ratio'] = df['volume'] / df['vol_ma']
        
        return df.dropna()
    except Exception as e:
        logger.error(f"Error calculating indicators: {str(e)}", exc_info=True)
        return None

def detect_trading_signal(df, params):
    """Detect trading signals based on technical indicators"""
    if df is None or len(df) < 2:
        return None
    
    try:
        row = df.iloc[-1]
        
        # Volume confirmation
        vol_boost = 20 if row['vol_ratio'] > params['volume_filter'] else 0
        
        # ADX strength - default to 0 if not calculated
        adx_value = row.get('adx', 0)
        adx_boost = 20 if adx_value > 25 else 0
        
        # Momentum confirmation
        momentum_boost = 15 if row['momentum'] > 0 else -15
        
        # Base confidence
        base_confidence = 30 + vol_boost + adx_boost + momentum_boost
        
        # Long signal conditions
        long_conditions = (
            row['macd'] > 0 and
            row['close'] > row['ema'] and
            row.get('rsi', 0) > 50 and  # Handle missing RSI
            row['momentum'] > 0
        )
        
        # Short signal conditions
        short_conditions = (
            row['macd'] < 0 and
            row['close'] < row['ema'] and
            row.get('rsi', 100) < 50 and  # Handle missing RSI
            row['momentum'] < 0
        )
        
        if long_conditions:
            return ("long", base_confidence, row)
        elif short_conditions:
            return ("short", base_confidence, row)
        return None
    except Exception as e:
        logger.error(f"Error detecting signals: {str(e)}", exc_info=True)
        return None

def confirm_with_1h(symbol, direction, params, is_crypto=True):
    """Confirm signal with 1-hour timeframe"""
    try:
        if is_crypto:
            df_1h = fetch_binance_data(symbol, "1h", 200)
        else:
            yahoo_symbol = YAHOO_SYMBOLS.get(symbol, symbol)
            df_1h = fetch_yahoo_data(yahoo_symbol, "1h")
            
        if df_1h is None or df_1h.empty:
            return 0
            
        df_1h = calculate_technical_indicators(df_1h, params)
        if df_1h is None:
            return 0
            
        signal = detect_trading_signal(df_1h, params)
        
        if signal and signal[0] == direction:
            return CONFIDENCE_BOOST
    except Exception as e:
        logger.error(f"1h confirmation failed for {symbol}: {str(e)}", exc_info=True)
    return 0

def format_telegram_message(asset, direction, confidence, row, params):
    """Format Telegram message with proper Markdown escaping"""
    # Escape special characters for MarkdownV2
    def escape_md(text):
        escape_chars = '_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))
    
    uk_time = datetime.now(pytz.timezone('Europe/London')).strftime("%Y-%m-%d %H:%M")
    entry = row['close']
    
    # Handle missing ATR
    atr = row.get('atr', 0)
    atr_ma = row.get('atr_ma', 1)
    
    position_size = CAPITAL * RISK_PER_TRADE
    volatility_ratio = (atr / atr_ma).round(2) if not pd.isna(atr_ma) and atr_ma != 0 else 1.0
    
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
        # Send with MarkdownV2 parsing
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="MarkdownV2"
        )
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram message: {str(e)}")
        return False

async def scan_assets():
    """Main scanning function"""
    logger.info("🚀 Starting asset scan...")
    signals = []
    
    # Scan crypto assets
    for symbol in CRYPTO:
        try:
            logger.info(f"🔍 Analyzing {symbol} (Binance)")
            params = ASSET_PARAMS.get(symbol, {})
            df_4h = fetch_binance_data(symbol, "4h")
            if df_4h is None or df_4h.empty:
                logger.warning(f"   ⚠️ No data for {symbol}")
                continue
                
            df_4h = calculate_technical_indicators(df_4h, params)
            if df_4h is None or df_4h.empty:
                logger.warning(f"   ⚠️ Failed to calculate indicators for {symbol}")
                continue
                
            signal = detect_trading_signal(df_4h, params)
            
            if signal:
                direction, confidence, row = signal
                logger.info(f"   • Found {direction.upper()} signal ({confidence}%)")
                
                # Add 1h confirmation
                confidence += confirm_with_1h(symbol, direction, params, is_crypto=True)
                
                if confidence >= 50:
                    msg = format_telegram_message(symbol, direction, confidence, row, params)
                    signals.append((confidence, msg))
                    logger.info(f"   ✅ Confirmed signal ({confidence}%)")
        except Exception as e:
            logger.error(f"⚠️ Error processing {symbol}: {str(e)}", exc_info=True)
    
    # Add delay between crypto and stock scans
    await asyncio.sleep(2)
    
    # Scan Yahoo assets
    for asset_name in YAHOO_SYMBOLS:
        try:
            symbol = YAHOO_SYMBOLS[asset_name]
            logger.info(f"🔍 Analyzing {asset_name} (Yahoo)")
            params = ASSET_PARAMS.get(asset_name, {})
            df_4h = fetch_yahoo_data(symbol, "4h")
            if df_4h is None or df_4h.empty:
                logger.warning(f"   ⚠️ No data for {asset_name}")
                continue
                
            df_4h = calculate_technical_indicators(df_4h, params)
            if df_4h is None or df_4h.empty:
                logger.warning(f"   ⚠️ Failed to calculate indicators for {asset_name}")
                continue
                
            signal = detect_trading_signal(df_4h, params)
            
            if signal:
                direction, confidence, row = signal
                logger.info(f"   • Found {direction.upper()} signal ({confidence}%)")
                
                # Add 1h confirmation
                confidence += confirm_with_1h(asset_name, direction, params, is_crypto=False)
                
                if confidence >= 50:
                    msg = format_telegram_message(asset_name, direction, confidence, row, params)
                    signals.append((confidence, msg))
                    logger.info(f"   ✅ Confirmed signal ({confidence}%)")
        except Exception as e:
            logger.error(f"⚠️ Error processing {asset_name}: {str(e)}", exc_info=True)
    
    # Process and send top signals
    if signals:
        signals.sort(key=lambda x: x[0], reverse=True)
        top_signals = signals[:TOP_SIGNALS]
        
        for confidence, message in top_signals:
            if await send_telegram_alert(message):
                logger.info(f"📤 Sent Telegram alert for signal ({confidence}%)")
            await asyncio.sleep(1)
    else:
        alert = "⚠️ No qualified signals found. Market conditions not met."
        # Send plain text message without Markdown
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=alert)
        logger.info(alert)
    
    logger.info("✅ Scan completed")

# Run the scanner
if __name__ == "__main__":
    asyncio.run(scan_assets())
