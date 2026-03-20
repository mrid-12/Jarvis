"""
ActionExecutor: Executes UI actions from the Gemini model.

After each action, the caller (BackendConnection) sends an immediate
screenshot back to Gemini so it can observe the result and self-correct.
"""

import logging
import time
import pyautogui

logger = logging.getLogger(__name__)

# Safety settings
pyautogui.FAILSAFE = False   # corners are valid targets for this agent
pyautogui.PAUSE = 0.1

# Characters that pyautogui.write() can't handle well (send via typewrite)
_SLOW_TYPE_THRESHOLD = 0    # always use typewrite for reliability


class ActionExecutor:
    def execute_action(self, action_dict: dict) -> tuple[bool, str]:
        """
        Execute a single UI action.

        action_dict keys (from the execute_ui_action tool declaration):
            action_type : click | type | key | scroll | status
            thought     : str   (reasoning, for logging)
            x, y        : int   (pixel coordinates)
            button      : str   (left | right | middle)
            text        : str   (text to type or status message)
            key         : str   (key name, e.g. "enter", "ctrl+c", "win+d")
            amount      : int   (scroll clicks, positive=up, negative=down)
        """
        try:
            action_type = action_dict.get("action_type") or action_dict.get("type")
            thought = action_dict.get("thought", "")
            logger.info("[Action] %s — %s", action_type, thought)

            if action_type == "click":
                return self._click(action_dict)

            elif action_type == "type":
                return self._type(action_dict)

            elif action_type == "key":
                return self._key(action_dict)

            elif action_type == "scroll":
                return self._scroll(action_dict)

            elif action_type == "status":
                return True, action_dict.get("text", "Done.")

            elif action_type == "speak":
                return self._speak(action_dict)

            elif action_type == "open_app":
                return self._open_app(action_dict)

            else:
                return False, f"Unknown action_type: {action_type!r}"

        except pyautogui.FailSafeException:
            return False, "FailSafe triggered (mouse in corner). Aborting."
        except Exception as e:
            logger.exception("Action execution error")
            return False, f"Error: {e}"

    # ── Individual action handlers ─────────────────────────────────────────

    def _click(self, a: dict) -> tuple[bool, str]:
        x, y = a.get("x"), a.get("y")
        if x is None or y is None:
            return False, "click: missing x or y"
            
        # Safety: Check for out-of-bounds or failed grounding (-1, -1)
        if x < 0 or y < 0:
            logger.warning("Aborting click: Negative coordinates (%d, %d)", x, y)
            return False, f"click: out-of-bounds or grounding failure at ({x}, {y})"

        button = a.get("button", "left")
        double = a.get("double", False)

        # Smooth move first so Gemini's coordinate estimate has time to land
        pyautogui.moveTo(x, y, duration=0.25, tween=pyautogui.easeOutQuad)
        time.sleep(0.05)

        if double:
            pyautogui.doubleClick(x, y, button=button)
        else:
            pyautogui.click(x, y, button=button)

        logger.info("  → clicked (%d, %d) %s", x, y, button)
        return True, f"Clicked ({x}, {y}) {button}"

    def _type(self, a: dict) -> tuple[bool, str]:
        text = a.get("text", "")
        if not text:
            return False, "type: empty text"

        # Use pyautogui.write for ASCII, but typewrite is same thing
        # interval gives a small per-char delay making it reliable
        pyautogui.write(text, interval=0.04)
        logger.info("  → typed %d chars", len(text))
        return True, f"Typed {len(text)} chars"

    def _key(self, a: dict) -> tuple[bool, str]:
        key_str = a.get("key", "").strip()
        if not key_str:
            return False, "key: empty key"

        # Sanitize common model outputs to pyautogui equivalents
        key_str = key_str.lower()
        key_str = key_str.replace("control", "ctrl")
        key_str = key_str.replace("command", "command") # Mac
        key_str = key_str.replace("window", "win")
        key_str = key_str.replace("windows", "win")
        key_str = key_str.replace("escape", "esc")
        key_str = key_str.replace("delete", "del")
        
        # Support combo keys e.g. "ctrl+c", "win+d", "shift+home"
        if "+" in key_str:
            # Remove any spaces around the + sign
            parts = [k.strip() for k in key_str.split("+")]
            pyautogui.hotkey(*parts)
            logger.info("  → hotkey %s", "+".join(parts))
        else:
            # Handle multiword key names Gemini sometimes sends: "left arrow" → "left"
            key_clean = key_str.replace(" ", "")
            pyautogui.press(key_clean)
            logger.info("  → key %s", key_clean)

        return True, f"Key: {key_str}"

    def _scroll(self, a: dict) -> tuple[bool, str]:
        amount = a.get("amount")
        if amount is None:
            # More aggressive default for visiblity
            amount = -5
            
        import platform
        # Reduced multiplier for more controlled scrolling. 
        # Windows Wheel Delta is 120, but 100 is a smoother 'unit'.
        multiplier = 100 if platform.system() == "Windows" else 15
        final_amount = int(amount * multiplier)
             
        x, y = a.get("x"), a.get("y")

        # Move and Click to ensure focus before scrolling
        if x is not None and y is not None and x >= 0 and y >= 0:
            logger.info("  → Scrolling: Focusing at (%d, %d)", x, y)
            pyautogui.click(x, y) # Focus the window/container
            time.sleep(0.05)
            
            # Use multiple small scrolls to ensure the app registers the movement
            steps = 4
            step_amount = int(final_amount / steps)
            for _ in range(steps):
                pyautogui.scroll(step_amount)
                time.sleep(0.01)
        else:
            # If no grounding, scroll at current mouse position but still click to focus
            logger.info("  → Scrolling %d at current mouse position (with focus click)", final_amount)
            pyautogui.click() 
            pyautogui.scroll(final_amount)

        logger.info("  → Finished scroll: %d total", final_amount)
        return True, f"Scrolled {final_amount}"

    def _open_app(self, a: dict) -> tuple[bool, str]:
        app_name = a.get("app_name", "").strip()
        if not app_name:
            return False, "open_app: empty app_name"

        import subprocess
        import os
        
        logger.info("  → launching app: %s", app_name)
        try:
            # Try Windows start command which hooks into system PATH and search
            import platform
            if platform.system() == "Windows":
                # Special cases for modern Windows Apps
                if "discord" in app_name.lower():
                    # Discord often installs to AppData, let the shell resolve it generically
                    subprocess.Popen(f"start {app_name}", shell=True)
                elif "brave" in app_name.lower() or "browser" in app_name.lower():
                    subprocess.Popen("start brave", shell=True)
                elif "chrome" in app_name.lower():
                    subprocess.Popen("start chrome", shell=True)
                elif "settings" in app_name.lower():
                    subprocess.Popen("start ms-settings:", shell=True)
                else:
                    # Generic fallback using start
                    subprocess.Popen(f"start {app_name}", shell=True)
            else:
                return False, "open_app currently only fully supported on Windows"
        except Exception as e:
            return False, f"Failed to launch app: {e}"

        return True, f"Launched {app_name}"

    def _speak(self, a: dict) -> tuple[bool, str]:
        text = a.get("text", "")
        logger.info("  → speaking: %s", text)
        return True, text
