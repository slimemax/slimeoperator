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
import threading
import queue
import re
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

load_dotenv()
init(autoreset=True)  # Initialize colorama

class DeepseekBrain:
    """Central coordinator for AI decision making and thread management"""
    def __init__(self, automator):
        self.automator = automator
        self.task_queue = queue.Queue()  # Queue for high-level tasks
        self.worker_queues = []  # List of queues for worker threads
        self.worker_threads = []  # List of worker threads
        self.coordinator_thread = None
        self.running = False
        self.last_assessment = None
        self.current_strategy = None
        self.last_action_type = None  # Track last action type
        self.last_action_time = 0  # Track when last action was executed
        self.consecutive_same_actions = 0  # Track repeated actions

    def start(self):
        """Start the brain coordinator"""
        self.running = True
        self.coordinator_thread = threading.Thread(target=self._coordinator_loop, daemon=True)
        self.coordinator_thread.start()
        self.automator.log_debug("Deepseek Brain coordinator started", "INFO")

    def stop(self):
        """Stop all brain operations"""
        self.running = False
        self.task_queue = queue.Queue()  # Clear tasks
        for q in self.worker_queues:
            q.queue.clear()  # Clear worker queues

    def _distribute_tasks(self):
        """Distribute tasks to worker threads"""
        try:
            # Check if we have tasks to distribute
            if self.task_queue.empty():
                return

            # Distribute tasks evenly among worker queues
            while not self.task_queue.empty():
                for queue in self.worker_queues:
                    if not self.task_queue.empty():
                        task = self.task_queue.get_nowait()
                        queue.put(task)

        except Exception as e:
            self.automator.log_debug(f"Task distribution error: {str(e)}", "ERROR")

    def _assess_situation(self):
        """Assess current browser state and determine strategy"""
        try:
            # Get a snapshot of the current state
            try:
                state = {
                    "url": self.automator.driver.current_url,
                    "title": self.automator.driver.title,
                    "goal": self.automator.current_goal,
                    "elements": self.automator.scan_page_elements(),
                    "last_actions": self.automator.action_history[-3:] if self.automator.action_history else [],
                    "action_count": self.automator.action_count,
                    "timestamp": time.time()
                }
            except Exception as e:
                self.automator.log_debug(f"State snapshot error: {str(e)}", "ERROR")
                return None

            # Simplified system prompt to reduce parsing errors
            system_prompt = """You are the strategic coordinator for browser automation.
Analyze the current state and return a JSON strategy object with these exact fields:
{
    "analysis": "brief situation analysis",
    "priority_tasks": ["task1", "task2"],
    "suggested_thread_count": 3,
    "focus_areas": ["area1", "area2"]
}
Keep responses concise and focused on immediate next steps."""

            data = {
                "model": "deepseek/deepseek-r1",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps({"state": state}, default=str, ensure_ascii=False)}
                ],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
                "max_tokens": 250
            }

            headers = {
                "Authorization": f"Bearer {self.automator.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                self.automator.api_url,
                headers=headers,
                json=data,
                timeout=(10, 30)
            )

            if response.status_code == 200:
                try:
                    response_data = response.json()
                    if 'choices' in response_data and response_data['choices']:
                        content = response_data['choices'][0]['message']['content'].strip()
                        
                        # Clean and validate JSON
                        start = content.find('{')
                        end = content.rfind('}') + 1
                        if start >= 0 and end > start:
                            content = content[start:end]
                            
                        strategy = json.loads(content)
                        
                        # Validate required fields
                        required_fields = ['analysis', 'priority_tasks', 'suggested_thread_count', 'focus_areas']
                        if all(field in strategy for field in required_fields):
                            self.current_strategy = strategy
                            self.automator.log_debug(
                                f"New strategy: {json.dumps(strategy, indent=2)[:200]}...", 
                                "INFO"
                            )
                            return strategy
                        else:
                            missing = [f for f in required_fields if f not in strategy]
                            self.automator.log_debug(f"Strategy missing fields: {missing}", "ERROR")
                            return None
                            
                except json.JSONDecodeError as e:
                    self.automator.log_debug(f"Strategy JSON parse error: {str(e)}\nContent: {content[:200]}", "ERROR")
                except Exception as e:
                    self.automator.log_debug(f"Strategy processing error: {str(e)}", "ERROR")
            else:
                self.automator.log_debug(f"API error {response.status_code}: {response.text[:200]}", "ERROR")
            
            return None

        except Exception as e:
            self.automator.log_debug(f"Strategy assessment error: {str(e)}", "ERROR")
            self.automator.log_debug(f"Assessment traceback: {traceback.format_exc()}", "DEBUG")
            return None

    def _coordinator_loop(self):
        """Main coordinator loop"""
        assessment_interval = 30  # Increased to 30 seconds between assessments
        last_assessment = 0
        
        while self.running:
            try:
                current_time = time.time()
                
                # Only assess periodically and if no recent action was successful
                if (current_time - last_assessment >= assessment_interval and 
                    current_time - self.last_action_time >= 5):  # Wait at least 5 seconds after last action
                    
                    self.automator.log_debug("Brain performing strategic assessment...", "INFO")
                    strategy = self._assess_situation()
                    if strategy:
                        # Adjust thread count based on strategy
                        suggested_threads = min(max(strategy.get('suggested_thread_count', 3), 1), 10)
                        self._adjust_worker_threads(suggested_threads)

                        # Create tasks based on priority areas
                        for task in strategy.get('priority_tasks', []):
                            if isinstance(task, str):  # Validate task format
                                # Don't add redundant navigation tasks
                                if ('navigate' in task.lower() and 
                                    self.last_action_type == 'navigate' and 
                                    current_time - self.last_action_time < 10):
                                    continue
                                    
                                self.task_queue.put({
                                    "task": task,
                                    "focus": strategy.get('focus_areas', []),
                                    "timestamp": current_time,
                                    "assessment_id": int(current_time)
                                })
                                self.automator.log_debug(f"Added task: {task}", "DEBUG")
                    
                    last_assessment = current_time
                    self.automator.log_debug("Strategic assessment complete", "INFO")

                # Distribute tasks to worker threads every 2 seconds
                if current_time - last_assessment > 2:
                    self._distribute_tasks()
                
                time.sleep(1)  # Check state every second

            except Exception as e:
                self.automator.log_debug(f"Coordinator error: {str(e)}", "ERROR")
                self.automator.log_debug(f"Coordinator traceback: {traceback.format_exc()}", "DEBUG")
                time.sleep(5)  # Longer sleep on error

    def _adjust_worker_threads(self, target_count):
        """Adjust number of worker threads based on strategy"""
        current_count = len(self.worker_threads)
        
        # Remove excess threads
        while current_count > target_count:
            if self.worker_queues:
                worker_queue = self.worker_queues.pop()
                worker_queue.put(None)  # Signal thread to stop
                # Remove the thread from our list
                if self.worker_threads:
                    self.worker_threads.pop()
            current_count -= 1

        # Add new threads
        while current_count < target_count:
            worker_queue = queue.Queue()
            self.worker_queues.append(worker_queue)
            thread = threading.Thread(
                target=self._worker_loop,
                args=(worker_queue, current_count),
                daemon=True
            )
            thread.start()
            self.worker_threads.append(thread)
            current_count += 1

        self.automator.log_debug(f"Adjusted worker threads to {target_count}", "INFO")

    def _worker_loop(self, task_queue, worker_id):
        """Individual worker thread loop"""
        while self.running:
            try:
                task = task_queue.get(timeout=5)
                if task is None:  # Stop signal
                    break

                self.automator.log_debug(f"Worker {worker_id} processing task: {task['task']}", "DEBUG")
                
                # Get AI action based on task
                action = self._get_worker_action(task, worker_id)
                if action:
                    self.automator.action_queue.put(action)

            except queue.Empty:
                continue
            except Exception as e:
                self.automator.log_debug(f"Worker {worker_id} error: {str(e)}", "ERROR")

    def _get_worker_action(self, task, worker_id):
        """Get specific action for a task"""
        try:
            # Check for direct site navigation in task
            task_text = task['task'].lower()
            
            # Don't repeat the same navigation action too quickly
            current_time = time.time()
            if (self.last_action_type == 'navigate' and 
                current_time - self.last_action_time < 5):
                return None
            
            # More aggressive direct site matching
            for site, url in self.automator.direct_sites.items():
                # Check for various ways to reference the site
                if any(pattern in task_text for pattern in [
                    f"go to {site}",
                    f"visit {site}",
                    f"open {site}",
                    f"search {site}",
                    f"find {site}",
                    site,  # Even just mentioning the site name
                ]):
                    # Check if we're already on this site
                    current_url = self.automator.driver.current_url.lower()
                    if site in current_url:
                        return None
                        
                    action = {
                        "action": "navigate",
                        "url": url,
                        "reason": f"Direct navigation to {site} requested"
                    }
                    self.last_action_type = 'navigate'
                    self.last_action_time = current_time
                    return action

            # If no direct navigation, proceed with normal AI action generation
            max_retries = 2  # Reduced retries
            retry_delay = 1  # Shorter delay
            
            for attempt in range(max_retries):
                try:
                    system_prompt = f"""Focus on task: {task['task']}
                    Return ONE specific browser action as JSON:
                    1. {{"action":"click", "xpath":"element_xpath", "reason":"why"}}
                    2. {{"action":"type", "xpath":"input_xpath", "text":"text_to_type", "reason":"why"}}
                    3. {{"action":"navigate", "url":"target_url", "reason":"why"}}"""

                    state = {
                        "url": self.automator.driver.current_url,
                        "title": self.automator.driver.title,
                        "task": task,
                        "elements": self.automator.scan_page_elements()
                    }

                    data = {
                        "model": "deepseek/deepseek-r1",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"State: {json.dumps(state, default=str)}"}
                        ],
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                        "max_tokens": 150
                    }

                    response = requests.post(
                        self.automator.api_url,
                        headers={"Authorization": f"Bearer {self.automator.api_key}"},
                        json=data,
                        timeout=(3, 10)  # Even shorter timeouts
                    )

                    if response.status_code == 200:
                        content = response.json()['choices'][0]['message']['content']
                        action = json.loads(content)
                        if self.automator.validate_action(action):
                            # Track action type and time
                            self.last_action_type = action['action']
                            self.last_action_time = current_time
                            return action
                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        self.automator.log_debug(f"API timeout, retrying ({attempt + 1}/{max_retries})", "WARNING")
                        time.sleep(retry_delay)
                    continue
                except Exception as e:
                    if attempt < max_retries - 1:
                        self.automator.log_debug(f"API error: {str(e)}, retrying ({attempt + 1}/{max_retries})", "WARNING")
                        time.sleep(retry_delay)
                    continue

        except Exception as e:
            self.automator.log_debug(f"Worker {worker_id} action error: {str(e)}", "ERROR")
        
        return None

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
        self.thread_count = 5  # Default thread count
        self.action_queue = queue.Queue()  # Queue for actions from AI threads
        self.ai_threads = []  # List to track AI threads
        self.brain = DeepseekBrain(self)
        # Add direct navigation sites
        self.direct_sites = {
            'google': 'https://www.google.com',
            'amazon': 'https://www.amazon.com',
            'facebook': 'https://www.facebook.com',
            'twitter': 'https://www.twitter.com',
            'youtube': 'https://www.youtube.com',
            'linkedin': 'https://www.linkedin.com',
            'reddit': 'https://www.reddit.com',
            'github': 'https://www.github.com',
            'hipcamp': 'https://www.hipcamp.com/en-US'
        }
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
{Fore.CYAN}tc N{Fore.WHITE}  - Set thread count (1-10)

{Fore.GREEN}=== SESSION INFO ===
{Fore.WHITE}Runtime: {int(time.time() - self.start_time)}s
Actions: {self.action_count}
Current Goal: {self.current_goal}
Active Threads: {len([t for t in self.ai_threads if t.is_alive()])}
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
            
            if action_type == "type":
                text_to_type = action['text']
                self.log_debug(f"Attempting to type: {text_to_type}", "DEBUG")
                
                # Enhanced input selectors with priorities
                input_selectors = [
                    # High priority - specific IDs and names
                    (By.ID, "twotabsearchtextbox", "Amazon main search"),
                    (By.ID, "search", "Generic search box"),
                    (By.NAME, "q", "Google/generic search"),
                    (By.NAME, "field-keywords", "Amazon alternative"),
                    (By.NAME, "search", "Generic search name"),
                    
                    # Medium priority - ARIA and role attributes
                    (By.CSS_SELECTOR, "[role='searchbox']", "Search role"),
                    (By.CSS_SELECTOR, "[aria-label*='search' i]", "Search aria-label"),
                    (By.CSS_SELECTOR, "[placeholder*='search' i]", "Search placeholder"),
                    
                    # Lower priority - type attributes
                    (By.CSS_SELECTOR, "[type='search']", "Search type"),
                    (By.CSS_SELECTOR, "[type='text']", "Text type"),
                    
                    # Lowest priority - generic input tags
                    (By.XPATH, "//input[@type='text']", "Generic text input"),
                    (By.XPATH, "//input[@type='search']", "Generic search input"),
                    (By.XPATH, action.get('xpath', ''), "Custom xpath")
                ]
                
                element = None
                wait = WebDriverWait(self.driver, 5)
                
                for by, selector, desc in input_selectors:
                    try:
                        # Try explicit wait first
                        try:
                            element = wait.until(EC.presence_of_element_located((by, selector)))
                            if element.is_displayed() and element.is_enabled():
                                self.log_debug(f"Found input using {desc}", "DEBUG")
                                break
                        except:
                            # Fall back to find_elements
                            elements = self.driver.find_elements(by, selector)
                            for el in elements:
                                if el.is_displayed() and el.is_enabled():
                                    element = el
                                    self.log_debug(f"Found input using {desc} (fallback)", "DEBUG")
                                    break
                        if element:
                            break
                    except:
                        continue

                if not element:
                    raise Exception("No input element found")

                # Clear with multiple retry methods
                clear_methods = [
                    lambda: element.clear(),
                    lambda: element.send_keys(Keys.CONTROL + "a" + Keys.DELETE),
                    lambda: element.send_keys(Keys.COMMAND + "a" + Keys.DELETE),
                    lambda: self.driver.execute_script("arguments[0].value = '';", element),
                    lambda: ActionChains(self.driver).click(element).key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL).send_keys(Keys.DELETE).perform()
                ]

                for clear_method in clear_methods:
                    try:
                        clear_method()
                        if not element.get_attribute('value'):
                            break
                    except:
                        continue

                # Type with human-like delays and verification
                for char in text_to_type:
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            current_value = element.get_attribute('value')
                            element.send_keys(char)
                            time.sleep(random.uniform(0.05, 0.15))
                            
                            # Verify character was typed
                            new_value = element.get_attribute('value')
                            if len(new_value) > len(current_value):
                                break
                            
                            # If not, try JavaScript
                            if attempt == max_retries - 1:
                                self.driver.execute_script(
                                    f"arguments[0].value = arguments[1];", 
                                    element, 
                                    current_value + char
                                )
                        except:
                            if attempt == max_retries - 1:
                                self.driver.execute_script(
                                    f"arguments[0].value += '{char}';", 
                                    element
                                )
                            time.sleep(0.1)

                # Enhanced search submission with multiple methods
                submit_methods = [
                    # Method 1: Press Enter key
                    lambda: element.send_keys(Keys.RETURN),
                    
                    # Method 2: Look for and click search button
                    lambda: self._click_search_button(),
                    
                    # Method 3: Form submit
                    lambda: self.driver.execute_script("arguments[0].form.submit();", element),
                    
                    # Method 4: Synthesize Enter key event
                    lambda: self.driver.execute_script("""
                        var event = new KeyboardEvent('keydown', {
                            'key': 'Enter',
                            'code': 'Enter',
                            'keyCode': 13,
                            'which': 13,
                            'bubbles': true
                        });
                        arguments[0].dispatchEvent(event);
                    """, element)
                ]

                for submit_method in submit_methods:
                    try:
                        submit_method()
                        time.sleep(1)
                        if self._verify_search_submitted():
                            return True
                    except:
                        continue

                return True

            elif action_type == "click":
                self.log_debug("Processing click action", "DEBUG")
                
                # Get click target info
                base_xpath = action.get('xpath', '')
                reason = action.get('reason', '').lower()
                
                # Enhanced text target extraction
                target_texts = []
                # Add quoted text from reason
                quoted = re.findall(r'"([^"]*)"', reason)
                target_texts.extend(quoted)
                
                # Extract key terms from reason
                reason_words = reason.lower().split()
                for word in reason_words:
                    if len(word) > 3 and word not in ['click', 'button', 'link', 'the', 'and', 'for']:
                        target_texts.append(word)
                
                # Add common button text based on context
                if any(term in reason.lower() for term in ['search', 'find', 'look']):
                    target_texts.extend(['search', 'go', 'find', 'submit'])
                if any(term in reason.lower() for term in ['submit', 'send', 'confirm']):
                    target_texts.extend(['submit', 'send', 'confirm', 'ok', 'yes'])
                if any(term in reason.lower() for term in ['next', 'continue']):
                    target_texts.extend(['next', 'continue', 'proceed'])
                
                # Build comprehensive xpath list with priorities
                possible_xpaths = []
                
                # High priority: Exact matches with button/input elements
                for text in target_texts:
                    possible_xpaths.extend([
                        f"//button[normalize-space(.)='{text}']",
                        f"//input[@type='submit' and @value='{text}']",
                        f"//input[@type='button' and @value='{text}']"
                    ])
                
                # Medium priority: Contains matches with various elements
                for text in target_texts:
                    possible_xpaths.extend([
                        f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//*[@role='button' and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//*[contains(@aria-label, '{text}')]",
                        f"//*[contains(@title, '{text}')]",
                        f"//*[contains(@data-testid, '{text}')]",
                        f"//*[contains(@id, '{text}')]",
                        f"//*[contains(@name, '{text}')]"
                    ])
                
                # Add the original xpath if provided
                if base_xpath:
                    possible_xpaths.insert(0, base_xpath)
                
                # Add common interactive elements
                possible_xpaths.extend([
                    "//button[@type='submit']",
                    "//input[@type='submit']",
                    "//*[@role='button']",
                    "//*[contains(@class, 'button')]",
                    "//*[contains(@class, 'btn')]",
                    "//button[last()]"  # Sometimes the last button is the submit button
                ])
                
                # Try each xpath with improved interaction
                wait = WebDriverWait(self.driver, 5)
                
                for xpath in possible_xpaths:
                    try:
                        # Try explicit wait first
                        try:
                            element = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
                            if element.is_displayed() and element.is_enabled():
                                if self._try_click_element(element):
                                    return True
                        except:
                            # Fall back to find_elements
                            elements = self.driver.find_elements(By.XPATH, xpath)
                            for element in elements:
                                if element.is_displayed() and element.is_enabled():
                                    if self._try_click_element(element):
                                        return True
                    except:
                        continue
                
                raise Exception("No clickable element found")

            elif action_type == "navigate":
                target_url = action['url'].lower()
                # Extract domain from URL or use full URL
                domain = target_url.replace('http://', '').replace('https://', '').split('/')[0].split('.')[0]
                
                # Check if this is a direct navigation site
                if domain in self.direct_sites:
                    self.log_debug(f"Direct navigation to {domain}", "INFO")
                    try:
                        self.driver.get(self.direct_sites[domain])
                        time.sleep(2)
                        return True
                    except Exception as e:
                        self.log_debug(f"Direct navigation failed: {str(e)}", "ERROR")
                        return False
                else:
                    try:
                        self.driver.get(target_url)
                        time.sleep(2)
                        return True
                    except Exception as e:
                        self.log_debug(f"Navigation failed: {str(e)}", "ERROR")
                        return False

            return True
        
        except Exception as e:
            self.log_debug(f"Action failed: {str(e)}", "ERROR")
            self.log_debug(f"Full error: {traceback.format_exc()}", "DEBUG")
            return False

    def _try_click_element(self, element):
        """Helper method for robust element clicking"""
        try:
            # Scroll into view with center alignment
            self.driver.execute_script("""
                arguments[0].scrollIntoView({
                    behavior: 'smooth',
                    block: 'center',
                    inline: 'center'
                });
            """, element)
            time.sleep(0.5)
            
            # Ensure element is in viewport
            viewport_script = """
                var rect = arguments[0].getBoundingClientRect();
                return (
                    rect.top >= 0 &&
                    rect.left >= 0 &&
                    rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                    rect.right <= (window.innerWidth || document.documentElement.clientWidth)
                );
            """
            
            if not self.driver.execute_script(viewport_script, element):
                # If not in viewport, try scrolling with offset
                self.driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(-100, 100))
                time.sleep(0.5)
            
            # Multiple click methods with verification
            click_methods = [
                # Standard click
                lambda: element.click(),
                
                # JavaScript click
                lambda: self.driver.execute_script("arguments[0].click();", element),
                
                # Action chains click
                lambda: ActionChains(self.driver).move_to_element(element).click().perform(),
                
                # JavaScript event dispatch
                lambda: self.driver.execute_script("""
                    var event = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    });
                    arguments[0].dispatchEvent(event);
                """, element),
                
                # Focus and Enter key
                lambda: (element.send_keys(Keys.RETURN) if element.is_displayed() and element.is_enabled() else None),
                
                # Double click (some elements require this)
                lambda: ActionChains(self.driver).double_click(element).perform()
            ]
            
            for click_method in click_methods:
                try:
                    click_method()
                    time.sleep(0.5)
                    if self.verify_click_success():
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            self.log_debug(f"Click attempt failed: {str(e)}", "DEBUG")
            return False

    def _verify_search_submitted(self):
        """Verify if a search was successfully submitted"""
        try:
            # Store initial state
            initial_url = self.driver.current_url
            
            # Wait briefly for changes
            time.sleep(1)
            
            # Check for URL change
            if self.driver.current_url != initial_url:
                return True
            
            # Check for loading indicators
            loading_indicators = [
                "//div[contains(@class, 'loading')]",
                "//div[contains(@class, 'spinner')]",
                "//div[contains(@class, 'progress')]",
                "//div[contains(@class, 'searching')]",
                "//div[contains(@class, 'results')]"
            ]
            
            for indicator in loading_indicators:
                try:
                    elements = self.driver.find_elements(By.XPATH, indicator)
                    if any(el.is_displayed() for el in elements):
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            self.log_debug(f"Search verification error: {str(e)}", "DEBUG")
            return False

    def _click_search_button(self):
        """Helper method to find and click search button"""
        search_button_selectors = [
            (By.XPATH, "//button[@type='submit']"),
            (By.XPATH, "//input[@type='submit']"),
            (By.XPATH, "//button[contains(@class, 'search')]"),
            (By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'search')]"),
            (By.CSS_SELECTOR, "[aria-label*='search' i]"),
            (By.CSS_SELECTOR, "[title*='search' i]")
        ]
        
        for by, selector in search_button_selectors:
            try:
                elements = self.driver.find_elements(by, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        return self._try_click_element(element)
            except:
                continue
        
        return False

    def verify_click_success(self):
        """Verify if click action had an effect"""
        try:
            # Store initial state
            initial_url = self.driver.current_url
            
            # Give more time for changes to occur
            for _ in range(3):  # Try for 3 seconds
                time.sleep(1)
                
                # Check for URL change
                if initial_url != self.driver.current_url:
                    self.log_debug("Click verified - URL changed", "DEBUG")
                    return True
                
                # Check for any new elements or DOM changes
                try:
                    # Look for common post-click indicators
                    indicators = [
                        "//div[contains(@class, 'loading')]",
                        "//div[contains(@class, 'spinner')]",
                        "//div[contains(@class, 'progress')]",
                        "//div[contains(@class, 'overlay')]",
                        "//div[contains(@class, 'modal')]",
                        "//form[contains(@class, 'search')]",
                        "//div[contains(@class, 'results')]"
                    ]
                    
                    for indicator in indicators:
                        elements = self.driver.find_elements(By.XPATH, indicator)
                        if any(el.is_displayed() for el in elements):
                            self.log_debug("Click verified - Found post-click indicator", "DEBUG")
                            return True
                except:
                    pass
                
                # Check if page is still responding
                try:
                    self.driver.execute_script("return document.readyState")
                except:
                    self.log_debug("Click may have triggered page reload", "DEBUG")
                    return True
            
            # If we get here, assume click worked but had no visible effect
            self.log_debug("Click completed - no visible changes detected", "DEBUG")
            return True
            
        except Exception as e:
            self.log_debug(f"Click verification error: {str(e)}", "DEBUG")
            return True  # Assume click worked if we can't verify

    def get_ai_instruction_threaded(self):
        """Thread-safe version of get_ai_instruction that puts results in queue"""
        thread_id = threading.current_thread().ident
        self.log_debug(f"AI Thread {thread_id} started", "INFO")
        
        while self.running:
            try:
                # Thread-safe browser state check
                try:
                    current_url = self.driver.current_url
                    page_title = self.driver.title
                except Exception as e:
                    self.log_debug(f"Thread {thread_id} - Browser state error: {str(e)}", "ERROR")
                    time.sleep(2)
                    continue

                # Get shorter state snapshot with error handling
                try:
                    state = {
                        "url": current_url,
                        "title": page_title,
                        "goal": self.current_goal,
                        "elements": self.scan_page_elements()
                    }
                except Exception as e:
                    self.log_debug(f"Thread {thread_id} - State snapshot error: {str(e)}", "ERROR")
                    time.sleep(2)
                    continue

                # Simplified system prompt to reduce tokens
                system_prompt = """Browser automation assistant. Return ONE action as JSON:
                1. {"action":"click", "xpath":"element_xpath", "reason":"why"}
                2. {"action":"type", "xpath":"input_xpath", "text":"text_to_type", "reason":"why"}
                3. {"action":"navigate", "url":"target_url", "reason":"why"}"""

                data = {
                    "model": "deepseek/deepseek-r1",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Goal: {self.current_goal}\nState: {json.dumps(state, default=str)}"}
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "max_tokens": 150  # Limit response size
                }

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/slimeoperator",
                    "X-Title": f"SlimeOperator Thread {thread_id}"
                }

                # API request with detailed error handling
                try:
                    self.log_debug(f"Thread {thread_id} - Sending API request", "DEBUG")
                    response = requests.post(
                        self.api_url,
                        headers=headers,
                        json=data,
                        timeout=(10, 30)
                    )
                    
                    if response.status_code != 200:
                        self.log_debug(f"Thread {thread_id} - API error {response.status_code}: {response.text[:200]}", "ERROR")
                        time.sleep(2)
                        continue

                    response_data = response.json()
                    if 'choices' not in response_data or not response_data['choices']:
                        self.log_debug(f"Thread {thread_id} - Invalid API response format", "ERROR")
                        time.sleep(2)
                        continue

                    content = response_data['choices'][0]['message']['content'].strip()
                    
                    # Clean JSON content
                    try:
                        start = content.find('{')
                        end = content.rfind('}') + 1
                        if start >= 0 and end > start:
                            content = content[start:end]
                    except Exception as e:
                        self.log_debug(f"Thread {thread_id} - JSON cleaning error: {str(e)}", "ERROR")
                        continue

                    # Parse and validate action
                    try:
                        action = json.loads(content)
                        if self.validate_action(action):
                            self.log_debug(f"Thread {thread_id} - Valid action generated: {json.dumps(action)[:200]}", "DEBUG")
                            self.action_queue.put(action)
                        else:
                            self.log_debug(f"Thread {thread_id} - Invalid action format: {json.dumps(action)[:200]}", "WARNING")
                    except json.JSONDecodeError as e:
                        self.log_debug(f"Thread {thread_id} - JSON parse error: {str(e)}\nContent: {content[:200]}", "ERROR")
                    except Exception as e:
                        self.log_debug(f"Thread {thread_id} - Action processing error: {str(e)}", "ERROR")

                except requests.exceptions.Timeout:
                    self.log_debug(f"Thread {thread_id} - API request timeout", "WARNING")
                except requests.exceptions.RequestException as e:
                    self.log_debug(f"Thread {thread_id} - API request failed: {str(e)}", "ERROR")
                except Exception as e:
                    self.log_debug(f"Thread {thread_id} - Unexpected error: {str(e)}", "ERROR")

            except Exception as e:
                self.log_debug(f"Thread {thread_id} - Critical error: {str(e)}", "ERROR")
                self.log_debug(f"Thread {thread_id} - Traceback: {traceback.format_exc()}", "DEBUG")

            # Adaptive sleep based on success/failure
            time.sleep(random.uniform(1.5, 3.0))

        self.log_debug(f"AI Thread {thread_id} stopped", "INFO")

    def validate_action(self, action):
        """Validate AI action format"""
        try:
            if not isinstance(action, dict):
                return False
                
            required_fields = {
                'click': ['action', 'xpath', 'reason'],
                'type': ['action', 'xpath', 'text', 'reason'],
                'navigate': ['action', 'url', 'reason']
            }
            
            if 'action' not in action:
                return False
                
            action_type = action['action']
            if action_type not in required_fields:
                return False
                
            # Check required fields for action type
            for field in required_fields[action_type]:
                if field not in action:
                    return False
                    
            # Additional validation for specific actions
            if action_type == 'navigate':
                if not action['url'].startswith('http'):
                    return False
                if 'hipcamp.com' in action['url'] and '/en-US' not in action['url']:
                    action['url'] = action['url'].replace('hipcamp.com', 'hipcamp.com/en-US')
                    
            return True
            
        except Exception as e:
            self.log_debug(f"Action validation error: {str(e)}", "ERROR")
            return False

    def manage_ai_threads(self):
        """Start or adjust AI threads based on thread_count"""
        # Clean up finished threads
        self.ai_threads = [t for t in self.ai_threads if t.is_alive()]
        
        # Start new threads if needed
        while len(self.ai_threads) < self.thread_count:
            thread = threading.Thread(
                target=self.get_ai_instruction_threaded,
                daemon=True
            )
            thread.start()
            self.ai_threads.append(thread)
            self.log_debug(f"Started new AI thread (total: {len(self.ai_threads)})", "INFO")

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
        
        print(Fore.YELLOW + "Establishing quantum connection...")
        time.sleep(1)
        
        self.current_goal = input(Fore.CYAN + "[INITIAL DIRECTIVE] > " + Fore.GREEN)
        self.log_debug(f"Initial goal set: {self.current_goal}", "INFO")
        self.running = True
        
        # Start the brain coordinator
        self.brain.start()
        
        command_queue = queue.Queue()
        
        def input_thread():
            while self.running:
                cmd = input().strip().lower()
                command_queue.put(cmd)
        
        threading.Thread(target=input_thread, daemon=True).start()
        
        print(Fore.YELLOW + "\nAutomation started! Type 'help' for commands\n")
        
        try:
            while self.running:
                try:
                    # Process commands
                    while not command_queue.empty():
                        cmd = command_queue.get_nowait()
                        if cmd.startswith('tc '):
                            try:
                                new_count = int(cmd.split()[1])
                                if 1 <= new_count <= 10:
                                    self.thread_count = new_count
                                    self.log_debug(f"Thread count set to {new_count}", "INFO")
                                    self.brain._adjust_worker_threads(new_count)
                                else:
                                    print(Fore.RED + "Thread count must be between 1 and 10")
                            except:
                                print(Fore.RED + "Invalid thread count format")
                        elif cmd == 'exit':
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

                    # Process AI actions
                    try:
                        action = self.action_queue.get_nowait()
                        if action:
                            print(Fore.WHITE + "\n[NEURAL RESPONSE] " + Fore.GREEN +
                                  f"Action: {action['action'].upper()}" + Fore.WHITE + " | " +
                                  Fore.CYAN + f"Reason: {action.get('reason', 'No reason provided')}")
                            
                            success = self.execute_action(action)
                            self.action_count += 1

                            status_color = Fore.GREEN if success else Fore.RED
                            print(status_color + f"\n[STATUS] {action['action'].upper()} OUTCOME: {'QUANTUM SYNCHRONIZED' if success else 'PROTOCOL FAILURE'}")
                    except queue.Empty:
                        pass

                    time.sleep(0.1)

                except Exception as e:
                    self.log_debug(f"Loop error: {str(e)}", "ERROR")
                    time.sleep(1)

        except KeyboardInterrupt:
            self.log_debug("Keyboard interrupt detected - saving and exiting", "WARNING")
        except Exception as e:
            self.log_debug(f"Main loop error: {str(e)}", "ERROR")
            self.log_debug(f"Full error: {traceback.format_exc()}", "DEBUG")
        finally:
            self.running = False
            self.brain.stop()
            self.save_debug_log()
            self.driver.quit()
            print(Fore.RED + "\nBrowser instance terminated. Returning to base reality...")

if __name__ == "__main__":
    automator = BrowserAutomator()
    automator.main_loop()