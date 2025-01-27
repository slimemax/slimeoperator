import os
import time
import json
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from dotenv import load_dotenv
from colorama import Fore, Style, init
import random
import traceback
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoAlertPresentException

load_dotenv()
init(autoreset=True)  # Initialize colorama

class BrowserAutomator:
    def __init__(self):
        self.driver = None
        self.current_goal = None
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.running = False
        self.action_count = 0
        self.start_time = time.time()
        self.action_history = []
        self.debug_log = []
        self.start_browser()

    def start_browser(self):
        firefox_service = Service()
        self.driver = webdriver.Firefox(service=firefox_service)
        self.driver.implicitly_wait(5)  # Reduced from 10
        
        try:
            # Verify clean startup
            self.log_debug("Starting browser and verifying initialization...", "INFO")
            self.driver.get('https://www.google.com')
            
            # Wait for page to be fully loaded
            WebDriverWait(self.driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            # Verify basic page elements are present
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, 'body'))
            )
            
            self.log_debug("Browser initialized successfully", "INFO")
        except Exception as e:
            self.log_debug(f"Browser initialization failed: {str(e)}", "ERROR")
            raise

    def scan_page_elements(self):
        """Scan current page for interactive elements"""
        try:
            elements_info = {
                "buttons": [],
                "links": [],
                "inputs": [],
                "text": []
            }
            
            # Scan for buttons
            buttons = self.driver.find_elements(By.TAG_NAME, "button")
            for button in buttons:
                try:
                    info = {
                        "text": button.text.strip(),
                        "xpath": self.driver.execute_script("""
                            function getXPath(element) {
                                if (element.id !== '')
                                    return `//*[@id="${element.id}"]`;
                                if (element === document.body)
                                    return element.tagName.toLowerCase();
                                var ix = 0;
                                var siblings = element.parentNode.childNodes;
                                for (var i = 0; i < siblings.length; i++) {
                                    var sibling = siblings[i];
                                    if (sibling === element)
                                        return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                                    if (sibling.nodeType === 1 && sibling.tagName === element.tagName)
                                        ix++;
                                }
                            }
                            return getXPath(arguments[0]);
                        """, button),
                        "visible": button.is_displayed()
                    }
                    if info["text"]:  # Only add if button has text
                        elements_info["buttons"].append(info)
                except:
                    continue

            # Scan for links
            links = self.driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                try:
                    info = {
                        "text": link.text.strip(),
                        "href": link.get_attribute("href"),
                        "visible": link.is_displayed()
                    }
                    if info["text"]:  # Only add if link has text
                        elements_info["links"].append(info)
                except:
                    continue

            # Scan for inputs
            inputs = self.driver.find_elements(By.TAG_NAME, "input")
            for input_elem in inputs:
                try:
                    info = {
                        "type": input_elem.get_attribute("type"),
                        "name": input_elem.get_attribute("name"),
                        "placeholder": input_elem.get_attribute("placeholder"),
                        "visible": input_elem.is_displayed()
                    }
                    if any([info["name"], info["placeholder"]]):  # Only add if input has identifiable info
                        elements_info["inputs"].append(info)
                except:
                    continue

            # Get main visible text
            try:
                main_text = self.driver.find_element(By.TAG_NAME, "body").text
                elements_info["text"] = main_text[:500]  # First 500 chars
            except:
                elements_info["text"] = ""

            self.log_debug(f"Found {len(elements_info['buttons'])} buttons, {len(elements_info['links'])} links, {len(elements_info['inputs'])} inputs", "DEBUG")
            return elements_info

        except Exception as e:
            self.log_debug(f"Error scanning page elements: {str(e)}", "ERROR")
            return None

    def get_browser_state(self):
        """Get enhanced browser state including page elements"""
        state = {
            "current_url": self.driver.current_url,
            "page_title": self.driver.title,
            "goal": self.current_goal,
            "elements": self.scan_page_elements()
        }
        return state

    def log_debug(self, message, level="INFO"):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        elapsed = int(time.time() - self.start_time)
        log_entry = f"[{timestamp}] [{elapsed}s] [{level}] {message}"
        self.debug_log.append(log_entry)
        
        color = {
            "INFO": Fore.BLUE,
            "ACTION": Fore.GREEN,
            "WARNING": Fore.YELLOW,
            "ERROR": Fore.RED,
            "DEBUG": Fore.MAGENTA
        }.get(level, Fore.WHITE)
        
        print(color + log_entry)

    def save_debug_log(self):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"slimeoperator_log_{timestamp}.txt"
        
        with open(filename, 'w') as f:
            f.write("=== SLIMEOPERATOR DEBUG LOG ===\n")
            f.write(f"Session started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.start_time))}\n")
            f.write(f"Total actions: {self.action_count}\n")
            f.write(f"Current goal: {self.current_goal}\n\n")
            f.write("=== ACTION HISTORY ===\n")
            for action in self.action_history:
                f.write(json.dumps(action, indent=2) + "\n")
            f.write("\n=== DETAILED LOG ===\n")
            f.write("\n".join(self.debug_log))
        
        self.log_debug(f"Debug log saved to {filename}", "INFO")

    def print_help(self):
        help_text = f"""
{Fore.GREEN}=== SLIMEOPERATOR COMMANDS ===
{Fore.CYAN}new{Fore.WHITE}  - Set a new directive/goal
{Fore.CYAN}exit{Fore.WHITE} - Terminate the session
{Fore.CYAN}save{Fore.WHITE} - Save debug log to file
{Fore.CYAN}help{Fore.WHITE} - Show this help message
{Fore.CYAN}info{Fore.WHITE} - Show current session info

{Fore.GREEN}=== SESSION INFO ===
{Fore.WHITE}Runtime: {int(time.time() - self.start_time)}s
Actions: {self.action_count}
Current Goal: {self.current_goal}
"""
        print(help_text)

    def handle_popups(self):
        """Quick popup check with minimal timeouts"""
        self.log_debug("Quick popup check", "DEBUG")
        try:
            # Much shorter timeout
            wait = WebDriverWait(self.driver, 1)
            common_popups = [
                "//button[contains(text(), 'No thanks')]",
                "//button[contains(text(), 'Reject all')]",
                "//button[contains(text(), 'Close')]"
            ]
            for xpath in common_popups:
                try:
                    element = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                    element.click()
                    time.sleep(0.5)
                except:
                    continue
        except:
            pass

    def verify_page_ready(self):
        """Verify page is in a stable state before actions"""
        try:
            # Wait for page load to complete
            WebDriverWait(self.driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            # Wait for any animations to finish
            time.sleep(0.5)
            
            # Check if page is responsive
            self.driver.execute_script("return window.location.href")
            
            return True
        except Exception as e:
            self.log_debug(f"Page not ready: {str(e)}", "WARNING")
            return False

    def execute_action(self, action):
        try:
            # First check if browser is alive
            try:
                _ = self.driver.current_url
            except Exception as e:
                self.log_debug(f"Browser connection lost: {str(e)}", "WARNING")
                self.start_browser()
                time.sleep(2)
                return False

            self.action_history.append({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "action": action,
                "elapsed_time": int(time.time() - self.start_time)
            })
            
            action_type = action['action']
            self.log_debug(f"Executing action: {action_type}", "ACTION")
            self.log_debug(f"Current URL: {self.driver.current_url}", "DEBUG")
            self.log_debug(f"Page Title: {self.driver.title}", "DEBUG")

            # Verify page is ready for interaction
            if not self.verify_page_ready():
                self.log_debug("Page not ready for interaction, retrying...", "WARNING")
                time.sleep(2)
                if not self.verify_page_ready():
                    raise Exception("Page failed to stabilize")

            # Handle any popups first
            self.handle_popups()

            # Add random delay before action
            time.sleep(random.uniform(0.5, 1.5))
            
            if action_type == "click":
                base_xpath = action.get('xpath', '')
                safe_text = "".join([c for c in base_xpath.split("/")[-1] if c.isalnum() or c in "_- "]).strip()
                element_id = base_xpath.split("@id")[-1].replace("'","").replace('"','') if "@id" in base_xpath else ""
                
                possible_xpaths = [
                    base_xpath,
                    f"//*[contains(text(), '{safe_text}')]",
                    f"//*[contains(@aria-label, '{safe_text}')]",
                    f"//*[contains(@id, '{element_id}')]",
                    f"//button[contains(text(), '{safe_text}')]",
                    f"//a[contains(text(), '{safe_text}')]"
                ]
                
                self.log_debug(f"Trying XPaths: {possible_xpaths}", "DEBUG")
                
                element = None
                for xpath in possible_xpaths:
                    try:
                        wait = WebDriverWait(self.driver, 3)
                        element = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                        if element:
                            self.log_debug(f"Found clickable element with XPath: {xpath}", "DEBUG")
                            break
                    except Exception as e:
                        self.log_debug(f"XPath failed: {xpath} - {str(e)}", "DEBUG")
                        continue
                
                if not element:
                    raise Exception("No clickable element found with any XPath")
                    
                # Scroll into view with random offset
                offset = random.randint(-100, 100)
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'}); window.scrollBy(0, arguments[1]);", 
                    element, 
                    offset
                )
                time.sleep(random.uniform(0.3, 0.7))
                
                # Try JavaScript click if regular click fails
                try:
                    element.click()
                except:
                    self.driver.execute_script("arguments[0].click();", element)

            elif action_type == "type":
                wait = WebDriverWait(self.driver, 10)
                element = wait.until(EC.visibility_of_element_located((By.XPATH, action['xpath'])))
                element.clear()
                for char in action['text']:
                    element.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.3))
                time.sleep(random.uniform(0.5, 1.0))
                element.send_keys(webdriver.Keys.RETURN)

            elif action_type == "navigate":
                target_url = action['url']
                
                # Ensure proper Hipcamp URL format
                if 'hipcamp.com' in target_url and '/en-US' not in target_url:
                    target_url = target_url.replace('hipcamp.com', 'hipcamp.com/en-US')
                
                self.log_debug(f"Navigating to: {target_url}", "DEBUG")
                
                try:
                    self.driver.get(target_url)
                    time.sleep(3)  # Wait longer for initial load
                    
                    # Verify we reached the correct domain
                    current_url = self.driver.current_url
                    if 'hipcamp.com' not in current_url:
                        self.log_debug("Navigation failed to reach Hipcamp", "ERROR")
                        # Try alternative URL
                        alt_url = "https://www.hipcamp.com/en-US"
                        self.log_debug(f"Trying alternative URL: {alt_url}", "DEBUG")
                        self.driver.get(alt_url)
                        time.sleep(3)
                    
                    # Final URL check
                    if '404' in self.driver.current_url or 'error' in self.driver.current_url.lower():
                        self.log_debug("Landed on error page, trying main site", "WARNING")
                        self.driver.get("https://www.hipcamp.com/en-US")
                        time.sleep(3)
                    
                    self.log_debug(f"Final navigation URL: {self.driver.current_url}", "DEBUG")
                    return True
                    
                except Exception as e:
                    self.log_debug(f"Navigation failed: {str(e)}", "ERROR")
                    return False

            elif action_type == "wait":
                time.sleep(action['seconds'])
                
            elif action_type == "scroll":
                scroll_amount = random.randint(300, 700)
                self.driver.execute_script(
                    "window.scrollBy({top: arguments[0], left: 0, behavior: 'smooth'});", 
                    scroll_amount
                )

            return True
        
        except Exception as e:
            self.log_debug(f"Action failed: {str(e)}", "ERROR")
            self.log_debug(f"Full error: {traceback.format_exc()}", "DEBUG")
            return False

    def get_ai_instruction(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/slimeoperator",
            "X-Title": "SlimeOperator Browser Automation",
            "Content-Type": "application/json"
        }

        # Get current state with page elements
        browser_state = self.get_browser_state()
        
        # Enhanced system prompt with element information
        system_prompt = """You are a browser automation assistant. Analyze the page elements and return ONE action as a JSON object.
        Available elements are provided in the state data. Use the most appropriate element for the current goal.
        Return ONLY a single JSON object with one of these formats:
        1. {"action":"click", "xpath":"element_xpath", "reason":"why"}
        2. {"action":"type", "xpath":"input_xpath", "text":"text_to_type", "reason":"why"}
        3. {"action":"navigate", "url":"target_url", "reason":"why"}"""

        data = {
            "model": "deepseek/deepseek-r1",
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user", 
                    "content": f"Goal: {self.current_goal}\nCurrent state: {json.dumps(browser_state, indent=2)}"
                }
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.log_debug(f"API attempt {attempt + 1}/{max_retries}", "DEBUG")
                
                with requests.Session() as session:
                    session.headers.update(headers)
                    response = session.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        json=data,
                        timeout=(10, 30)
                    )
                
                if response.status_code == 200:
                    response_data = response.json()
                    if 'choices' in response_data and response_data['choices']:
                        content = response_data['choices'][0]['message']['content'].strip()
                        
                        # Clean the response - remove any non-JSON text
                        try:
                            # Find the first '{' and last '}'
                            start = content.find('{')
                            end = content.rfind('}') + 1
                            if start >= 0 and end > start:
                                content = content[start:end]
                            
                            action = json.loads(content)
                            
                            # Validate action structure
                            if not isinstance(action, dict):
                                raise ValueError("Action must be a dictionary")
                            
                            if 'action' not in action:
                                raise ValueError("Action missing 'action' field")
                                
                            if action['action'] not in ['navigate', 'click', 'type']:
                                raise ValueError(f"Invalid action type: {action['action']}")
                                
                            # For navigation actions, ensure URL is complete
                            if action['action'] == 'navigate':
                                url = action.get('url', '')
                                if not url.startswith('http'):
                                    raise ValueError(f"Invalid URL: {url}")
                                
                                # Force Hipcamp URL format
                                if 'hipcamp.com' in url and '/en-US' not in url:
                                    action['url'] = url.replace('hipcamp.com', 'hipcamp.com/en-US')
                            
                            self.log_debug(f"Valid action parsed: {json.dumps(action, indent=2)}", "DEBUG")
                            return action
                            
                        except json.JSONDecodeError as e:
                            self.log_debug(f"JSON parse error: {str(e)}", "ERROR")
                            self.log_debug(f"Raw content: {content}", "DEBUG")
                            continue
                            
                        except ValueError as e:
                            self.log_debug(f"Validation error: {str(e)}", "ERROR")
                            continue
                
                self.log_debug(f"Invalid response: {response.text[:500]}", "WARNING")
                
            except Exception as e:
                self.log_debug(f"Request error: {str(e)}", "ERROR")
                if attempt < max_retries - 1:
                    time.sleep(2)
                continue

        # Fallback action with verified URL
        return {
            "action": "navigate",
            "url": "https://www.hipcamp.com/en-US",
            "reason": "Fallback: Direct navigation to Hipcamp main page"
        }

    def get_ai_goal_refinement(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        system_prompt = f"""You are analyzing the current automation goal: {self.current_goal}
        Based on the current browser state, suggest an improved or more specific goal.
        Consider what additional steps or refinements could make the goal more effective."""

        payload = {
            "model": "deepseek-ai/deepseek-r1",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(self.get_browser_state())}
            ],
            "temperature": 0.7
        }

        try:
            response = requests.post(self.api_url, headers=headers, json=payload)
            response_data = response.json()
            refined_goal = response_data['choices'][0]['message']['content']
            return refined_goal
        except:
            return None

    def print_banner(self):
        print(Fore.GREEN + r"""
  _________.____    .___   _____  ___________________ _______________________________    ________________________ __________ 
 /   _____/|    |   |   | /     \ \_   _____/\_____  \\______   \_   _____/\______   \  /  _  \__    ___/\_____  \\______   \
 \_____  \ |    |   |   |/  \ /  \ |    __)_  /   |   \|     ___/|    __)_  |       _/ /  /_\  \|    |    /   |   \|       _/
 /        \|    |___|   /    Y    \|        \/    |    \    |    |        \ |    |   \/    |    \    |   /    |    \    |   \
/_______  /|_______ \___\____|__  /_______  /\_______  /____|   /_______  / |____|_  /\____|__  /____|   \_______  /____|_  /
        \/         \/           \/        \/         \/                 \/         \/         \/                 \/       \/ 
""")
        print(Style.BRIGHT + Fore.GREEN + "    SLIMEOPERATOR DEEPSEEK R1".center(80))
        print()
        print(Fore.CYAN + "[ NEURAL INTERFACE ACTIVE ]".center(80))
        print(Fore.RED + ">> INITIALIZING SLIME PROTOCOLS <<\n")

    def reset_browser(self):
        """Hard reset of the browser"""
        self.log_debug("Performing hard browser reset", "WARNING")
        try:
            self.driver.quit()
        except:
            pass
        
        time.sleep(2)
        
        # Fresh browser instance
        firefox_service = Service()
        self.driver = webdriver.Firefox(service=firefox_service)
        self.driver.implicitly_wait(5)
        
        self.log_debug("Browser reset complete", "INFO")

    def main_loop(self):
        self.print_banner()
        self.log_debug("Session started", "INFO")
        self.log_debug(f"API Key present: {bool(self.api_key)}", "DEBUG")
        self.log_debug(f"Browser: Firefox", "DEBUG")
        
        print(Fore.YELLOW + "Establishing quantum connection...")
        time.sleep(1)
        
        for _ in range(3):
            print(Fore.GREEN + "Bypassing security protocols" + Fore.WHITE + "." * random.randint(1,5))
            time.sleep(0.3)
        
        print("\n" + Fore.CYAN + "Terminal interface activated".upper())
        print(Fore.MAGENTA + "-" * 60 + Style.RESET_ALL + "\n")
        
        self.current_goal = input(Fore.CYAN + "[INITIAL DIRECTIVE] > " + Fore.GREEN)
        self.log_debug(f"Initial goal set: {self.current_goal}", "INFO")
        self.running = True
        
        import threading
        import queue

        command_queue = queue.Queue()
        
        def input_thread():
            while self.running:
                cmd = input().strip().lower()
                command_queue.put(cmd)
        
        threading.Thread(target=input_thread, daemon=True).start()
        self.log_debug("Input thread started", "DEBUG")
        
        print(Fore.YELLOW + "\nAutomation started! Type 'help' for commands\n")
        
        try:
            while self.running:
                try:
                    # health check
                    try:
                        _ = self.driver.current_url
                    except:
                        self.log_debug("Browser health check failed - attempting restart", "WARNING")
                        try:
                            self.driver.quit()
                        except:
                            pass
                        time.sleep(2)
                        self.start_browser()
                        time.sleep(5)  # ensure new browser is up fully
                        continue

                    while not command_queue.empty():
                        cmd = command_queue.get_nowait()
                        self.log_debug(f"Command received: {cmd}", "DEBUG")
                        
                        if cmd == 'exit':
                            self.running = False
                            break
                        elif cmd == 'new':
                            print(Fore.CYAN + "\n[NEW DIRECTIVE] > " + Fore.GREEN, end='')
                            new_goal = input().strip()
                            self.current_goal = new_goal
                            self.log_debug(f"Goal updated: {new_goal}", "INFO")
                        elif cmd == 'help':
                            self.print_help()
                        elif cmd == 'save':
                            self.save_debug_log()
                        elif cmd == 'info':
                            self.log_debug(f"Session runtime: {int(time.time() - self.start_time)}s", "INFO")
                            self.log_debug(f"Total actions: {self.action_count}", "INFO")
                            self.log_debug(f"Current goal: {self.current_goal}", "INFO")
                            self.log_debug(f"Current URL: {self.driver.current_url}", "INFO")

                    if not self.running:
                        break

                    # get next AI action
                    self.log_debug("Requesting AI instruction", "DEBUG")
                    action = self.get_ai_instruction()

                    # Check if action is a dictionary
                    if not isinstance(action, dict):
                        self.log_debug("Received invalid action format", "ERROR")
                        continue

                    print(Fore.WHITE + "\n[NEURAL RESPONSE] " + Fore.GREEN +
                          f"Action: {action['action'].upper()}" + Fore.WHITE + " | " +
                          Fore.CYAN + f"Reason: {action.get('reason', 'No reason provided')}")
                    
                    success = self.execute_action(action)

                    if action['action'] == 'navigate':
                        # Verify navigation actually worked
                        target_url = action['url']
                        current_url = self.driver.current_url
                        
                        if target_url not in current_url:
                            self.log_debug(f"Navigation verification failed. Expected: {target_url}, Got: {current_url}", "ERROR")
                            # Force direct navigation
                            self.driver.get(target_url)
                            time.sleep(3)

                    status_color = Fore.GREEN if success else Fore.RED
                    print(status_color + f"\n[STATUS] {action['action'].upper()} OUTCOME: {'QUANTUM SYNCHRONIZED' if success else 'PROTOCOL FAILURE'}")
                    
                    self.action_count += 1
                    if self.action_count % 5 == 0:
                        self.log_debug("Performing goal optimization analysis", "INFO")
                        refined_goal = self.get_ai_goal_refinement()
                        if refined_goal:
                            print(Fore.CYAN + f"Suggested goal refinement: {refined_goal}")
                            print(Fore.YELLOW + "Type 'new' to update goal or press Enter to continue")

                    time.sleep(2)

                except queue.Empty:
                    pass
                except Exception as e:
                    self.log_debug(f"Loop iteration error: {str(e)}", "ERROR")
                    self.log_debug(f"Full error: {traceback.format_exc()}", "DEBUG")
                    time.sleep(2)

        except KeyboardInterrupt:
            self.log_debug("Keyboard interrupt detected - saving and exiting", "WARNING")
        except Exception as e:
            self.log_debug(f"Main loop error: {str(e)}", "ERROR")
            self.log_debug(f"Full error: {traceback.format_exc()}", "DEBUG")
        finally:
            # Cleanup
            self.running = False
            try:
                self.log_debug("Session ending - cleaning up", "INFO")
                self.save_debug_log()
                self.driver.quit()
            except:
                pass
            print(Fore.RED + "\nBrowser instance terminated. Returning to base reality...")

if __name__ == "__main__":
    automator = BrowserAutomator()
    automator.main_loop()
