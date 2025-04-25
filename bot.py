import requests
import time
import random
import logging
import os
import json
import sys
import string
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
import telegram
from telegram.error import TelegramError
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("username_checker.log"),
        logging.StreamHandler()
    ]
)

# Constants for username generation
USERNAME_CHARS = string.ascii_lowercase + string.digits + '_.'
RESULTS_FILE = f"available_usernames_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
CHECKPOINT_FILE = "checkpoint.json"
MAX_WORKERS = 50  # Reduced for better performance
REQUEST_TIMEOUT = 5  # Seconds
DELAY_BETWEEN_REQUESTS = 0.5  # Check one username per second
MAX_RETRIES = 3  # Maximum number of retries for a username
MENTION_USERNAME = "@TheOnly1x"  # Username to mention in Telegram messages

class InstagramUsernameChecker:
    def __init__(self, proxy_file, bot_token, chat_id, use_proxies=True):
        self.proxy_file = proxy_file
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.use_proxies = use_proxies
        
        self.proxies = []
        self.working_proxies = []
        self.failing_proxies = {}  # Track failing proxies with timestamp
        self.proxy_usage_count = {}  # Track usage count for each proxy
        self.proxy_lock = asyncio.Lock()
        self.result_lock = asyncio.Lock()
        self.bot = None
        
        # Stats
        self.successful_checks = 0
        self.failed_checks = 0
        self.proxy_errors = 0
        self.untaken_usernames = []
        
        # Checkpointing
        self.processed_usernames = set()
        self.progress = 0
        
        # User agents
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Safari/605.1.15",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 15_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:95.0) Gecko/20100101 Firefox/95.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.93 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36 Edg/97.0.1072.62",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 OPR/82.0.4227.44",
            "Mozilla/5.0 (iPad; CPU OS 15_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/96.0.4664.116 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:95.0) Gecko/20100101 Firefox/95.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0"
        ]
        
        # Continuous generation parameters
        self.length_distribution = [3, 4, 5]  # Username lengths to generate
        self.current_length_index = 0
        self.used_usernames = set()
        
    async def initialize(self):
        # Reset the stats
        self.reset_state()
        
        # Load proxies if using them
        if self.use_proxies:
            if not os.path.exists(self.proxy_file):
                logging.error(f"Proxy file {self.proxy_file} not found!")
                return False
                
            with open(self.proxy_file, "r") as f:
                self.proxies = [line.strip() for line in f if line.strip()]
                
            if not self.proxies:
                logging.error("No valid proxies found in the proxy file!")
                return False
                
            # Initialize working proxies list
            self.working_proxies = self.proxies.copy()
            logging.info(f"Loaded {len(self.proxies)} proxies")
            
            # Test proxies in parallel to find the working ones
            logging.info("Testing proxies for functionality...")
            await self.test_proxies()
            
            if not self.working_proxies:
                logging.error("No working proxies found! Please check your proxy list.")
                return False
                
            logging.info(f"{len(self.working_proxies)} proxies are working correctly")
        else:
            logging.info("Running without proxies as requested")
        
        # Load checkpoint if it exists
        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, "r") as f:
                    checkpoint_data = json.load(f)
                    self.processed_usernames = set(checkpoint_data.get("processed_usernames", []))
                    self.untaken_usernames = checkpoint_data.get("untaken_usernames", [])
                    self.successful_checks = checkpoint_data.get("successful_checks", 0)
                    self.failed_checks = checkpoint_data.get("failed_checks", 0)
                    self.progress = checkpoint_data.get("progress", 0)
                    self.used_usernames = set(checkpoint_data.get("used_usernames", []))
                    
                logging.info(f"Loaded checkpoint: {len(self.processed_usernames)} usernames already processed")
            except Exception as e:
                logging.error(f"Error loading checkpoint: {e}")
        
        # Initialize Telegram bot
        try:
            self.bot = telegram.Bot(token=self.bot_token)
            await self.bot.get_me()  # Test the connection
            logging.info("Telegram bot initialized successfully")
        except TelegramError as e:
            logging.error(f"Failed to initialize Telegram bot: {e}")
            return False
            
        return True
    
    def reset_state(self):
        """Reset the state of the checker to start fresh"""
        # Remove checkpoint file if it exists
        if os.path.exists(CHECKPOINT_FILE):
            try:
                os.remove(CHECKPOINT_FILE)
                logging.info("Removed checkpoint file for fresh start")
            except Exception as e:
                logging.error(f"Error removing checkpoint file: {e}")
        
        # Reset all counters and collections
        self.processed_usernames = set()
        self.untaken_usernames = []
        self.successful_checks = 0
        self.failed_checks = 0
        self.proxy_errors = 0
        self.progress = 0
        self.used_usernames = set()
        self.failing_proxies = {}
        self.proxy_usage_count = {}
        
        logging.info("Reset complete - starting with fresh state")
        
    async def test_proxies(self):
        """Test all proxies in parallel to find working ones"""
        test_url = "https://www.instagram.com/"
        test_tasks = []
        
        async def test_proxy(proxy_str):
            try:
                connector = self._create_connector(proxy_str)
                if not connector:
                    return False
                
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    headers = {"User-Agent": random.choice(self.user_agents)}
                    async with session.get(test_url, headers=headers) as response:
                        return 200 <= response.status < 300
            except Exception:
                return False
        
        # Create tasks for testing each proxy
        for proxy in self.proxies:
            task = asyncio.create_task(test_proxy(proxy))
            test_tasks.append((proxy, task))
        
        # Process results
        self.working_proxies = []
        for proxy, task in test_tasks:
            try:
                is_working = await task
                if is_working:
                    self.working_proxies.append(proxy)
            except Exception:
                pass
                
    def _create_connector(self, proxy_str):
        """Create a proxy connector for aiohttp from a proxy string"""
        try:
            # Handle different proxy formats
            if "://" in proxy_str:
                proxy_type, proxy_addr = proxy_str.split("://", 1)
                if proxy_type.lower() == "http":
                    return aiohttp.TCPConnector(ssl=False)  # For HTTP proxies, we'll set the proxy in the request
                elif proxy_type.lower() in ["socks4", "socks5"]:
                    return ProxyConnector.from_url(proxy_str)
            else:
                # Default to http if no protocol specified
                return aiohttp.TCPConnector(ssl=False)
        except Exception as e:
            logging.error(f"Error creating connector for proxy {proxy_str}: {e}")
            return None
        
    async def get_working_proxy(self):
        """Get a working proxy with proper rotation or None if not using proxies"""
        if not self.use_proxies:
            return None, aiohttp.TCPConnector(ssl=False)
            
        async with self.proxy_lock:
            # If no working proxies, try to recover some from the failing list
            if not self.working_proxies:
                now = time.time()
                recovered = []
                for proxy, timestamp in list(self.failing_proxies.items()):
                    # If proxy has been in failing state for more than 5 minutes, try to recover
                    if now - timestamp > 300:
                        recovered.append(proxy)
                        del self.failing_proxies[proxy]
                
                if recovered:
                    self.working_proxies.extend(recovered)
                    logging.info(f"Recovered {len(recovered)} proxies from failing state")
                else:
                    # No proxies available at all
                    logging.critical("No working proxies available! Attempting to continue with original list.")
                    self.working_proxies = self.proxies.copy()
            
            if not self.working_proxies:
                return None, None
                
            # Select a random proxy from working list for better distribution
            proxy_str = random.choice(self.working_proxies)
            
            # Update usage count
            self.proxy_usage_count[proxy_str] = self.proxy_usage_count.get(proxy_str, 0) + 1
            
            # Format proxy for aiohttp
            connector = self._create_connector(proxy_str)
            return proxy_str, connector
    
    async def mark_proxy_as_failing(self, proxy_str):
        """Mark a proxy as failing and remove it from working proxies"""
        if not proxy_str or not self.use_proxies:
            return
            
        async with self.proxy_lock:
            if proxy_str in self.working_proxies:
                self.working_proxies.remove(proxy_str)
                self.failing_proxies[proxy_str] = time.time()
                self.proxy_errors += 1
                logging.warning(f"Marked proxy {proxy_str} as failing. {len(self.working_proxies)} working proxies remaining.")
    
    async def check_username(self, username):
        """Check if a username is available on Instagram using proxies or direct connection."""
        username = username.strip()
        if not username:
            return None
            
        # Skip if already processed
        if username in self.processed_usernames:
            return None
            
        url = f"https://www.instagram.com/{username}/"
        
        headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "Referer": "https://www.instagram.com/",
            "cookie": ""  # Empty cookie to appear more like a browser
        }
        
        for attempt in range(MAX_RETRIES):
            try:
                proxy_str, connector = await self.get_working_proxy()
                if not connector:
                    await asyncio.sleep(random.uniform(1, 2))
                    continue
                    
                # Create a new session for this request
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                
                proxy_dict = None
                if self.use_proxies and "://" in proxy_str and proxy_str.split("://")[0].lower() == "http":
                    # For HTTP proxies
                    proxy_dict = proxy_str
                    
                # Allow redirects to properly handle 302 responses
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    # First try with allow_redirects=False to see the initial response
                    async with session.get(url, headers=headers, proxy=proxy_dict, allow_redirects=False) as response:
                        status = response.status
                        
                        # If we get a redirect, we should check the redirect location
                        if status in [301, 302, 303, 307, 308]:
                            location = response.headers.get('Location', '')
                            
                            # If redirected to login page, the username likely exists
                            if 'login' in location or 'accounts/login' in location:
                                async with self.result_lock:
                                    self.successful_checks += 1
                                    self.processed_usernames.add(username)
                                logging.info(f"Taken - ({username}) [Redirect to login]")
                                return ("taken", username)
                            
                            # Follow the redirect to see final destination
                            async with session.get(url, headers=headers, proxy=proxy_dict, allow_redirects=True) as redirect_response:
                                final_url = str(redirect_response.url)
                                final_status = redirect_response.status
                                
                                # Check if we landed on the username page (username exists)
                                if final_status == 200 and f"/{username}" in final_url:
                                    async with self.result_lock:
                                        self.successful_checks += 1
                                        self.processed_usernames.add(username)
                                    logging.info(f"Taken - ({username}) [After redirect]")
                                    return ("taken", username)
                                # Check if we ended up on a 404 page (username doesn't exist)
                                elif final_status == 404:
                                    async with self.result_lock:
                                        self.successful_checks += 1
                                        self.untaken_usernames.append(username)
                                        self.processed_usernames.add(username)
                                    logging.info(f"Untaken - {username}")
                                    
                                    # Send as tap-to-copy format without @ symbol
                                    await self.send_telegram_message(f"`{username}` (tap to copy) {MENTION_USERNAME}")
                                    return ("untaken", username)
                                # Other status codes after redirect
                                else:
                                    logging.info(f"Ambiguous result for {username}: Redirected to {final_url} with status {final_status}")
                                    if attempt == MAX_RETRIES - 1:
                                        # Consider it taken by default if we can't determine clearly
                                        async with self.result_lock:
                                            self.successful_checks += 1
                                            self.processed_usernames.add(username)
                                        return ("taken", f"{username} - Ambiguous redirect")
                        # Direct 200 response - username exists
                        elif status == 200:
                            async with self.result_lock:
                                self.successful_checks += 1
                                self.processed_usernames.add(username)
                            logging.info(f"Taken - ({username})")
                            return ("taken", username)
                        # Direct 404 response - username doesn't exist
                        elif status == 404:
                            async with self.result_lock:
                                self.successful_checks += 1
                                self.untaken_usernames.append(username)
                                self.processed_usernames.add(username)
                            logging.info(f"Untaken - {username}")
                            
                            # Send as tap-to-copy format without @ symbol
                            await self.send_telegram_message(f"`{username}` (tap to copy) {MENTION_USERNAME}")
                            return ("untaken", username)
                        # Rate limiting responses
                        elif status in [429, 403]:
                            await self.mark_proxy_as_failing(proxy_str)
                            if attempt == MAX_RETRIES - 1:
                                async with self.result_lock:
                                    self.failed_checks += 1
                                    self.processed_usernames.add(username)
                                return ("error", f"{username} - Rate limited")
                        else:
                            logging.warning(f"Unexpected status code {status} for {username} (attempt {attempt+1}/{MAX_RETRIES})")
                            if attempt == MAX_RETRIES - 1:
                                async with self.result_lock:
                                    self.failed_checks += 1
                                    self.processed_usernames.add(username)
                                return ("error", f"{username} - Status code: {status}")
                        
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if proxy_str:
                    await self.mark_proxy_as_failing(proxy_str)
                    
                logging.warning(f"Request error for {username}: {str(e)} (attempt {attempt+1}/{MAX_RETRIES})")
                if attempt == MAX_RETRIES - 1:
                    async with self.result_lock:
                        self.failed_checks += 1
                        self.processed_usernames.add(username)
                    return ("error", f"{username} - {str(e)}")
            except Exception as e:
                logging.error(f"Unexpected error checking {username}: {str(e)}")
                if attempt == MAX_RETRIES - 1:
                    async with self.result_lock:
                        self.failed_checks += 1
                        self.processed_usernames.add(username)
                    return ("error", f"{username} - Unexpected error")
                    
            # Wait before retrying with exponential backoff but keep it reasonable
            await asyncio.sleep(random.uniform(0.5, 1))
                
        return None
    
    async def save_checkpoint(self):
        """Save current progress to checkpoint file"""
        checkpoint_data = {
            "processed_usernames": list(self.processed_usernames),
            "untaken_usernames": self.untaken_usernames,
            "successful_checks": self.successful_checks,
            "failed_checks": self.failed_checks,
            "progress": self.progress,
            "used_usernames": list(self.used_usernames),
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump(checkpoint_data, f)
            logging.debug("Checkpoint saved")
        except Exception as e:
            logging.error(f"Error saving checkpoint: {e}")
    
    async def send_telegram_message(self, message):
        """Send a message via Telegram bot."""
        if not self.bot:
            logging.error("Telegram bot not initialized!")
            return False
            
        try:
            # Use Markdown parse_mode for the monospace formatting with backticks
            await self.bot.send_message(chat_id=self.chat_id, text=message, parse_mode="Markdown")
            return True
        except TelegramError as e:
            logging.error(f"Failed to send Telegram message: {e}")
            return False
    
    def generate_username(self, length):
        """Generate a single username of specified length that hasn't been used yet"""
        max_attempts = 100  # Prevent infinite loop
        
        for _ in range(max_attempts):
            # Generate a random username of specified length
            chars = [random.choice(USERNAME_CHARS) for _ in range(length)]
            username = ''.join(chars)
            
            # Apply filtering rules and check if already used
            if ('..' not in username and 
                not username.endswith('.') and 
                not username.startswith('.') and
                username not in self.used_usernames):
                
                # Mark as used and return
                self.used_usernames.add(username)
                return username
                
        # If we couldn't generate a unique username after max attempts, try with a different length
        return None
        
    async def continuous_check(self):
        """Continuously generate and check usernames one by one"""
        # Initialize statistics and counters
        total_checked = 0
        last_checkpoint_time = time.time()
        checkpoint_interval = 60  # Save checkpoint every 60 seconds
        
        try:
            logging.info("Starting continuous username checking...")
            await self.send_telegram_message("🔍 Starting Instagram username checker. Valid usernames will be sent as they're found.")
            
            while True:
                # Cycle through username lengths (3, 4, 5)
                current_length = self.length_distribution[self.current_length_index]
                self.current_length_index = (self.current_length_index + 1) % len(self.length_distribution)
                
                # Generate a unique username
                username = self.generate_username(current_length)
                
                if username:
                    # Check if the username is available
                    result = await self.check_username(username)
                    total_checked += 1
                    
                    # Wait between requests
                    await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
                    
                    # Log progress periodically
                    if total_checked % 10 == 0:
                        if self.use_proxies:
                            logging.info(
                                f"Progress: Checked {self.successful_checks + self.failed_checks} usernames | "
                                f"Found {len(self.untaken_usernames)} untaken | "
                                f"Working proxies: {len(self.working_proxies)}/{len(self.proxies)}"
                            )
                        else:
                            logging.info(
                                f"Progress: Checked {self.successful_checks + self.failed_checks} usernames | "
                                f"Found {len(self.untaken_usernames)} untaken | "
                                f"Running without proxies"
                            )
                    
                    # Save checkpoint periodically
                    current_time = time.time()
                    if current_time - last_checkpoint_time > checkpoint_interval:
                        await self.save_checkpoint()
                        last_checkpoint_time = current_time
                        
                else:
                    # If we couldn't generate a unique username, wait a bit and try again
                    await asyncio.sleep(0.1)
                    
        except KeyboardInterrupt:
            logging.info("Process interrupted. Saving final checkpoint...")
            await self.save_checkpoint()
            return False
        except Exception as e:
            logging.error(f"Unexpected error in continuous checking: {e}")
            await self.save_checkpoint()
            return False

    async def run(self):
        """Main function to run the username checker."""
        logging.info("Starting continuous username checking process...")
        
        # Send initial Telegram message
        proxy_status = "with proxies" if self.use_proxies else "without proxies"
        await self.send_telegram_message(f"📱 Instagram Username Checker Started ({proxy_status})\nValid usernames will be sent.\n{MENTION_USERNAME}")
        
        # Start continuous checking
        await self.continuous_check()
        
        # Save final checkpoint
        await self.save_checkpoint()
        
        logging.info("Username checking process completed successfully")
        return True

async def main():
    print("\n===== Instagram 3l | 4l | 5l Username Checker =====")
    
    # Ask if the user wants to use proxies
    use_proxies_input = input("Do you want to use proxies? (y/n): ").strip().lower()
    use_proxies = use_proxies_input.startswith('y')
    
    # Get user input
    proxy_file = ""
    if use_proxies:
        proxy_file = input("Enter path to proxies file: ").strip()
        if not proxy_file or not os.path.exists(proxy_file):
            print(f"Error: Proxy file '{proxy_file}' not found.")
            return
    
    bot_token = input("Enter Telegram bot token: ").strip()
    chat_id = input("Enter Telegram chat ID: ").strip()
    
    # Validate inputs
    if not bot_token or not chat_id:
        print("Error: Bot token and chat ID are required.")
        return
    
    # Reset option
    reset_state = input("Reset state and start fresh? (y/n): ").strip().lower()
    reset = reset_state.startswith('y')
    
    print("\nInitializing username checker...")
    print("This will continuously generate and check Instagram usernames of lengths 3, 4, and 5.")
    print("Available usernames will be sent to Telegram in 'tap to copy' format.")
    print("Press Ctrl+C to stop the process at any time.\n")
    
    # Loop to allow restarting after stops
    while True:
        try:
            checker = InstagramUsernameChecker(proxy_file, bot_token, chat_id, use_proxies)
            if await checker.initialize():
                if reset:
                    # Reset will happen in initialize
                    pass
                print("\nStarting continuous username checking process...\n")
                await checker.run()
            else:
                print("\nFailed to initialize the username checker. Check the logs for details.")
                break
                
            # Ask if the user wants to restart
            restart = input("\nDo you want to restart the checker? (y/n): ").strip().lower()
            if not restart.startswith('y'):
                print("Exiting username checker.")
                break
                
            # If restarting, ask if we should reset state
            reset_state = input("Reset state for the new run? (y/n): ").strip().lower()
            reset = reset_state.startswith('y')
            
        except KeyboardInterrupt:
            print("\nProcess interrupted by user.")
            restart = input("\nDo you want to restart the checker? (y/n): ").strip().lower()
            if not restart.startswith('y'):
                print("Exiting username checker.")
                break
                
            # If restarting, ask if we should reset state
            reset_state = input("Reset state for the new run? (y/n): ").strip().lower()
            reset = reset_state.startswith('y')

if __name__ == "__main__":
    asyncio.run(main())