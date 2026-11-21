"""共用工具：HTTP 抓取（含重試）、路徑、紀錄。只用 Python 標準函式庫。"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
# 不自訂 User-Agent：實測 FRED 會拒絕瀏覽器樣式的 UA，國防部會拒絕自訂名稱，Python 預設值兩者都接受。

TPE = dt.timezone(dt.timedelta(hours=8))  # 台北時間

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dashboard")


def fetch(url: str, *, retries: int = 4, timeout: int = 40, backoff: float = 5.0) -> str:
    """抓取網址並回傳文字；失敗時以遞增間隔重試，最後仍失敗就丟出例外。"""
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            wait = backoff * (2 ** i)
            log.warning("抓取失敗 (%s/%s) %s：%s，%ss 後重試", i + 1, retries, url, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"多次重試仍失敗：{url}") from last


def html_to_text(page: str) -> str:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def roc_to_iso(roc: str) -> str:
    """民國日期 '115.11.21' → '2026-11-21'。"""
    y, m, d = (int(x) for x in roc.split("."))
    return f"{y + 1911:04d}-{m:02d}-{d:02d}"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def write_status(source: str, ok: bool, message: str) -> None:
    """記錄各來源最近一次的抓取狀態，供網頁顯示資料健康度。"""
    path = DATA / "status.json"
    status = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    status[source] = {"ok": ok, "message": message, "checked_at": dt.datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")}
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
