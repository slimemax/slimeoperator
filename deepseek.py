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
                
                # Only assess periodically
                if current_time - last_assessment >= assessment_interval:
                    self.automator.log_debug("Brain performing strategic assessment...", "INFO")
                    strategy = self._assess_situation()
                    if strategy:
                        # Adjust thread count based on strategy
                        suggested_threads = min(max(strategy.get('suggested_thread_count', 3), 1), 10)
                        self._adjust_worker_threads(suggested_threads)

                        # Create tasks based on priority areas
                        for task in strategy.get('priority_tasks', []):
                            if isinstance(task, str):  # Validate task format
                                self.task_queue.put({
                                    "task": task,
                                    "focus": strategy.get('focus_areas', []),
                                    "timestamp": current_time,
                                    "assessment_id": int(current_time)  # Track which assessment created this task
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
            # Construct focused prompt based on task
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
                timeout=(10, 30)
            )

            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                action = json.loads(content)
                if self.automator.validate_action(action):
                    return action

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
                # Extract text to match from xpath or reason
                base_xpath = action.get('xpath', '')
                reason = action.get('reason', '').lower()
                
                # Build target text list from multiple sources
                target_texts = []
                if 'glamping' in base_xpath.lower():
                    target_texts.append('glamping')
                if 'glamp' in base_xpath.lower():
                    target_texts.append('glamp')
                if 'hipcamp' in base_xpath.lower():
                    target_texts.append('hipcamp')
                
                # Extract any quoted text from reason
                quoted = re.findall(r'"([^"]*)"', reason)
                target_texts.extend(quoted)
                
                # More flexible XPath patterns with dynamic text matching
                possible_xpaths = [base_xpath]  # Start with original XPath
                
                # Add dynamic XPaths for each target text
                for text in target_texts:
                    possible_xpaths.extend([
                        f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//a[contains(@href, '{text}')]",
                        f"//nav//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]",
                        f"//a[contains(@href, 'search') and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]"
                    ])
                
                # Add general purpose selectors
                possible_xpaths.extend([
                    "//a[contains(@class, 'search-result')]",
                    "//div[contains(@class, 'result')]//a",
                    "//h3/parent::a",
                    "//h3/following::a[1]"
                ])
                
                self.log_debug(f"Attempting to click with {len(possible_xpaths)} XPath patterns", "DEBUG")
                
                found_elements = []
                for xpath in possible_xpaths:
                    try:
                        elements = self.driver.find_elements(By.XPATH, xpath)
                        for el in elements:
                            try:
                                if el.is_displayed():
                                    found_elements.append({
                                        "element": el,
                                        "text": el.text.strip(),
                                        "xpath": xpath
                                    })
                            except:
                                continue
                    except Exception as e:
                        self.log_debug(f"XPath attempt failed: {xpath}", "DEBUG")
                        continue
                
                if not found_elements:
                    # Last resort - try finding any visible link containing target text
                    try:
                        links = self.driver.find_elements(By.TAG_NAME, "a")
                        for link in links:
                            try:
                                if link.is_displayed():
                                    link_text = link.text.lower()
                                    if any(text.lower() in link_text for text in target_texts):
                                        found_elements.append({
                                            "element": link,
                                            "text": link_text,
                                            "xpath": "direct_search"
                                        })
                            except:
                                continue
                    except:
                        pass
                
                if not found_elements:
                    raise Exception(f"No clickable elements found matching: {', '.join(target_texts)}")
                
                # Sort elements by relevance (prefer elements with exact text match)
                found_elements.sort(key=lambda x: sum(text.lower() in x["text"].lower() for text in target_texts), reverse=True)
                
                # Try clicking each element until success
                for elem_info in found_elements:
                    try:
                        element = elem_info["element"]
                        
                        # Ensure element is still valid
                        try:
                            _ = element.is_enabled()
                        except:
                            continue
                        
                        # Scroll into view with retry
                        scroll_attempts = 3
                        for _ in range(scroll_attempts):
                            try:
                                # Smooth scroll with offset
                                offset = random.randint(-100, 100)
                                self.driver.execute_script(
                                    """
                                    arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});
                                    window.scrollBy(0, arguments[1]);
                                    """, 
                                    element, 
                                    offset
                                )
                                time.sleep(1)  # Wait for scroll to complete
                                
                                # Verify element is visible in viewport
                                if self.driver.execute_script("""
                                    var elem = arguments[0];
                                    var rect = elem.getBoundingClientRect();
                                    return (
                                        rect.top >= 0 &&
                                        rect.left >= 0 &&
                                        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                                        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
                                    );
                                    """, element):
                                    break
                            except:
                                continue
                        
                        # Try multiple click methods with retry
                        click_methods = [
                            lambda: element.click(),
                            lambda: self.driver.execute_script("arguments[0].click();", element),
                            lambda: webdriver.ActionChains(self.driver).move_to_element(element).click().perform(),
                            lambda: self.driver.execute_script(
                                "arguments[0].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));", 
                                element
                            )
                        ]
                        
                        for click_method in click_methods:
                            try:
                                click_method()
                                if self.verify_click_success():
                                    self.log_debug(f"Click successful on: {elem_info['text'][:50]}", "DEBUG")
                                    return True
                            except Exception as e:
                                self.log_debug(f"Click method failed: {str(e)}", "DEBUG")
                                continue
                            
                            time.sleep(0.5)  # Brief pause between attempts
                            
                    except Exception as e:
                        self.log_debug(f"Click attempt failed: {str(e)}", "DEBUG")
                        continue
                
                raise Exception("All click attempts failed")

            elif action_type == "type":
                # Enhanced input field detection
                possible_selectors = [
                    (By.XPATH, action.get('xpath', '')),
                    (By.NAME, "q"),  # Google search
                    (By.XPATH, "//input[@type='text']"),
                    (By.XPATH, "//input[@type='search']"),
                    (By.XPATH, "//textarea[@type='search']"),
                    (By.XPATH, "//textarea[@name='q']"),
                    (By.CSS_SELECTOR, "[name='q']"),
                    (By.CSS_SELECTOR, "[type='search']"),
                    (By.CSS_SELECTOR, "[type='text']")
                ]
                
                element = None
                for by, selector in possible_selectors:
                    try:
                        wait = WebDriverWait(self.driver, 3)
                        element = wait.until(EC.presence_of_element_located((by, selector)))
                        if element and element.is_displayed():
                            self.log_debug(f"Found input element using: {by}={selector}", "DEBUG")
                            break
                    except:
                        continue
                
                if not element:
                    raise Exception("No input element found")
                    
                # Clear with retry
                try:
                    element.clear()
                except:
                    self.driver.execute_script("arguments[0].value = '';", element)
                
                # Type with human-like delays
                text = action['text']
                for char in text:
                    try:
                        element.send_keys(char)
                        time.sleep(random.uniform(0.1, 0.3))
                    except:
                        self.driver.execute_script(f"arguments[0].value += '{char}';", element)
                        time.sleep(random.uniform(0.1, 0.3))
                
                time.sleep(random.uniform(0.5, 1.0))
                
                # Press Enter with retry
                try:
                    element.send_keys(webdriver.Keys.RETURN)
                except:
                    self.driver.execute_script("arguments[0].form.submit();", element)
                
                return True

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
