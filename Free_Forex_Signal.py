import os
import re
import json
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import hashlib
import pytz
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SIGNALS_FILE = 'latest_signals.json'
CLEANUP_HOURS = 24  # Cleanup signals older than this

def setup_selenium():
    """Configure headless Chrome browser with retry logic"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    # Add retry logic for browser setup
    max_retries = 3
    for attempt in range(max_retries):
        try:
            driver = webdriver.Chrome(options=options)
            return driver
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(5)

def scrape_signals():
    """Scrape forex signals from website with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            driver = setup_selenium()
            driver.get("https://live-forex-signals.com/en/")
            
            # Wait for dynamic content with multiple checks
            for _ in range(5):
                time.sleep(3)
                page_text = driver.find_element(By.TAG_NAME, "body").text
                if "signal" in page_text.lower():
                    return page_text
            
            # If we got here, signals weren't found
            raise ValueError("Signal content not found on page")
            
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_retries - 1:
                raise
            time.sleep(10)
        finally:
            if 'driver' in locals():
                driver.quit()

def normalize_time_text(text):
    """Normalize time-related text to prevent duplicates from minor wording changes"""
    replacements = {
        r'\b\d+\s*minutes?\b': 'minutes ago',
        r'\b\d+\s*hours?\b': 'hours ago',
        r'\b\d+\s*days?\b': 'days ago',
        r'\bjust now\b': '0 hours ago'
    }
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

def extract_signals(page_text):
    """Parse signals from page text using flexible regex patterns"""
    # Normalize the text first
    normalized_text = normalize_time_text(page_text)
    
    # More flexible pattern to handle website changes
    patterns = [
        # Primary pattern
        r"([A-Z]{3}/[A-Z]{3})\s+.*?signal\s+([^\n]+?)\s*From\s+UTC\+01:00\s+([\d:]+)\s+Till\s+UTC\+01:00\s+([\d:]+)\s+(Buy|Sell)\s+.*?at\s+([\d.]+)\s+Take profit\*? at\s+([\d.]+)\s+Stop loss at\s+([\d.]+)",
        
        # Fallback pattern if structure changes slightly
        r"([A-Z]{3}/[A-Z]{3})\s+.*?(Buy|Sell)\s+signal.*?Posted:\s*([^\n]+)\s*Entry:\s*([\d.]+)\s*TP:\s*([\d.]+)\s*SL:\s*([\d.]+)\s*Time:\s*([\d:]+)\s*-\s*([\d:]+)"
    ]

    signals = []
    for pattern in patterns:
        matches = re.findall(pattern, normalized_text, re.DOTALL)
        if matches:
            for match in matches:
                if len(match) == 8:  # First pattern
                    pair, age, from_time, till_time, action, entry, tp, sl = match
                else:  # Second pattern
                    pair, action, age, entry, tp, sl, from_time, till_time = match
                
                # Create consistent ID regardless of pattern
                signal_id = hashlib.md5(f"{pair}{from_time}{till_time}{entry}".encode()).hexdigest()
                
                signals.append({
                    "id": signal_id,
                    "pair": pair,
                    "posted": normalize_time_text(age.strip()),
                    "from": from_time.strip(),
                    "till": till_time.strip(),
                    "action": action.strip(),
                    "entry": entry.strip(),
                    "take_profit": tp.strip(),
                    "stop_loss": sl.strip(),
                    "timestamp": datetime.utcnow().isoformat()
                })
            break  # Stop after first successful pattern
    
    return signals

def load_previous_signals():
    """Load and cleanup old signals"""
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, 'r') as f:
                data = json.load(f)
                
                # Convert to dict if needed
                if isinstance(data, list):
                    data = {sig["id"]: sig for sig in data}
                
                # Cleanup old signals
                now = datetime.utcnow()
                cleaned_data = {}
                for sig_id, signal in data.items():
                    signal_time = datetime.fromisoformat(signal["timestamp"])
                    if (now - signal_time) < timedelta(hours=CLEANUP_HOURS):
                        cleaned_data[sig_id] = signal
                
                # Save cleaned data if changes were made
                if len(cleaned_data) != len(data):
                    with open(SIGNALS_FILE, 'w') as f:
                        json.dump(cleaned_data, f, indent=2)
                
                return cleaned_data
        except (json.JSONDecodeError, KeyError, ValueError):
            return {}
    return {}

def save_signals(signals):
    """Save signals with cleanup"""
    existing = load_previous_signals()
    updated = {sig["id"]: sig for sig in signals}
    
    # Merge with existing signals
    merged = {**existing, **updated}
    
    # Cleanup old signals during save
    now = datetime.utcnow()
    cleaned = {
        sig_id: sig for sig_id, sig in merged.items()
        if (now - datetime.fromisoformat(sig["timestamp"])) < timedelta(hours=CLEANUP_HOURS)
    }
    
    with open(SIGNALS_FILE, 'w') as f:
        json.dump(cleaned, f, indent=2)

def format_telegram_message(signal):
    """Create formatted Telegram message with emojis"""
    action_emoji = "🟢" if signal["action"].lower() == "buy" else "🔴"
    action_text = "BUY ↗️" if signal["action"].lower() == "buy" else "SELL ↘️"

    # Get current time in UK timezone
    uk_time = datetime.now(pytz.timezone('Europe/London')).strftime("%Y-%m-%d %H:%M")

    return (
        f"⏰ *{uk_time} UK*\n"
        f"{action_emoji} *NEW FREE FOREX SIGNAL* {action_emoji}\n\n"
        f"📈 *Pair:* {signal['pair']}\n"
        f"⏱ *Posted:* {signal['posted']}\n"
        f"🕒 *Active:* {signal['from']} - {signal['till']} UTC+1\n\n"
        f"⚡ *Action:* {action_text}\n"
        f"💰 *Entry Price:* `{signal['entry']}`\n"
        f"🎯 *Take Profit:* `{signal['take_profit']}`\n"
        f"🛡 *Stop Loss:* `{signal['stop_loss']}`\n\n"
        f"🔔 *Signal ID:* `{signal['id'][:8]}`"
    )

def send_telegram_message(message):
    """Send message to Telegram with retry logic"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                print(f"❌ Final attempt failed to send Telegram message: {e}")
                return False
            time.sleep(5)

def main():
    print("🚀 Starting forex signal scraper...")
    print(f"🕒 Current UTC time: {datetime.utcnow().isoformat()}")

    try:
        # Scrape website
        print("🌐 Scraping website...")
        page_text = scrape_signals()

        # Extract signals
        print("🔍 Parsing signals...")
        current_signals = extract_signals(page_text)

        if not current_signals:
            print("⚠️ No signals found on the page")
            return

        print(f"📊 Found {len(current_signals)} signals")

        # Load previous signals
        previous_signals = load_previous_signals()
        previous_ids = set(previous_signals.keys())

        # Find new signals
        new_signals = [
            sig for sig in current_signals
            if sig["id"] not in previous_ids
        ]

        print(f"✨ Found {len(new_signals)} new signals")

        # Process new signals
        for signal in new_signals:
            print(f"  💌 Processing signal {signal['id'][:8]} for {signal['pair']}")
            message = format_telegram_message(signal)

            if send_telegram_message(message):
                print("    📤 Signal sent to Telegram")
                # Update previous signals only after successful send
                previous_signals[signal["id"]] = signal
            else:
                print("    ❌ Failed to send signal")

        # Save updated signal history
        save_signals(list(previous_signals.values()))
        print(f"💾 Saved {len(previous_signals)} signals to history file")

    except Exception as e:
        print(f"🔥 Critical error: {str(e)}")
        # Send error notification to Telegram
        error_msg = f"🚨 Forex Signal Scraper Failed 🚨\nError: {str(e)}"
        send_telegram_message(error_msg)
        raise  # Re-raise for GitHub Actions to catch

if __name__ == "__main__":
    main()
