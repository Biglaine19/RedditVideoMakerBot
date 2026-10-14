"""Shared helpers: structured logging with secret redaction, HTTP with backoff."""
import json
import logging
import re
import time
import urllib.error
import urllib.request

_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)((?:token|api[_-]?key|secret|password)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
    re.compile(r"shpat_[A-Za-z0-9]+"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload))


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


log = get_logger("dropship.http")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def http_json(url, method="GET", body=None, headers=None, retries=4, timeout=30, sleep=time.sleep):
    """JSON request with exponential backoff on network errors and 429/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "dropship-ops/0.1"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == retries:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
        except urllib.error.URLError:
            if attempt == retries:
                raise
            wait = delay
        log.warning(f"retrying {method} {url} in {wait}s (attempt {attempt + 1}/{retries})")
        sleep(wait)
        delay *= 2
