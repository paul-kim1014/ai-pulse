"""공통 유틸: 설정·.env 읽기, 날짜(KST), HTTP 재시도, 로깅."""
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))

log = logging.getLogger("ai_pulse")


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)


_cfg_cache = {}


def cfg(name):
    """config/<name>.yaml 을 읽어 캐시한다."""
    if name not in _cfg_cache:
        with open(ROOT / "config" / f"{name}.yaml", encoding="utf-8") as f:
            _cfg_cache[name] = yaml.safe_load(f) or {}
    return _cfg_cache[name]


def load_env():
    """.env를 직접 파싱한다 (bash source 시 특수문자 깨짐 방지)."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def today_kst():
    return datetime.now(KST).date()


def parse_date(s):
    """ISO 날짜/시각 문자열 → date (UTC 기준 날짜). 없으면 None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _retryable(e):
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(e, (httpx.TransportError, httpx.TimeoutException))


_client = None


def client():
    global _client
    if _client is None:
        headers = {"User-Agent": "ai-pulse/1.0 (personal research dashboard)"}
        _client = httpx.Client(timeout=60, headers=headers, follow_redirects=True)
    return _client


@retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(5),
       wait=wait_exponential(multiplier=2, min=2, max=60), reraise=True)
def http_get(url, params=None, headers=None):
    r = client().get(url, params=params, headers=headers)
    if r.status_code == 429:
        wait = int(r.headers.get("retry-after", "0") or 0)
        if wait:
            time.sleep(min(wait, 120))
    r.raise_for_status()
    return r


@retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(6),
       wait=wait_exponential(multiplier=2, min=3, max=90), reraise=True)
def http_post_json(url, body, params=None, headers=None):
    r = client().post(url, params=params, json=body, headers=headers)
    r.raise_for_status()
    return r.json()


def dump_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
