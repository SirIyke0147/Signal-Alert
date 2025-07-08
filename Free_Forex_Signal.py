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
DEBUG_FILE = 'page_content.txt'

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("Missing Telegram credentials in environment variables")

def setup_selenium():
    """Configure headless Chrome browser with retry logic"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_argument('--disable-infobars')
    options.binary_location = os.getenv('CHROME_PATH', '/usr/bin/chromium-browser')
    
    # Explicitly specify chromedriver path
    service = webdriver.ChromeService(executable_path='/usr/bin/chromedriver')
    return webdriver.Chrome(service=service, options=options)

def scrape_signals():
    """Scrape forex signals from website with enhanced loading checks"""
    driver = setup_selenium()
    try:
        driver.get("https://live-forex-signals.com/en/")
        
        # Wait for dynamic content to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".signal-item, .signal, .trading-signal"))
        )
        
        # Scroll to trigger lazy loading
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        # Extract page content
        page_text = driver.find_element(By.TAG_NAME, "body").text
        
        # Save for debugging
        with open(DEBUG_FILE, 'w', encoding='utf-8') as f:
            f.write(page_text)
            
        return page_text
    except Exception as e:
        print(f"⚠️ Scraping error: {str(e)}")
        return ""
    finally:
        driver.quit()

def extract_signals(page_text):
    """Robust signal parser with multiple fallback patterns"""
    if not page_text:
        return []

    # Primary pattern - focused on core data elements
    primary_pattern = re.compile(
        r"([A-Z]{3}/[A-Z]{3})\b.*?"  # Currency pair
        r"(\d+\s*(?:minute|hour|day)s?\s+ago)\b.*?"  # Time posted
        r"From\b.*?([\d:]{4,5})\b.*?"  # From time
        r"Till\b.*?([\d:]{4,5})\b.*?"  # Till time
        r"(Buy|Sell)\b.*?"  # Action
        r"at\s+([\d.]+)\b.*?"  # Entry price
        r"Take\s+profit.*?([\d.]+)\b.*?"  # Take profit
        r"Stop\s+loss\s+([\d.]+)",  # Stop loss
        re.DOTALL | re.IGNORECASE
    )
    
    # Fallback pattern - more flexible matching
    fallback_pattern = re.compile(
        r"([A-Z]{3}/[A-Z]{3})\b.*?"  # Currency pair
        r"(\d+\s*(?:minute|hour|day)s?\s+ago)\b.*?"  # Time posted
        r"([\d:]{4,5})\s*[-–]\s*([\d:]{4,5})\b.*?"  # Time range
        r"(Buy|Sell)\b.*?"  # Action
        r"(\d+\.\d+)\b.*?"  # Entry price
        r"(\d+\.\d+)\b.*?"  # Take profit
        r"(\d+\.\d+)",  # Stop loss
        re.DOTALL | re.IGNORECASE
    )
    
    signals = []
    seen_ids = set()
    
    # Try primary pattern first
    for match in primary_pattern.findall(page_text):
        pair, age, from_time, till_time, action, entry, tp, sl = match
        signal_id = hashlib.md5(f"{pair}{from_time}{till_time}".encode()).hexdigest()
        
        if signal_id not in seen_ids:
            signals.append(create_signal_dict(pair, age, from_time, till_time, action, entry, tp, sl, signal_id))
            seen_ids.add(signal_id)
    
    # If no signals found, try fallback pattern
    if not signals:
        for match in fallback_pattern.findall(page_text):
            pair, age, from_time, till_time, action, entry, tp, sl = match
            signal_id = hashlib.md5(f"{pair}{from_time}{till_time}".encode()).hexdigest()
            
            if signal_id not in seen_ids:
                signals.append(create_signal_dict(pair, age, from_time, till_time, action, entry, tp, sl, signal_id))
                seen_ids.add(signal_id)
    
    return signals

def create_signal_dict(pair, age, from_time, till_time, action, entry, tp, sl, signal_id):
    """Create standardized signal dictionary"""
    # Normalize time formats (HH:MM)
    from_time = re.sub(r"(\d{1,2}):(\d{2})", r"\1:\2", from_time)
    till_time = re.sub(r"(\d{1,2}):(\d{2})", r"\1:\2", till_time)
    
    return {
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
    }

def load_previous_signals():
    """Load previously processed signals from file with backup"""
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, 'r') as f:
                data = json.load(f)
                return {sig["id"]: sig for sig in data}
    except Exception as e:
        print(f"⚠️ Error loading signals: {str(e)}")
    
    return {}

def save_signals(signals):
    """Save signals to file with atomic write"""
    try:
        # Atomic write to prevent corruption
        temp_file = f"{SIGNALS_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump([sig for sig in signals.values()], f, indent=2)
        
        os.replace(temp_file, SIGNALS_FILE)
    except Exception as e:
        print(f"⚠️ Error saving signals: {str(e)}")

def format_telegram_message(signal):
    """Create formatted Telegram message with fallbacks"""
    action_emoji = "🟢" if signal["action"].lower() == "buy" else "🔴"
    action_text = "BUY ↗️" if signal["action"].lower() == "buy" else "SELL ↘️"
    
    # Get current time in UK timezone
    uk_time = datetime.now(pytz.timezone('Europe/London')).strftime("%Y-%m-%d %H:%M")
    
    # Build message with fallbacks for missing data
    message = f"⏰ *{uk_time} UK*\n"
    message += f"{action_emoji} *NEW FOREX SIGNAL* {action_emoji}\n\n"
    message += f"📈 *Pair:* {signal.get('pair', 'N/A')}\n"
    message += f"⏱ *Posted:* {signal.get('posted', 'recently')}\n"
    
    if 'from' in signal and 'till' in signal:
        message += f"🕒 *Active:* {signal['from']} - {signal['till']} UTC+1\n\n"
    
    message += f"⚡ *Action:* {action_text}\n"
    message += f"💰 *Entry Price:* `{signal.get('entry', 'N/A')}`\n"
    message += f"🎯 *Take Profit:* `{signal.get('take_profit', 'N/A')}`\n"
    message += f"🛡 *Stop Loss:* `{signal.get('stop_loss', 'N/A')}`\n\n"
    message += f"🔔 *Signal ID:* `{signal['id'][:8]}`"
    
    return message

def send_telegram_message(message):
    """Send message to Telegram with retry logic"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Telegram send error (attempt {attempt+1}): {e}")
            time.sleep(2)
    
    return False

def main():
    print("🚀 Starting forex signal scraper...")
    
    # Scrape website
    print("🌐 Scraping website...")
    page_text = scrape_signals()
    
    if not page_text:
        print("❌ Failed to scrape page content")
        return
    
    # Save page content for debugging
    with open(DEBUG_FILE, 'w', encoding='utf-8') as f:
        f.write(page_text)
    print(f"💾 Saved page content to {DEBUG_FILE}")
    
    # Extract signals
    print("🔍 Parsing signals...")
    current_signals = extract_signals(page_text)
    
    if not current_signals:
        print("⚠️ No signals found on the page")
        # Send alert if no signals found during market hours
        uk_hour = datetime.now(pytz.timezone('Europe/London')).hour
        if 6 <= uk_hour <= 18:  # Market hours
            send_telegram_message("⚠️ *NO SIGNALS FOUND* on website during market hours. Please check scraper.")
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
    save_signals(previous_signals)
    print(f"💾 Saved {len(previous_signals)} signals to history file")

if __name__ == "__main__":
    main()
