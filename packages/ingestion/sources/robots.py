"""Minimal, correct robots.txt parser.

The stdlib `urllib.robotparser` mishandles `Allow:` lines that precede
`Disallow:` (documented Python bug). Since we only hit a handful of
well-known hosts, we implement our own parser — small, deterministic,
easy to debug.

Rules (RFC 9309 semantics):
    User-agent: *
    Allow: /public/
    Disallow: /private/

Precedence:
    1. Longest matching rule wins.
    2. If an Allow and a Disallow have the same length, Allow wins.
    3. No matching rule → allow.

If robots.txt is missing (404) → allow all.
If robots.txt can't be fetched → deny (conservative) but cache the decision.

Cached per host.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class RobotsRules:
    """Per-user-agent rules for one host."""
    user_agent: str
    allow: list[re.Pattern] = field(default_factory=list)
    disallow: list[re.Pattern] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class HostRobots:
    """All rules for one host."""
    host: str
    rules_by_ua: dict[str, RobotsRules] = field(default_factory=dict)
    sitemaps: list[str] = field(default_factory=list)

    def for_user_agent(self, ua: str) -> RobotsRules | None:
        """Find the most specific matching rule for this UA."""
        # Exact match first
        if ua in self.rules_by_ua:
            return self.rules_by_ua[ua]
        # Prefix match — for our use, just check the first token
        ua_token = ua.split("/")[0].strip().lower()
        for key, rules in self.rules_by_ua.items():
            if key == "*":
                continue
            if key.lower() in ua_token or ua_token in key.lower():
                return rules
        # Fall back to wildcard
        return self.rules_by_ua.get("*")


def _compile_pattern(path: str) -> re.Pattern:
    """Convert a robots.txt path pattern to a regex.

    Supports `*` wildcard and `$` end anchor.
    """
    # Escape regex specials except our wildcards
    pattern = re.escape(path)
    # Restore wildcard
    pattern = pattern.replace(r"\*", ".*")
    # Restore end anchor
    if pattern.endswith(r"\$"):
        pattern = pattern[:-2] + "$"
    return re.compile("^" + pattern)


def _parse_robots(text: str) -> dict[str, RobotsRules]:
    """Parse robots.txt into per-UA rules."""
    rules: dict[str, RobotsRules] = {}
    current_uas: list[str] = []
    # Track directives we encounter until we hit a new User-agent
    pending_allow: list[str] = []
    pending_disallow: list[str] = []
    pending_delay: float | None = None

    def flush() -> None:
        nonlocal pending_allow, pending_disallow, pending_delay
        for ua in current_uas:
            if ua not in rules:
                rules[ua] = RobotsRules(user_agent=ua)
            rules[ua].allow.extend(_compile_pattern(p) for p in pending_allow)
            rules[ua].disallow.extend(_compile_pattern(p) for p in pending_disallow)
            if pending_delay is not None and rules[ua].crawl_delay is None:
                rules[ua].crawl_delay = pending_delay
        pending_allow = []
        pending_disallow = []
        pending_delay = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            # Start a new user-agent group; flush the previous
            if pending_allow or pending_disallow or pending_delay is not None:
                flush()
                current_uas = []
            current_uas.append(value)
        elif key == "allow":
            if current_uas:
                pending_allow.append(value)
        elif key == "disallow":
            if current_uas:
                pending_disallow.append(value)
        elif key == "crawl-delay":
            if current_uas and value:
                try:
                    pending_delay = float(value)
                except ValueError:
                    pass
    # Final flush
    if current_uas:
        flush()

    return rules


def _fetch_robots(host: str) -> str | None:
    """Fetch robots.txt for a host. Returns None on failure."""
    settings = get_settings()
    scheme = "https"
    url = f"{scheme}://{host}/robots.txt"
    req = urllib.request.Request(url, headers={"User-Agent": settings.sec_user_agent})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.info("robots.not_found", host=host)
            return ""  # empty robots = allow all
        log.warning("robots.http_error", host=host, code=exc.code)
        return None
    except Exception as exc:
        log.warning("robots.fetch_failed", host=host, error=str(exc)[:200])
        return None


@lru_cache(maxsize=128)
def _get_host_rules(host: str) -> HostRobots | None:
    """Get (cached) robots rules for a host. None = couldn't fetch (deny)."""
    text = _fetch_robots(host)
    if text is None:
        return None
    if text == "":
        return HostRobots(host=host, rules_by_ua={})  # no rules = allow all
    rules = _parse_robots(text)
    return HostRobots(host=host, rules_by_ua=rules)


def can_fetch(url: str, user_agent: str) -> bool:
    """Return True if the given UA may fetch this URL."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        host_rules = _get_host_rules(host)
        if host_rules is None:
            # Couldn't fetch robots.txt — conservative deny
            log.warning("robots.deny_unknown", url=url)
            return False
        if not host_rules.rules_by_ua:
            # Empty robots.txt — allow all
            return True

        rules = host_rules.for_user_agent(user_agent)
        if rules is None:
            return True  # no matching rule → allow

        # Find longest matching rule (allow or disallow)
        best_allow_len = -1
        best_disallow_len = -1
        for pat in rules.allow:
            if pat.search(path):
                best_allow_len = max(best_allow_len, len(pat.pattern))
        for pat in rules.disallow:
            if pat.search(path):
                best_disallow_len = max(best_disallow_len, len(pat.pattern))

        if best_disallow_len == -1 and best_allow_len == -1:
            return True  # no rule matched → allow
        # Allow wins ties
        return best_allow_len >= best_disallow_len
    except Exception as exc:
        log.warning("robots.check_failed", url=url, error=str(exc)[:200])
        return False  # conservative


def crawl_delay(url: str, user_agent: str) -> float | None:
    """Return the crawl delay for this URL, if any."""
    try:
        parsed = urlparse(url)
        host_rules = _get_host_rules(parsed.netloc)
        if host_rules is None:
            return None
        rules = host_rules.for_user_agent(user_agent)
        return rules.crawl_delay if rules else None
    except Exception:
        return None


def clear_cache() -> None:
    _get_host_rules.cache_clear()
