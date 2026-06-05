"""Thin wrapper around the browser-use CLI."""

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
(() => document.body.innerText.match(/(\d+)精选活动/)?.[1] || '0')()
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

    def _run(self, *args: str, timeout: int = 120) -> str:
        cmd = [self._bin, "--session", self.session, *args]
        logger.debug("run: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(
                f"browser-use failed ({proc.returncode}): {' '.join(args)}\n{output}"
            )
        return output.strip()

    def close(self) -> None:
        try:
            subprocess.run(
                [self._bin, "--session", self.session, "close"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            logger.warning("close session failed", exc_info=True)

    def connect_cloud(self) -> None:
        env = os.environ.copy()
        api_key = self.config.browser_use_api_key or self._local_browser_use_api_key()
        profile_id = (
            self.config.browser_use_cloud_profile_id
            or self._local_cloud_profile_id()
        )
        if api_key:
            env["BROWSER_USE_API_KEY"] = api_key
        if profile_id:
            env["BROWSER_USE_CLOUD_PROFILE_ID"] = profile_id
        cmd = [self._bin, "--session", self.session, "cloud", "connect"]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(f"cloud connect failed:\n{output}")
        self._use_cloud = True
        logger.info("cloud browser connected")

    def import_cookies(self, path: Path) -> None:
        self._run("cookies", "import", str(path))

    def open(self, url: str) -> None:
        args = ["open", url]
        headed = not self.config.browser_headless and not self._use_cloud
        if headed:
            args = ["--headed", *args]
        self._run(*args, timeout=max(120, self.config.page_timeout_ms // 1000))

    def eval_json(self, js: str) -> Any:
        raw = self._run("eval", js, timeout=60)
        return self._parse_eval_result(raw)

    def wait_for_events(self, min_count: int = 1, timeout_s: int = 30) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                count = int(self.eval_json(
                    "document.querySelectorAll('tr.event-card').length"
                ))
                if count >= min_count:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError(f"events not loaded within {timeout_s}s")

    @staticmethod
    def _parse_eval_result(raw: str) -> Any:
        if raw.startswith("result:"):
            raw = raw[len("result:") :].strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        if raw.startswith("{'") or raw.startswith("[{"):
            try:
                import ast

                return ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                pass
        return raw

    def bootstrap(self) -> None:
        """Establish authenticated browser session."""
        self.close()
        has_cloud = (
            self.config.browser_use_api_key
            or os.getenv("BROWSER_USE_API_KEY")
            or self._local_browser_use_api_key()
        )
        if has_cloud:
            self.connect_cloud()
            return

        cookies_path = self._resolve_cookies_path()
        if cookies_path:
            self._run("open", "about:blank")
            self.import_cookies(cookies_path)
            logger.info("imported cookies from %s", cookies_path)
            return

        logger.warning("no cloud profile or cookies; proceeding without login")

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

    def scrape_page(self, url: str, page_num: int) -> list[dict[str, Any]]:
        self.open(url)
        time.sleep(self.config.cf_wait_seconds)
        self.wait_for_events()
        current = str(self.eval_json(CURRENT_PAGE_JS))
        if page_num > 1 and current != str(page_num):
            logger.warning("expected page %s, got %s", page_num, current)
        rows = self.eval_json(EXTRACT_EVENTS_JS)
        if not isinstance(rows, list):
            raise RuntimeError(f"unexpected scrape payload: {rows!r}")
        for row in rows:
            row["page"] = page_num
        return rows

    def estimate_total_pages(self, base_url: str, per_page: int = 40) -> int:
        self.open(base_url)
        time.sleep(self.config.cf_wait_seconds)
        total_raw = self.eval_json(TOTAL_EVENTS_JS)
        total = int(str(total_raw).strip() or "0")
        if total <= 0:
            return 1
        return (total + per_page - 1) // per_page
