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
DEBUG_DIR = "debug"  # Directory for debug files

# Create debug directory if it doesn't exist
if not os.path.exists(DEBUG_DIR):
    os.makedirs(DEBUG_DIR)

def setup_selenium():
    """Configure headless Chrome browser with retry logic"""
    print("🛠 Setting up Selenium...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    # Add retry logic for browser setup
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"  Attempt {attempt+1} to initialize Chrome...")
            driver = webdriver.Chrome(options=options)
            print("✅ Chrome initialized successfully")
            return driver
        except Exception as e:
            print(f"    ❌ Chrome init attempt {attempt+1} failed: {str(e)}")
            if attempt == max_retries - 1:
                raise
            time.sleep(5)

def scrape_signals():
    """Scrape forex signals from website with retry logic"""
    print("🌐 Starting website scraping...")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            driver = setup_selenium()
            print(f"  Navigating to https://live-forex-signals.com/en/...")
            driver.get("https://live-forex-signals.com/en/")
            
            # Save initial page for debugging
            initial_html = driver.page_source
            with open(os.path.join(DEBUG_DIR, f"initial_page_{attempt}.html"), "w", encoding="utf-8") as f:
                f.write(initial_html)
            
            # Wait for dynamic content with multiple checks
            signal_found = False
            for i in range(5):
                wait_time = 3 + i*2  # Progressive wait: 3,5,7,9,11 seconds
                print(f"  Waiting {wait_time} seconds for content to load (check #{i+1})...")
                time.sleep(wait_time)
                
                # Get current page state
                page_text = driver.find_element(By.TAG_NAME, "body").text
                current_html = driver.page_source
                
                # Save page state for debugging
                with open(os.path.join(DEBUG_DIR, f"page_check_{attempt}_{i}.html"), "w", encoding="utf-8") as f:
                    f.write(current_html)
                driver.save_screenshot(os.path.join(DEBUG_DIR, f"screenshot_{attempt}_{i}.png"))
                
                print(f"  Check #{i+1}: Page text length: {len(page_text)} characters")
                print(f"  Check #{i+1}: Contains 'signal': {'signal' in page_text.lower()}")
                print(f"  Check #{i+1}: Contains 'forex': {'forex' in page_text.lower()}")
                
                if "signal" in page_text.lower():
                    print("✅ Found 'signal' keyword in page text")
                    signal_found = True
                    return page_text
            
            # If we got here, signals weren't found
            if not signal_found:
                print("⚠️ 'signal' keyword not found in any page check")
                # Save final page state for debugging
                with open(os.path.join(DEBUG_DIR, "final_page_no_signal.html"), "w", encoding="utf-8") as f:
                    f.write(current_html)
                driver.save_screenshot(os.path.join(DEBUG_DIR, "final_screenshot_no_signal.png"))
                
                # Print sample of page text for debugging
                print("\n=== PAGE TEXT SAMPLE (First 1000 chars) ===")
                print(page_text[:1000])
                print("===========================================")
                
                raise ValueError("Signal content not found on page")
            
        except Exception as e:
            print(f"⛔ Scrape attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_retries - 1:
                print("🔥 All scrape attempts failed")
                raise
            print("🔄 Retrying after 10 seconds...")
            time.sleep(10)
        finally:
            if 'driver' in locals():
                print("🛑 Quitting browser...")
                driver.quit()

def normalize_time_text(text):
    """Normalize time-related text to prevent duplicates from minor wording changes"""
    replacements = {
        r'\b\d+\s*minutes?\b': 'minutes ago',
        r'\b\d+\s*hours?\b': 'hours ago',
        r'\b\d+\s*days?\b': 'days ago',
        r'\bjust now\b': '0 hours ago',
        r'\bminute\b': 'minute ago',
        r'\bhour\b': 'hour ago',
        r'\bday\b': 'day ago',
        r'\s+ago\s+ago': ' ago'  # Fix double "ago"
    }
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

def extract_signals(page_text):
    """Parse signals from page text using flexible regex patterns"""
    print("🔍 Starting signal extraction...")
    
    # Save raw text for debugging
    with open(os.path.join(DEBUG_DIR, "raw_page_text.txt"), "w", encoding="utf-8") as f:
        f.write(page_text)
    
    # Normalize the text first
    normalized_text = normalize_time_text(page_text)
    
    # Save normalized text for debugging
    with open(os.path.join(DEBUG_DIR, "normalized_text.txt"), "w", encoding="utf-8") as f:
        f.write(normalized_text)
    
    # More flexible pattern to handle website changes
    patterns = [
        # New pattern based on the debug output
        r"([A-Z]{3}/[A-Z]{3})\s+signal\s+([^\n]+?)\s*From\s*UTC[^\s]*\s*([\d:]+)\s*Till\s*UTC[^\s]*\s*([\d:]+)\s*(Buy|Sell)\s+.*?at\s+([\d.]+)\s+Take profit\*?\s*at\s+([\d.]+)\s+Stop loss\s*at\s+([\d.]+)",
        
        # More generic pattern that matches the sample text
        r"([A-Z]{3}/[A-Z]{3})\s+signal\s+([^\n]+?)\s*From\s*([^\n]+?)\s*Till\s*([^\n]+?)\s*(Buy|Sell)\s+.*?at\s+([\d.]+)\s+Take profit[^\d]*([\d.]+)\s+Stop loss[^\d]*([\d.]+)",
        
        # Fallback pattern with more flexibility
        r"([A-Z]{3}/[A-Z]{3})\s+.*?signal\s+([^\n]+?)\s*[^\n]*?From[^\n]*?([\d:]+)[^\n]*?Till[^\n]*?([\d:]+)[^\n]*?(Buy|Sell)[^\n]*?at\s+([\d.]+)[^\n]*?Take profit[^\d]*([\d.]+)[^\n]*?Stop loss[^\d]*([\d.]+)"
    ]

    signals = []
    pattern_used = None
    
    for idx, pattern in enumerate(patterns):
        print(f"  Trying pattern #{idx+1}...")
        matches = re.findall(pattern, normalized_text, re.DOTALL)
        print(f"    Found {len(matches)} matches with pattern #{idx+1}")
        
        if matches:
            pattern_used = idx + 1
            for match_idx, match in enumerate(matches):
                if len(match) == 8:
                    pair, age, from_time, till_time, action, entry, tp, sl = match
                else:
                    # Skip invalid matches
                    print(f"    ⚠️ Match #{match_idx+1} has invalid length: {len(match)}")
                    continue
                
                # Clean up the time strings
                from_time = re.sub(r"UTC[^\s]*\s*", "", from_time).strip()
                till_time = re.sub(r"UTC[^\s]*\s*", "", till_time).strip()
                
                # Create consistent ID
                signal_id = hashlib.md5(f"{pair}{from_time}{till_time}{entry}".encode()).hexdigest()
                
                signals.append({
                    "id": signal_id,
                    "pair": pair,
                    "posted": normalize_time_text(age.strip()),
                    "from": from_time,
                    "till": till_time,
                    "action": action.strip(),
                    "entry": entry.strip(),
                    "take_profit": tp.strip(),
                    "stop_loss": sl.strip(),
                    "timestamp": datetime.utcnow().isoformat(),
                    "pattern": pattern_used  # For debugging
                })
                print(f"    ✅ Extracted signal #{match_idx+1}: {pair} {action} at {entry}")
            print(f"✅ Using pattern #{pattern_used}")
            break  # Stop after first successful pattern
        else:
            # Print why pattern didn't match
            print(f"    Pattern #{idx+1} failed to match")
    
    if not signals:
        print("⚠️ No patterns matched any signals")
        # Print sample of normalized text around "signal" keyword
        signal_positions = [m.start() for m in re.finditer(r'signal', normalized_text, re.IGNORECASE)]
        if signal_positions:
            print("\n=== TEXT AROUND 'signal' KEYWORD ===")
            for pos in signal_positions[:3]:  # Show first 3 occurrences
                start = max(0, pos - 100)
                end = min(len(normalized_text), pos + 200)
                print(normalized_text[start:end])
                print("-----------------------------------------")
        else:
            print("ℹ️ 'signal' keyword not found in normalized text")
    
    return signals

def load_previous_signals():
    """Load and cleanup old signals"""
    print("📂 Loading previous signals...")
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, 'r') as f:
                data = json.load(f)
                print(f"  Found {len(data)} signals in history file")
                
                # Convert to dict if needed
                if isinstance(data, list):
                    data = {sig["id"]: sig for sig in data}
                    print("  Converted list format to dictionary")
                
                # Cleanup old signals
                now = datetime.utcnow()
                cleaned_data = {}
                for sig_id, signal in data.items():
                    try:
                        signal_time = datetime.fromisoformat(signal["timestamp"])
                        if (now - signal_time) < timedelta(hours=CLEANUP_HOURS):
                            cleaned_data[sig_id] = signal
                    except KeyError:
                        print(f"⚠️ Signal {sig_id} missing timestamp, keeping anyway")
                        cleaned_data[sig_id] = signal
                
                # Save cleaned data if changes were made
                if len(cleaned_data) != len(data):
                    print(f"  Removing {len(data) - len(cleaned_data)} expired signals")
                    with open(SIGNALS_FILE, 'w') as f:
                        json.dump(cleaned_data, f, indent=2)
                
                print(f"  Returning {len(cleaned_data)} valid signals")
                return cleaned_data
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"⚠️ Error loading signal history: {str(e)}")
            return {}
    print("ℹ️ No existing signal file found")
    return {}

def save_signals(signals):
    """Save signals with cleanup"""
    print("💾 Saving signals...")
    existing = load_previous_signals()
    updated = {sig["id"]: sig for sig in signals}
    
    # Merge with existing signals
    merged = {**existing, **updated}
    
    # Cleanup old signals during save
    now = datetime.utcnow()
    cleaned = {}
    for sig_id, sig in merged.items():
        try:
            sig_time = datetime.fromisoformat(sig["timestamp"])
            if (now - sig_time) < timedelta(hours=CLEANUP_HOURS):
                cleaned[sig_id] = sig
        except KeyError:
            print(f"⚠️ Signal {sig_id} missing timestamp, keeping anyway")
            cleaned[sig_id] = sig
    
    print(f"  Saving {len(cleaned)} signals ({len(signals)} new)")
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
    print("📤 Attempting to send Telegram message...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"  Sending attempt {attempt+1}...")
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            print("✅ Message sent successfully")
            return True
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                print(f"❌ Final attempt failed: {e}")
                return False
            print(f"⚠️ Attempt {attempt+1} failed, retrying in 5 seconds...")
            time.sleep(5)

def main():
    print("\n" + "="*50)
    print("🚀 STARTING FOREX SIGNAL SCRAPER")
    print("="*50)
    print(f"🕒 Current UTC time: {datetime.utcnow().isoformat()}")
    print(f"🔧 Debug files will be saved to: {os.path.abspath(DEBUG_DIR)}")

    try:
        # Scrape website
        print("\n" + "-"*50)
        print("🌐 BEGINNING WEBSITE SCRAPING")
        print("-"*50)
        page_text = scrape_signals()
        print(f"✅ Scraping complete. Page text length: {len(page_text)} characters")

        # Extract signals
        print("\n" + "-"*50)
        print("🔍 EXTRACTING SIGNALS FROM PAGE TEXT")
        print("-"*50)
        current_signals = extract_signals(page_text)

        if not current_signals:
            print("⚠️ No signals extracted from page text")
            return

        print(f"📊 Found {len(current_signals)} signals in page text")

        # Load previous signals
        print("\n" + "-"*50)
        print("📚 LOADING PREVIOUS SIGNALS")
        print("-"*50)
        previous_signals = load_previous_signals()
        previous_ids = set(previous_signals.keys())
        print(f"ℹ️ {len(previous_ids)} signals in history")

        # Find new signals
        new_signals = [
            sig for sig in current_signals
            if sig["id"] not in previous_ids
        ]

        print(f"✨ Found {len(new_signals)} new signals")

        # Process new signals
        if new_signals:
            print("\n" + "-"*50)
            print("💌 PROCESSING NEW SIGNALS")
            print("-"*50)
            for signal in new_signals:
                print(f"  Processing signal {signal['id'][:8]} for {signal['pair']}")
                print(f"    Action: {signal['action']}, Entry: {signal['entry']}")
                print(f"    Pattern used: {signal.get('pattern', 'N/A')}")
                message = format_telegram_message(signal)

                if send_telegram_message(message):
                    print("    📤 Signal sent to Telegram")
                    # Update previous signals only after successful send
                    previous_signals[signal["id"]] = signal
                else:
                    print("    ❌ Failed to send signal")
        else:
            print("ℹ️ No new signals to send")

        # Save updated signal history
        print("\n" + "-"*50)
        print("💾 SAVING SIGNAL HISTORY")
        print("-"*50)
        save_signals(list(previous_signals.values()))
        print("✅ Signal history saved")

    except Exception as e:
        print(f"\n🔥 CRITICAL ERROR: {str(e)}")
        # Send error notification to Telegram
        error_msg = f"🚨 FOREX SIGNAL SCRAPER FAILED 🚨\nError: {str(e)}\nUTC Time: {datetime.utcnow().isoformat()}"
        send_telegram_message(error_msg)
        raise  # Re-raise for GitHub Actions to catch
    finally:
        print("\n" + "="*50)
        print("🏁 SCRAPER RUN COMPLETE")
        print("="*50)

if __name__ == "__main__":
    main()
