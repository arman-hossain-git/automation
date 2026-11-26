"""
core/self_healing.py
====================
Autonomous Self-Healing Web Selector Engine.

When web portals update their UI and existing XPaths break:
1. Catches NoSuchElementException and TimeoutException.
2. Extracts visible interactive elements via in-browser JS.
3. Uses local Ollama to semantically identify the target element.
4. Validates interactability with Selenium.
5. Memorizes the new XPath into learning_system.py so the user never has to
   debug, inspect elements, or provide an XPath.
"""

import logging
import time
import urllib.parse
from typing import Optional, Dict, Any, List

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
)

from core.learning_system import learning_engine
from core.ollama_engine import ollama_engine

logger = logging.getLogger(__name__)


class SelfHealingEngine:
    """Orchestrates DOM inspection and selector recovery."""

    @staticmethod
    def extract_interactive_elements(driver) -> List[Dict[str, Any]]:
        """Extracts visible buttons, inputs, links, and actionable elements from the active frame."""
        js_script = """
        return Array.from(document.querySelectorAll('button, input, a, select, [role="button"], [onclick], .btn'))
            .filter(el => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            })
            .slice(0, 45) // limit to top 45 visible interactive elements for fast LLM processing
            .map(el => {
                let xpath = '';
                if (el.id) {
                    xpath = `//*[@id="${el.id}"]`;
                } else if (el.name) {
                    xpath = `//${el.tagName.toLowerCase()}[@name="${el.name}"]`;
                } else if (el.innerText && el.innerText.trim().length > 0 && el.innerText.trim().length < 40) {
                    xpath = `//${el.tagName.toLowerCase()}[contains(normalize-space(.), "${el.innerText.trim()}")]`;
                } else {
                    xpath = `//${el.tagName.toLowerCase()}[contains(@class, "${(el.className || '').trim().split(' ')[0]}")]`;
                }
                return {
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    name: el.name || '',
                    type: el.type || '',
                    text: (el.innerText || el.value || el.title || el.getAttribute('aria-label') || '').trim().slice(0, 60),
                    xpath: xpath
                };
            });
        """
        try:
            return driver.execute_script(js_script) or []
        except Exception as e:
            logger.debug(f"Failed to execute DOM element extraction: {e}")
            return []

    @classmethod
    def heal_selector(
        cls, driver, current_url: str, action_description: str, element_hint: str = "button"
    ) -> Optional[str]:
        """
        Queries local Ollama to identify the intended element from visible page components.
        Returns a robust XPath on success, or None if Ollama cannot find a match.
        """
        try:
            if not ollama_engine.is_available():
                return None
        except Exception:
            return None

        elements = cls.extract_interactive_elements(driver)
        if not elements:
            return None

        system_prompt = (
            "You are an autonomous web automation engineer. "
            "Your task is to identify which visible HTML element corresponds to the user's intended action.\\n"
            "Analyze element text, labels, IDs, names, and tags.\\n"
            "Output JSON format strictly:\\n"
            '{"best_xpath": "//xpath_here", "confidence": 0.95, "element_text": "Label", "reasoning": "brief why"}'
        )

        user_prompt = (
            f"TARGET ACTION: '{action_description}' (Expected element type: {element_hint})\\n\\n"
            f"CURRENT VISIBLE INTERACTIVE ELEMENTS ON PAGE:\\n"
            f"{elements}\\n\\n"
            "Return the exact XPath for the single element that best performs this target action."
        )

        try:
            res = ollama_engine.generate_json(system_prompt, user_prompt, timeout=60)
            best_xpath = res.get("best_xpath")
            confidence = float(res.get("confidence", 0.0))
            if best_xpath and confidence >= 0.70:
                # Test whether the selector actually resolves in the browser
                found = driver.find_elements(By.XPATH, best_xpath)
                if found and any(f.is_displayed() for f in found):
                    return best_xpath
        except Exception as e:
            logger.debug(f"Self-healing LLM query failed: {e}")

        return None


def click_xpath_adaptive(
    driver,
    wait,
    xpath: str,
    description: str = "element",
    hover_first: bool = False,
    timeout_override: Optional[float] = None,
    log_func=None,
):
    """
    Enhanced click_xpath with autonomous self-healing.

    1. Tries the requested xpath.
    2. If failed, queries learning_engine for past successful alternatives.
    3. If still failed, invokes Ollama to inspect the live DOM, self-heal, and save the new selector.
    """
    _log = log_func or (lambda l, m: logger.log(getattr(logging, l.upper(), logging.INFO), m))
    current_url = driver.current_url or ""

    # Phase 1: Try provided XPath
    try:
        active_wait = WebDriverWait(driver, timeout_override or 5.0, poll_frequency=0.2)
        el = active_wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        except Exception:
            pass
        if hover_first:
            from selenium.webdriver.common.action_chains import ActionChains
            try:
                ActionChains(driver).move_to_element(el).perform()
                time.sleep(0.1)
            except Exception:
                pass
        el.click()
        learning_engine.record_successful_xpath(current_url, description, xpath)
        return el
    except (TimeoutException, NoSuchElementException):
        pass

    # Phase 2: Try learned historical alternatives from LearningEngine
    learned_candidates = learning_engine.get_learned_xpaths(current_url, description)
    for alt_xpath in learned_candidates:
        if alt_xpath == xpath:
            continue
        try:
            alt_el = WebDriverWait(driver, 2.0).until(EC.element_to_be_clickable((By.XPATH, alt_xpath)))
            alt_el.click()
            _log('info', f"[ADAPTIVE] Clicked using learned alternate selector for '{description}'")
            learning_engine.record_successful_xpath(current_url, description, alt_xpath)
            return alt_el
        except Exception:
            continue

    # Phase 3: Autonomous Self-Healing via Ollama
    _log('warning', f"[SELF-HEALING] Standard selector missing for '{description}'. Initiating AI recovery...")
    healed_xpath = SelfHealingEngine.heal_selector(driver, current_url, description, element_hint="button/link")
    if healed_xpath:
        try:
            healed_el = WebDriverWait(driver, 3.0).until(EC.element_to_be_clickable((By.XPATH, healed_xpath)))
            healed_el.click()
            _log('info', f"[SELF-HEALING] ✓ Successfully resolved and memorized new selector for '{description}'")
            learning_engine.record_successful_xpath(current_url, description, healed_xpath)
            return healed_el
        except Exception as e:
            _log('error', f"[SELF-HEALING] Failed to click healed selector: {e}")

    # Fallback: Raise original exception if healing fails
    raise NoSuchElementException(f"Could not locate '{description}' via standard, learned, or AI self-healing selectors.")
