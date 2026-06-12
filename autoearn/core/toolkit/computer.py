"""Computer-use and browser-automation tools.

Desktop layer (pyautogui): screenshot, mouse, keyboard — requires a DISPLAY.
Browser layer (Playwright): navigate, click, type, extract, screenshot — headless,
works on any server.

Both layers degrade to readable error strings when dependencies are missing or no
display is present, so agents can route around them automatically.

Per-agent browser contexts keep each agent's browsing session independent.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _pyautogui():
    if not _has_display():
        raise RuntimeError(
            "No display available (DISPLAY env var not set). "
            "Desktop tools require a graphical environment. "
            "Use browser_* tools instead — they run headlessly on any server."
        )
    try:
        import pyautogui  # type: ignore
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.05
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "pyautogui not installed — run: pip install pyautogui Pillow"
        )


_browser_lock = threading.Lock()
# agent_name -> {"pw": ..., "browser": ..., "page": ..., "closed": bool}
_BROWSER_CONTEXTS: dict[str, dict[str, Any]] = {}


def _page_for(agent_name: str):
    """Return (or lazily create) a Playwright page for this agent."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        raise RuntimeError(
            "playwright not installed — run: pip install playwright && playwright install chromium"
        )
    with _browser_lock:
        ctx = _BROWSER_CONTEXTS.get(agent_name)
        if ctx is None or ctx.get("closed"):
            pw = sync_playwright().__enter__()
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = browser.new_page()
            # Skip images/fonts to speed up navigation
            page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,eot}",
                lambda r: r.abort(),
            )
            _BROWSER_CONTEXTS[agent_name] = {
                "pw": pw, "browser": browser, "page": page, "closed": False
            }
            ctx = _BROWSER_CONTEXTS[agent_name]
    return ctx["page"]


# --------------------------------------------------------------------------
# Desktop — screenshot
# --------------------------------------------------------------------------

def screenshot(save_path: str = "") -> str:
    """Capture the full desktop. Returns the file path of the saved PNG."""
    pag = _pyautogui()
    path = save_path or f"/tmp/screenshot_{int(time.time())}.png"
    pag.screenshot(path)
    return path


def screen_size() -> str:
    """Return screen dimensions as JSON {width, height}."""
    pag = _pyautogui()
    w, h = pag.size()
    return json.dumps({"width": w, "height": h})


# --------------------------------------------------------------------------
# Desktop — mouse
# --------------------------------------------------------------------------

def mouse_move(x: int = 0, y: int = 0, duration: float = 0.3) -> str:
    """Move the mouse cursor to (x, y)."""
    _pyautogui().moveTo(int(x), int(y), duration=float(duration))
    return f"Moved to ({x},{y})"


def mouse_click(x: int = 0, y: int = 0, button: str = "left", clicks: int = 1) -> str:
    """Click at (x, y). button: left | right | middle. clicks: 1 or 2."""
    _pyautogui().click(int(x), int(y), button=str(button), clicks=int(clicks))
    return f"Clicked {button} at ({x},{y})"


def mouse_double_click(x: int = 0, y: int = 0) -> str:
    """Double-click at (x, y)."""
    _pyautogui().doubleClick(int(x), int(y))
    return f"Double-clicked at ({x},{y})"


def mouse_right_click(x: int = 0, y: int = 0) -> str:
    """Right-click at (x, y)."""
    _pyautogui().rightClick(int(x), int(y))
    return f"Right-clicked at ({x},{y})"


def mouse_drag(
    x1: int = 0, y1: int = 0, x2: int = 0, y2: int = 0, duration: float = 0.5
) -> str:
    """Click-and-drag from (x1,y1) to (x2,y2)."""
    pag = _pyautogui()
    pag.moveTo(int(x1), int(y1))
    pag.dragTo(int(x2), int(y2), duration=float(duration))
    return f"Dragged ({x1},{y1}) → ({x2},{y2})"


def mouse_scroll(
    x: int = 0, y: int = 0, amount: int = 3, direction: str = "down"
) -> str:
    """Scroll the mouse wheel at (x, y). direction: up | down."""
    pag = _pyautogui()
    delta = -int(amount) if direction == "down" else int(amount)
    pag.scroll(int(x), int(y), delta)
    return f"Scrolled {direction} {amount} clicks at ({x},{y})"


def get_mouse_pos() -> str:
    """Return the current mouse position as JSON {x, y}."""
    pag = _pyautogui()
    pos = pag.position()
    return json.dumps({"x": pos.x, "y": pos.y})


# --------------------------------------------------------------------------
# Desktop — keyboard
# --------------------------------------------------------------------------

def keyboard_type(text: str = "", interval: float = 0.01) -> str:
    """Type a string using the keyboard."""
    _pyautogui().typewrite(str(text), interval=float(interval))
    return f"Typed {len(text)} characters"


def keyboard_press(key: str = "") -> str:
    """Press a single key: 'enter', 'escape', 'tab', 'backspace', 'space', etc."""
    _pyautogui().press(str(key))
    return f"Pressed: {key}"


def keyboard_shortcut(keys: str = "") -> str:
    """Press a key combination. Keys joined with '+'. E.g. 'ctrl+c', 'alt+tab'."""
    pag = _pyautogui()
    parts = [k.strip() for k in str(keys).split("+")]
    pag.hotkey(*parts)
    return f"Shortcut: {keys}"


def keyboard_write_line(text: str = "") -> str:
    """Type text then press Enter."""
    pag = _pyautogui()
    pag.typewrite(str(text), interval=0.01)
    pag.press("enter")
    return f"Wrote and entered: {text[:80]}"


# --------------------------------------------------------------------------
# Browser — navigation (headless Playwright, per-agent sessions)
# --------------------------------------------------------------------------

def browser_open(agent_name: str = "", url: str = "") -> str:
    """Navigate the agent's browser to a URL."""
    try:
        page = _page_for(agent_name)
        resp = page.goto(str(url), wait_until="domcontentloaded", timeout=30000)
        status = resp.status if resp else "?"
        return f"Opened {url} → HTTP {status}, title: {page.title()}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR navigating to {url}: {exc}"


def browser_url(agent_name: str = "") -> str:
    """Return the current URL in the agent's browser."""
    try:
        return _page_for(agent_name).url
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_title(agent_name: str = "") -> str:
    """Return the current page title."""
    try:
        return _page_for(agent_name).title()
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_click(agent_name: str = "", selector: str = "") -> str:
    """Click an element by CSS selector."""
    try:
        _page_for(agent_name).click(str(selector), timeout=10000)
        return f"Clicked: {selector}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR clicking {selector}: {exc}"


def browser_type(agent_name: str = "", selector: str = "", text: str = "") -> str:
    """Fill an input field by CSS selector."""
    try:
        _page_for(agent_name).fill(str(selector), str(text), timeout=10000)
        return f"Typed into {selector}: {text[:60]}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR typing into {selector}: {exc}"


def browser_press(agent_name: str = "", key: str = "Enter") -> str:
    """Send a keyboard key to the browser (e.g. 'Enter', 'Tab', 'Escape')."""
    try:
        _page_for(agent_name).keyboard.press(str(key))
        return f"Browser key: {key}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_scroll(
    agent_name: str = "", direction: str = "down", px: int = 500
) -> str:
    """Scroll the browser page. direction: up | down. px: pixels."""
    try:
        page = _page_for(agent_name)
        delta = int(px) if direction == "down" else -int(px)
        page.evaluate(f"window.scrollBy(0, {delta})")
        return f"Scrolled browser {direction} {px}px"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_text(agent_name: str = "", selector: str = "body") -> str:
    """Extract visible text from an element (or 'body' for the full page)."""
    try:
        page = _page_for(agent_name)
        elements = page.query_selector_all(str(selector))
        texts = [el.inner_text() for el in elements[:10]]
        return ("\n---\n".join(texts))[:5000]
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_html(agent_name: str = "", selector: str = "body") -> str:
    """Get the inner HTML of an element (first match)."""
    try:
        page = _page_for(agent_name)
        el = page.query_selector(str(selector))
        return (el.inner_html() if el else "")[:4000]
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_links(agent_name: str = "") -> str:
    """Get all links on the current page as JSON array [{text, href}]."""
    try:
        page = _page_for(agent_name)
        links = page.evaluate(
            "Array.from(document.querySelectorAll('a[href]'))"
            ".map(a=>({text:a.innerText.trim().slice(0,80),href:a.href}))"
            ".filter(l=>l.href.startsWith('http')).slice(0,40)"
        )
        return json.dumps(links)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_screenshot(agent_name: str = "", path: str = "") -> str:
    """Take a screenshot of the current browser page. Returns file path."""
    try:
        page = _page_for(agent_name)
        p = path or f"/tmp/browser_{agent_name}_{int(time.time())}.png"
        page.screenshot(path=p, full_page=True)
        return p
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_js(agent_name: str = "", script: str = "") -> str:
    """Execute JavaScript in the browser and return the result as JSON."""
    try:
        result = _page_for(agent_name).evaluate(str(script))
        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_wait(
    agent_name: str = "", selector: str = "", timeout_ms: int = 10000
) -> str:
    """Wait for a CSS selector to appear on the page."""
    try:
        _page_for(agent_name).wait_for_selector(str(selector), timeout=int(timeout_ms))
        return f"Element appeared: {selector}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR waiting for {selector}: {exc}"


def browser_fill_form(
    agent_name: str = "", fields: dict | None = None
) -> str:
    """Fill multiple form fields at once. fields: {CSS_selector: value}."""
    try:
        page = _page_for(agent_name)
        for selector, value in (fields or {}).items():
            page.fill(selector, str(value))
        return f"Filled {len(fields or {})} form fields."
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_select(
    agent_name: str = "", selector: str = "", value: str = ""
) -> str:
    """Select an option in a <select> element by value."""
    try:
        _page_for(agent_name).select_option(str(selector), value=str(value))
        return f"Selected {value!r} in {selector}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_hover(agent_name: str = "", selector: str = "") -> str:
    """Hover over an element by CSS selector."""
    try:
        _page_for(agent_name).hover(str(selector), timeout=8000)
        return f"Hovered over: {selector}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_get_attribute(
    agent_name: str = "", selector: str = "", attribute: str = "href"
) -> str:
    """Get an attribute from elements matching a CSS selector, returns JSON list."""
    try:
        page = _page_for(agent_name)
        elements = page.query_selector_all(str(selector))
        results = [el.get_attribute(attribute) or "" for el in elements[:20]]
        return json.dumps(results)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def browser_close(agent_name: str = "") -> str:
    """Close and clean up the agent's browser session."""
    with _browser_lock:
        ctx = _BROWSER_CONTEXTS.pop(agent_name, None)
    if ctx and not ctx.get("closed"):
        try:
            ctx["browser"].close()
            ctx["pw"].__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        return "Browser closed."
    return "No browser open for this agent."


def browser_new_tab(agent_name: str = "") -> str:
    """Open a new tab in the agent's browser and switch to it."""
    with _browser_lock:
        ctx = _BROWSER_CONTEXTS.get(agent_name)
        if ctx is None:
            return "No browser open — call browser_open first."
        page = ctx["browser"].new_page()
        ctx["page"] = page
    return "New tab opened."


def active_browsers() -> str:
    """Return a list of agents that currently have browser sessions open."""
    with _browser_lock:
        agents = [name for name, ctx in _BROWSER_CONTEXTS.items() if not ctx.get("closed")]
    return json.dumps(agents)
