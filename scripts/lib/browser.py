"""Thin wrapper around Playwright."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.sync_api import sync_playwright

from .config import Config

logger = logging.getLogger(__name__)

EXTRACT_EVENTS_JS = r"""
(() => {
  const cards = document.querySelectorAll('tr.event-card');
  return Array.from(cards).map(c => {
    const timeEl = c.querySelector('.eventTime');
    const titleSpan = c.querySelector('h2 .d-block');
    const venueEl = c.querySelector('.venue');
    const venueLinks = Array.from(c.querySelectorAll('.venue a')).map(a => a.innerText.trim());
    const onclick = c.querySelector('[onclick]')?.getAttribute('onclick') || '';
    const url = (onclick.match(/https[^']+/) || [])[0] || '';
    const cells = Array.from(c.querySelectorAll('td')).map(td => td.innerText.trim());
    const description = cells.find(t =>
      t.length > 20 && !t.match(/^周/) && !t.includes('贸易展览会') &&
      !t.includes('立即注册') && !t.includes('有兴趣') && !t.match(/^\d+\.\d$/)
    ) || '';
    const categoryText = cells.find(t =>
      t.includes('贸易展览会') || t.includes('会议') || t.includes('工作坊')
    ) || '';
    const categories = categoryText
      ? categoryText.replace(/贸易展览会|会议|工作坊|生产车间/g, ' ').split(/\s+/).filter(Boolean)
      : [];
    return {
      id: (c.className.match(/event_(\d+)/) || [])[1] || '',
      dates: timeEl?.innerText?.trim() || '',
      start: timeEl?.dataset?.startDate || '',
      end: timeEl?.dataset?.endDate || '',
      title: (titleSpan?.innerText || '').trim().replace(/\s+/g, ' '),
      city: venueLinks[0] || '',
      country: venueLinks[1] || venueLinks[0] || '',
      venue_text: venueEl?.innerText?.trim() || '',
      description,
      categories,
      url
    };
  });
})()
"""

TOTAL_EVENTS_JS = r"""
(() => {
  const text = document.body.innerText || '';
  const patterns = [/(\d[\d,]*)\s*精选活动/, /(\d[\d,]*)\s*场活动/, /(\d[\d,]*)\s*Events/i];
  for (const re of patterns) {
    const m = text.match(re);
    if (m) return m[1].replace(/,/g, '');
  }
  return '0';
})()
"""

CURRENT_PAGE_JS = r"""
(() => document.querySelector('.pagination .current')?.innerText?.trim() || '1')()
"""


class BrowserUse:
    def __init__(self, config: Config, session: str = "radar") -> None:
        self.config = config
        self.session = session
        self._bin = shutil.which("browser-use") or "browser-use"
        self._use_cloud = False
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _ensure_cli_config(self, api_key: str | None, profile_id: str | None) -> None:
        if api_key:
            subprocess.run(
                [self._bin, "config", "set", "api_key", api_key],
                check=True,
                capture_output=True,
                text=True,
            )
        if profile_id:
            subprocess.run(
                [
                    self._bin,
                    "config",
                    "set",
                    "cloud_connect_profile_id",
                    profile_id,
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def _stop_active_cloud_browsers(self) -> None:
        try:
            proc = subprocess.run(
                [self._bin, "cloud", "v2", "GET", "/browsers"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return
            data = json.loads(proc.stdout.strip())
            for item in data.get("items", []):
                if item.get("status") == "active" and item.get("id"):
                    subprocess.run(
                        [
                            self._bin,
                            "cloud",
                            "v2",
                            "PATCH",
                            f"/browsers/{item['id']}",
                            '{"action":"stop"}',
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    logger.info("stopped cloud browser %s", item["id"])
        except Exception:
            logger.warning("cloud browser cleanup failed", exc_info=True)

    def _connect_cloud_and_get_cdp(self, api_key: str, profile_id: str | None) -> str | None:
        self._ensure_cli_config(api_key, profile_id)
        self._stop_active_cloud_browsers()
        
        cmd = [self._bin, "--session", self.session, "cloud", "connect"]
        logger.info("Running cloud connect: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=os.environ.copy()
            )
            
            cdp_url = None
            start_time = time.time()
            while time.time() - start_time < 30:
                line = proc.stdout.readline()
                if not line:
                    break
                line_str = line.strip()
                logger.debug("cloud-connect: %s", line_str)
                if "cdp_url:" in line_str:
                    match = re.search(r"cdp_url:\s*(wss://\S+)", line_str)
                    if match:
                        cdp_url = match.group(1)
                        break
                time.sleep(0.1)
                
            if not cdp_url:
                logger.error("Failed to extract cdp_url from cloud connect output")
                proc.kill()
                return None
                
            return cdp_url
        except Exception:
            logger.exception("Error connecting to browser-use cloud")
            return None

    def bootstrap(self) -> None:
        """Establish authenticated browser session."""
        logger.info("Initializing Playwright...")
        self._playwright = sync_playwright().start()

        # Check for cloud connection credentials
        api_key = self.config.browser_use_api_key or self._local_browser_use_api_key()
        profile_id = (
            self.config.browser_use_cloud_profile_id
            or self._local_cloud_profile_id()
        )
        
        cdp_url = None
        if api_key:
            logger.info("Attempting to connect to Browser Use Cloud...")
            cdp_url = self._connect_cloud_and_get_cdp(api_key, profile_id)

        if cdp_url:
            logger.info("Connecting Playwright over Cloud CDP: %s", cdp_url)
            self.browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            self._use_cloud = True
            self.context = self.browser.contexts[0]
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        else:
            logger.info("Launching local Chromium (headless=%s)", self.config.browser_headless)
            self.browser = self._playwright.chromium.launch(
                headless=self.config.browser_headless,
                channel="chrome" if not self.config.browser_headless else None,
                args=["--disable-blink-features=AutomationControlled"]
            )
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )

            cookies_path = self._resolve_cookies_path()
            if cookies_path:
                try:
                    cookies_raw = json.loads(cookies_path.read_text(encoding="utf-8"))
                    if isinstance(cookies_raw, dict):
                        cookies_list = cookies_raw.get("cookies", [])
                    elif isinstance(cookies_raw, list):
                        cookies_list = cookies_raw
                    else:
                        cookies_list = []

                    sanitized_cookies = []
                    for cookie in cookies_list:
                        if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
                            continue

                        sanitized = {
                            "name": str(cookie["name"]),
                            "value": str(cookie["value"]),
                        }

                        if "domain" in cookie:
                            sanitized["domain"] = str(cookie["domain"])
                        else:
                            sanitized["domain"] = ".10times.com"

                        if "path" in cookie:
                            sanitized["path"] = str(cookie["path"])
                        if "expires" in cookie:
                            try:
                                val = cookie["expires"]
                                if val is not None:
                                    sanitized["expires"] = float(val)
                            except (ValueError, TypeError):
                                pass
                        if "httpOnly" in cookie:
                            sanitized["httpOnly"] = bool(cookie["httpOnly"])
                        if "secure" in cookie:
                            sanitized["secure"] = bool(cookie["secure"])
                        if "sameSite" in cookie:
                            val = str(cookie["sameSite"])
                            if val in {"Lax", "None", "Strict"}:
                                sanitized["sameSite"] = val

                        sanitized_cookies.append(sanitized)

                    if sanitized_cookies:
                        self.context.add_cookies(sanitized_cookies)
                        logger.info("Imported %d cookies from %s", len(sanitized_cookies), cookies_path)
                    else:
                        logger.warning("No valid cookies found in %s", cookies_path)
                except Exception:
                    logger.exception("Failed to load cookies from %s", cookies_path)
            else:
                logger.warning("No cookies file found; proceeding without login")

            self.page = self.context.new_page()

    def close(self) -> None:
        logger.info("Closing Playwright browser...")
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
        except Exception:
            logger.warning("Error closing browser/context", exc_info=True)
        finally:
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self.page = None
            self.context = None
            self.browser = None
            
        if self._use_cloud:
            try:
                subprocess.run(
                    [self._bin, "--session", self.session, "close"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except Exception:
                logger.warning("close session failed", exc_info=True)
            self._use_cloud = False

    @staticmethod
    def _read_browser_use_config() -> dict[str, Any]:
        cfg = Path.home() / ".browser-use" / "config.json"
        if not cfg.exists():
            return {}
        try:
            return json.loads(cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    @classmethod
    def _local_browser_use_api_key(cls) -> str | None:
        return cls._read_browser_use_config().get("api_key")

    @classmethod
    def _local_cloud_profile_id(cls) -> str | None:
        return cls._read_browser_use_config().get("cloud_connect_profile_id")

    def _resolve_cookies_path(self) -> Path | None:
        if self.config.tentimes_cookies_path and self.config.tentimes_cookies_path.exists():
            return self.config.tentimes_cookies_path
        if self.config.tentimes_cookies_b64:
            import base64

            path = self.config.state_path.parent / "imported_cookies.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(self.config.tentimes_cookies_b64))
            return path
        local = self.config.state_path.parent / "10times_cookies.json"
        if local.exists():
            return local
        return None

    def open(self, url: str) -> None:
        logger.info("Navigating to %s", url)
        self.page.goto(url, timeout=self.config.page_timeout_ms)

    def eval_json(self, js: str) -> Any:
        return self.page.evaluate(js)

    def wait_for_events(self, min_count: int = 1, timeout_s: int = 30) -> None:
        logger.info("Waiting for event cards to load...")
        try:
            self.page.wait_for_selector("tr.event-card", state="attached", timeout=timeout_s * 1000)
        except Exception as exc:
            count = self.page.locator("tr.event-card").count()
            if count >= min_count:
                return
            
            # Check if the page has loaded standard structural elements (meaning it's not a connection/load failure)
            is_loaded = self.page.locator("footer, #footer, .pagination, header, #header").count() > 0
            
            # Check if we are stuck on a Cloudflare challenge page
            title = self.page.title() or ""
            is_cf = any(term in title for term in ["请稍候", "Cloudflare", "Just a moment", "Verify", "DDoS", "Checking your browser"])
            
            if is_loaded and not is_cf:
                logger.info("Page loaded successfully but contains 0 event cards (end of pagination).")
                return
                
            raise TimeoutError(f"events not loaded within {timeout_s}s (found {count} cards, title: {title})") from exc

    @staticmethod
    def page_url(base_url: str, page: int) -> str:
        parsed = urlparse(base_url)
        query = parse_qs(parsed.query)
        if page <= 1:
            query.pop("page", None)
        else:
            query["page"] = [str(page)]
        new_query = urlencode({k: v[0] for k, v in query.items()})
        return urlunparse(parsed._replace(query=new_query, fragment=""))

    def scrape_page(self, url: str, page_num: int, *, retries: int = 2) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self.open(url)
                if self.config.cf_wait_seconds > 0:
                    logger.info("Sleeping %ds for Cloudflare bypass...", self.config.cf_wait_seconds)
                    time.sleep(self.config.cf_wait_seconds)
                self.wait_for_events(timeout_s=45)
                current = str(self.eval_json(CURRENT_PAGE_JS))
                if page_num > 1 and current not in {str(page_num), "undefined", ""}:
                    logger.warning("expected page %s, got %s", page_num, current)
                rows = self.eval_json(EXTRACT_EVENTS_JS)
                if not isinstance(rows, list):
                    raise RuntimeError(f"unexpected scrape payload: {rows!r}")
                for row in rows:
                    row["page"] = page_num
                return rows
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "scrape page %s attempt %s failed: %s",
                    page_num,
                    attempt + 1,
                    exc,
                )
                time.sleep(3)
        raise last_error or RuntimeError(f"failed to scrape page {page_num}")

    def estimate_total_pages(self, base_url: str, per_page: int = 40) -> int:
        self.open(base_url)
        if self.config.cf_wait_seconds > 0:
            logger.info("Sleeping %ds for Cloudflare bypass (estimating pages)...", self.config.cf_wait_seconds)
            time.sleep(self.config.cf_wait_seconds)
        total_raw = self.eval_json(TOTAL_EVENTS_JS)
        total = int(str(total_raw).strip() or "0")
        if total <= 0:
            return 1
        return (total + per_page - 1) // per_page
