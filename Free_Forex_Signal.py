import os
import re
import json
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import hashlib

# Load configuration from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SIGNALS_FILE = 'latest_signals.json'

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("Missing Telegram credentials in environment variables")

def setup_selenium():
    """Configure headless Chrome browser"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=options)

def scrape_signals():
    """Scrape forex signals from website"""
    driver = setup_selenium()
    try:
        driver.get("https://live-forex-signals.com/en/")
        time.sleep(10)  # Wait for page to load

        # Extract page content
        page_text = driver.find_element(By.TAG_NAME, "body").text
        return page_text
    finally:
        driver.quit()

def extract_signals(page_text):
    """Parse signals from page text using regex"""
    pattern = re.compile(
        r"([A-Z]{3}/[A-Z]{3})\s+.*?signal\s+(\d+ hours? ago).*?From\s+UTC\+01:00\s+([\d:]+)\s+Till\s+UTC\+01:00\s+([\d:]+)\s+(Buy|Sell)\s+.*?at\s+([\d.]+)\s+Take profit\* at\s+([\d.]+)\s+Stop loss at\s+([\d.]+)",
        re.DOTALL
    )

    signals = []
    for match in pattern.findall(page_text):
        pair, age, from_time, till_time, action, entry, tp, sl = match
        signal_id = hashlib.md5(f"{pair}{from_time}{till_time}".encode()).hexdigest()

        signals.append({
            "id": signal_id,
            "pair": pair,
            "posted": age,
            "from": from_time,
            "till": till_time,
            "action": action,
            "entry": entry,
            "take_profit": tp,
            "stop_loss": sl,
            "timestamp": datetime.utcnow().isoformat()
        })

    return signals

def load_previous_signals():
    """Load previously processed signals from file"""
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, 'r') as f:
                data = json.load(f)
                # Convert list to dictionary if needed
                if isinstance(data, list):
                    return {sig["id"]: sig for sig in data}
                return data
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}

def save_signals(signals):
    """Save signals to file"""
    with open(SIGNALS_FILE, 'w') as f:
        json.dump({sig["id"]: sig for sig in signals}, f, indent=2)

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
    """Send message to Telegram with error handling"""
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
        print(f"Error sending Telegram message: {e}")
        return False

def main():
    print("🚀 Starting forex signal scraper...")

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

if __name__ == "__main__":
    import pytz  # Imported here to avoid top-level dependency for GitHub Actions
    main()
