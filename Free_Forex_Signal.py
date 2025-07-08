import os
import re
import json
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import hashlib
import pytz

# Load configuration from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SIGNALS_FILE = 'latest_signals.json'

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("Missing Telegram credentials in environment variables")

def setup_selenium():
    """Configure headless Chrome browser with reliability improvements"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    return webdriver.Chrome(options=options)

def scrape_signals():
    """Scrape forex signals from website with enhanced reliability"""
    driver = setup_selenium()
    try:
        print("🌐 Navigating to website...")
        driver.get("https://live-forex-signals.com/en/")
        
        # Wait for page to load dynamically
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # Scroll to trigger content loading
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        print("📄 Extracting page content...")
        page_text = driver.find_element(By.TAG_NAME, "body").text
        return page_text
    except Exception as e:
        print(f"⚠️ Scraping error: {str(e)}")
        return ""
    finally:
        driver.quit()

def extract_signals(page_text):
    """Parse signals from page text using flexible regex"""
    if not page_text:
        return []

    # More flexible pattern to handle time variations
    pattern = re.compile(
        r"([A-Z]{3}/[A-Z]{3})\s+.*?signal\s+(\d+\s*(?:minute|hour|day)s?\s+ago).*?From\s+UTC\+01:00\s+([\d:]+)\s+Till\s+UTC\+01:00\s+([\d:]+)\s+(Buy|Sell)\s+.*?at\s+([\d.]+)\s+Take\s+profit[\*\s]*at\s+([\d.]+)\s+Stop\s+loss\s+at\s+([\d.]+)",
        re.DOTALL | re.IGNORECASE
    )
    
    signals = []
    seen_ids = set()
    
    # Get current date for daily reset
    uk_date = datetime.now(pytz.timezone('Europe/London')).strftime('%Y-%m-%d')
    
    for match in pattern.findall(page_text):
        pair, age, from_time, till_time, action, entry, tp, sl = match
        
        # Create date-based ID to prevent duplicates across days
        signal_id = hashlib.md5(f"{uk_date}:{pair}:{from_time}:{till_time}".encode()).hexdigest()
        
        # Skip duplicates in this run
        if signal_id in seen_ids:
            continue
        seen_ids.add(signal_id)
        
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
    """Load previously processed signals with daily reset"""
    # Check if we need to reset for new day
    reset_signals = False
    if os.path.exists(SIGNALS_FILE):
        file_mtime = datetime.fromtimestamp(os.path.getmtime(SIGNALS_FILE))
        if file_mtime.date() < datetime.utcnow().date():
            print("♻️ Resetting signal history for new day")
            reset_signals = True
    
    if reset_signals or not os.path.exists(SIGNALS_FILE):
        return {}
    
    try:
        with open(SIGNALS_FILE, 'r') as f:
            data = json.load(f)
            # Convert list to dictionary if needed
            if isinstance(data, list):
                return {sig["id"]: sig for sig in data}
            return data
    except (json.JSONDecodeError, KeyError):
        return {}

def save_signals(signals):
    """Save signals to file with atomic write"""
    # Create temporary file first to prevent corruption
    temp_file = f"{SIGNALS_FILE}.tmp"
    with open(temp_file, 'w') as f:
        json.dump({sig["id"]: sig for sig in signals}, f, indent=2)
    
    # Replace original file
    os.replace(temp_file, SIGNALS_FILE)

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
    
    # Retry up to 3 times
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Telegram error (attempt {attempt+1}): {e}")
            time.sleep(2)
    
    return False

def main():
    print("🚀 Starting forex signal scraper...")
    start_time = time.time()
    
    # Scrape website
    page_text = scrape_signals()
    if not page_text:
        print("❌ Failed to scrape page content")
        return
    
    # Extract signals
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
    
    # Performance metrics
    duration = time.time() - start_time
    print(f"⏱ Execution time: {duration:.2f} seconds")

if __name__ == "__main__":
    main()
