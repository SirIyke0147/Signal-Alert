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
from selenium.common.exceptions import WebDriverException, TimeoutException
import time
import hashlib
import pytz

# Load configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SIGNALS_FILE = 'latest_signals.json'
DEBUG_FILE = 'page_content.txt'

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("Missing Telegram credentials in environment variables")

def setup_selenium():
    """Configure headless Chrome with compatibility fixes"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_argument('--disable-infobars')
    
    # Use system Chrome
    chrome_binary = os.getenv('CHROME_PATH', '/usr/bin/google-chrome')
    options.binary_location = chrome_binary
    
    # ChromeDriver setup
    service = webdriver.ChromeService(
        executable_path='/usr/bin/chromedriver',
        service_args=['--verbose', '--log-path=chromedriver.log']
    )
    return webdriver.Chrome(service=service, options=options)

def scrape_signals():
    """Robust scraping with multiple fallbacks"""
    driver = None
    try:
        driver = setup_selenium()
        print("🌐 Navigating to website...")
        driver.get("https://live-forex-signals.com/en/")
        
        # Multiple wait strategies
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//body"))
            )
        except TimeoutException:
            print("ℹ️ Using fallback wait for body content")
            time.sleep(10)
        
        # Scroll to trigger content loading
        print("🖱️ Scrolling to load content...")
        for _ in range(2):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5);")
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        
        # Extract content
        print("📝 Extracting page content...")
        body = driver.find_element(By.TAG_NAME, "body")
        page_text = body.text
        
        # Save for debugging
        with open(DEBUG_FILE, 'w', encoding='utf-8') as f:
            f.write(page_text)
            
        return page_text
    except WebDriverException as e:
        print(f"🚨 Critical WebDriver error: {str(e)}")
        return ""
    except Exception as e:
        print(f"⚠️ General scraping error: {str(e)}")
        return ""
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def extract_signals(page_text):
    """Signal extraction with daily reset compatibility"""
    if not page_text:
        return []

    print("🔍 Parsing signals...")
    signals = []
    
    # Get current date for daily reset
    uk_date = datetime.now(pytz.timezone('Europe/London')).strftime('%Y-%m-%d')
    
    # Flexible pattern
    pattern = re.compile(
        r"([A-Z]{3}/[A-Z]{3})\b.*?"  # Currency pair
        r"(\d+\s*(?:minute|hour|day)s?\s+ago)\b.*?"  # Time posted
        r"([\d:]+)\s*[-–]\s*([\d:]+)\b.*?"  # Time range
        r"(Buy|Sell)\b.*?"  # Action
        r"([\d.]+)\b.*?"  # Entry price
        r"([\d.]+)\b.*?"  # Take profit
        r"([\d.]+)",  # Stop loss
        re.DOTALL | re.IGNORECASE
    )
    
    for match in pattern.findall(page_text):
        try:
            pair, age, from_time, till_time, action, entry, tp, sl = match
            
            # Include date in ID for daily reset
            signal_id = hashlib.md5(
                f"{uk_date}:{pair}:{from_time}:{till_time}".encode()
            ).hexdigest()
            
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
        except Exception as e:
            print(f"⚠️ Error parsing signal: {str(e)}")
            continue
            
    return signals

def load_previous_signals():
    """Load signals from file with daily reset check"""
    try:
        # Check if we should reset signals for new day
        reset_signals = False
        if os.path.exists(SIGNALS_FILE):
            file_mtime = datetime.fromtimestamp(os.path.getmtime(SIGNALS_FILE))
            if file_mtime.date() < datetime.utcnow().date():
                print("♻️ Resetting signal history for new day")
                reset_signals = True
        
        if reset_signals or not os.path.exists(SIGNALS_FILE):
            return {}
        
        with open(SIGNALS_FILE, 'r') as f:
            data = json.load(f)
            return {sig["id"]: sig for sig in data}
    except Exception as e:
        print(f"⚠️ Error loading signals: {str(e)}")
        return {}

def save_signals(signals):
    """Save signals to file"""
    try:
        with open(SIGNALS_FILE, 'w') as f:
            json.dump([sig for sig in signals.values()], f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving signals: {str(e)}")

def format_telegram_message(signal):
    """Create formatted Telegram message"""
    action_emoji = "🟢" if signal["action"].lower() == "buy" else "🔴"
    action_text = "BUY ↗️" if signal["action"].lower() == "buy" else "SELL ↘️"
    
    # UK time formatting
    uk_time = datetime.now(pytz.timezone('Europe/London')).strftime("%Y-%m-%d %H:%M")
    
    message = (
        f"⏰ *{uk_time} UK*\n"
        f"{action_emoji} *NEW FOREX SIGNAL* {action_emoji}\n\n"
        f"📈 *Pair:* {signal.get('pair', 'N/A')}\n"
        f"⏱ *Posted:* {signal.get('posted', 'recently')}\n"
    )
    
    if 'from' in signal and 'till' in signal:
        message += f"🕒 *Active:* {signal['from']} - {signal['till']} UTC+1\n\n"
    
    message += (
        f"⚡ *Action:* {action_text}\n"
        f"💰 *Entry Price:* `{signal.get('entry', 'N/A')}`\n"
        f"🎯 *Take Profit:* `{signal.get('take_profit', 'N/A')}`\n"
        f"🛡 *Stop Loss:* `{signal.get('stop_loss', 'N/A')}`\n\n"
        f"🔔 *Signal ID:* `{signal['id'][:8]}`"
    )
    
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
    start_time = time.time()
    
    # Scrape website
    print("🌐 Scraping website...")
    page_text = scrape_signals()
    
    if not page_text:
        print("❌ Failed to scrape page content")
        # Send alert during market hours
        uk_hour = datetime.now(pytz.timezone('Europe/London')).hour
        if 6 <= uk_hour <= 18:
            send_telegram_message("⚠️ *SCRAPER FAILURE*: Could not retrieve website content")
        return
    
    print(f"💾 Saved page content to {DEBUG_FILE}")
    
    # Extract signals
    print("🔍 Parsing signals...")
    current_signals = extract_signals(page_text)
    
    if not current_signals:
        print("⚠️ No signals found on the page")
        uk_hour = datetime.now(pytz.timezone('Europe/London')).hour
        if 6 <= uk_hour <= 18:  # Market hours
            send_telegram_message("ℹ️ *NO NEW SIGNALS* found during market hours")
        return
    
    print(f"📊 Found {len(current_signals)} signals")
    
    # Load previous signals with daily reset
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
            previous_signals[signal["id"]] = signal
        else:
            print("    ❌ Failed to send signal")
    
    # Save updated signal history
    save_signals(previous_signals)
    print(f"💾 Saved {len(previous_signals)} signals to history file")
    
    # Performance metrics
    duration = time.time() - start_time
    print(f"⏱ Execution time: {duration:.2f} seconds")

if __name__ == "__main__":
    main()
