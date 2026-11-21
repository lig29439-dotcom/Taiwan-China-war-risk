"""新聞熱度：用 Google 新聞 RSS 計算每個關鍵字每天的報導數；另存中央社兩岸新聞標題。

輸出：
  data/news_counts.csv   每日 × 關鍵字 的報導數（以不重複標題計）
  data/headlines.json    最近的相關新聞標題（網頁「最新消息」用）
用法：
  python scripts/fetch_news.py              # 重算最近 3 個完整日
  python scripts/fetch_news.py --days 90    # 回補 90 天
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from common import DATA, fetch, log, write_status

KEYWORDS = ["共機", "共艦", "軍演", "圍台", "封鎖", "制裁", "統戰", "國台辦"]
COUNTS_PATH = DATA / "news_counts.csv"
HEADLINES_PATH = DATA / "headlines.json"
CAP = 100  # Google 新聞 RSS 單次最多回傳 100 則；達上限代表實際數量可能更多
TPE = dt.timezone(dt.timedelta(hours=8))


def gnews(query: str) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"})
    root = ET.fromstring(fetch(url))
    items = []
    for it in root.findall(".//item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": it.findtext("link") or "",
            "source": it.findtext("source") or "",
            "time": parsedate_to_datetime(it.findtext("pubDate")).astimezone(TPE).isoformat(),
        })
    return items


def count_day(keyword: str, day: dt.date) -> tuple[int, list[dict]]:
    nxt = day + dt.timedelta(days=1)
    items = gnews(f"{keyword} after:{day} before:{nxt}")
    # 只計算該日（台北時間）發布、標題不重複的報導
    seen, kept = set(), []
    for i in items:
        key = i["title"].rsplit(" - ", 1)[0]
        if i["time"][:10] == day.isoformat() and key not in seen:
            seen.add(key)
            kept.append(i)
    return len(kept), kept


def load_counts() -> dict[tuple[str, str], dict]:
    if not COUNTS_PATH.exists():
        return {}
    with COUNTS_PATH.open(encoding="utf-8") as f:
        return {(r["date"], r["keyword"]): r for r in csv.DictReader(f)}


def save_counts(data: dict) -> None:
    with COUNTS_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "keyword", "count", "capped"])
        w.writeheader()
        for k in sorted(data):
            w.writerow(data[k])


def cna_headlines() -> list[dict]:
    root = ET.fromstring(fetch("https://feeds.feedburner.com/rsscna/mainland"))
    out = []
    for it in root.findall(".//item"):
        out.append({"title": (it.findtext("title") or "").strip(), "link": it.findtext("link") or "",
                    "source": "中央社", "time": parsedate_to_datetime(it.findtext("pubDate")).astimezone(TPE).isoformat()})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    args = ap.parse_args()

    today = dt.datetime.now(TPE).date()
    days = [today - dt.timedelta(days=i) for i in range(args.days, 0, -1)]  # 不含今天（未滿一天）
    data = load_counts()
    recent: list[dict] = []
    errors = 0
    for day in days:
        for kw in KEYWORDS:
            try:
                n, items = count_day(kw, day)
                data[(day.isoformat(), kw)] = {"date": day.isoformat(), "keyword": kw, "count": n, "capped": int(n >= CAP)}
                if day >= today - dt.timedelta(days=2):
                    recent += [dict(i, keyword=kw) for i in items]
            except Exception as e:
                errors += 1
                log.error("%s %s 失敗：%s", day, kw, e)
            time.sleep(1.5)
        log.info("新聞熱度 %s 完成", day)
        save_counts(data)  # 每天存一次，中斷也保留進度

    try:
        recent = cna_headlines() + recent
    except Exception as e:
        log.error("中央社 RSS 失敗：%s", e)
        errors += 1
    uniq = {}
    for h in sorted(recent, key=lambda h: h["time"], reverse=True):
        uniq.setdefault(h["title"].rsplit(" - ", 1)[0], h)
    HEADLINES_PATH.write_text(json.dumps(list(uniq.values())[:60], ensure_ascii=False, indent=1), encoding="utf-8")
    # 累積存檔：每天新增的標題併入 headlines_archive.json（以標題去重），供網頁「歷史查閱」使用
    arch_path = DATA / "headlines_archive.json"
    arch = json.loads(arch_path.read_text(encoding="utf-8")) if arch_path.exists() else []
    seen = {h["title"].rsplit(" - ", 1)[0] for h in arch}
    arch += [h for k, h in uniq.items() if k not in seen]
    arch.sort(key=lambda h: h["time"])
    arch_path.write_text(json.dumps(arch, ensure_ascii=False, indent=0), encoding="utf-8")

    total = len(days) * len(KEYWORDS) + 1
    msg = f"查詢 {total} 次，失敗 {errors} 次；資料涵蓋至 {days[-1]}"
    ok = errors <= total * 0.1
    write_status("news", ok, msg)
    log.info(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
