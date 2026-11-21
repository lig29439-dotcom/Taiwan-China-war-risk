"""抓取國防部「中共解放軍臺海周邊海、空域動態」。

原文存進 data/raw/mnd.jsonl（解析規則改了可以重跑），解析結果存進 data/mnd.csv。

用法：
  python scripts/fetch_mnd.py              # 增量更新：抓到已存在的公告為止
  python scripts/fetch_mnd.py --backfill   # 回補全部歷史（約 2,000 篇，需數十分鐘）
  python scripts/fetch_mnd.py --reparse    # 不連網，只用已存原文重新解析
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from common import DATA, RAW, fetch, html_to_text, log, read_jsonl, roc_to_iso, write_jsonl, write_status

BASE = "https://www.mnd.gov.tw"
RAW_PATH = RAW / "mnd.jsonl"
CSV_PATH = DATA / "mnd.csv"
DELAY = 1.0  # 每次請求間隔（秒），避免造成對方負擔
FIELDS = ["date", "id", "aircraft", "aircraft_crossing", "ships", "official_ships", "balloons", "format", "parse_ok"]

ITEM_RE = re.compile(r'href="(?:/)?news/plaact/(\d+)"[^>]*>(.*?)</a>', re.S)


def list_page(page: int) -> list[dict]:
    url = f"{BASE}/news/plaactlist" + (f"/{page}" if page > 1 else "")
    items = []
    for m in ITEM_RE.finditer(fetch(url)):
        title = html_to_text(m.group(2))
        dm = re.search(r"(1\d{2}\.\d{2}\.\d{2})", title)
        if dm:
            items.append({"id": int(m.group(1)), "date": roc_to_iso(dm.group(1)), "title": title})
    return items


def detail_text(item_id: int) -> str:
    text = html_to_text(fetch(f"{BASE}/news/plaact/{item_id}"))
    start = text.find("發布單位")
    end = text.find("關鍵字", start)
    return text[start:end if end > 0 else start + 1500].strip()


def _num(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def parse(rec: dict) -> dict:
    """從原文解析數字。以「迄 0600 時止」每日完整統計的格式（2022-11 起）才視為可比較資料，視為可比較資料。"""
    t = rec["text"]
    row = {"date": rec["date"], "id": rec["id"], "aircraft": None, "aircraft_crossing": None,
           "ships": None, "official_ships": None, "balloons": None, "format": "legacy", "parse_ok": False}
    if re.search(r"迄(?:今日)?\s*\d{4}\s*時止", t):  # 「前日 0600 至當日 0600」的每日完整統計
        row["format"] = "current"
        row["aircraft"] = _num(r"共機\s*(\d+)\s*架次?", t)
        row["ships"] = _num(r"(?:共)?艦\s*(\d+)\s*艘", t)
        # 「活動動態」只寫共艦、沒提共機數量的日子，代表當天未偵獲共機
        if row["aircraft"] is None and (re.search(r"(未偵獲|無偵獲|無)共機", t) or row["ships"] is not None):
            row["aircraft"] = 0
        # 只寫共機、沒提共艦數量的日子，代表當天未偵獲共艦
        if row["ships"] is None and row["aircraft"] is not None:
            row["ships"] = 0
        # 「逾越中線」或「進入…空域」的架次
        row["aircraft_crossing"] = _num(r"(?:逾越中線|進入)[^0-9（）()]{0,30}?(\d+)\s*架次?", t)
        if row["aircraft_crossing"] is None and row["aircraft"] is not None:
            row["aircraft_crossing"] = 0
        row["official_ships"] = _num(r"公務船\s*(\d+)\s*艘", t) or 0
        row["balloons"] = _num(r"氣球[^0-9]{0,8}(\d+)\s*顆", t) or 0
        row["parse_ok"] = row["aircraft"] is not None and row["ships"] is not None
    return row


def save_csv(records: list[dict]) -> list[dict]:
    rows = sorted((parse(r) for r in records), key=lambda r: (r["date"], r["id"]))
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--reparse", action="store_true")
    args = ap.parse_args()

    records = read_jsonl(RAW_PATH)
    known = {r["id"] for r in records}

    if not args.reparse:
        # 1) 先掃列表頁，找出還沒抓過的公告
        todo, seen, page, max_pages = [], set(), 1, 400 if args.backfill else 10
        while page <= max_pages:
            items = list_page(page)
            # 超過最後一頁時網站會重複回傳最後一頁，遇到看過的公告就停
            if not items or items[0]["id"] in seen:
                break
            seen.update(i["id"] for i in items)
            fresh = [i for i in items if i["id"] not in known]
            todo += fresh
            if page % 20 == 0 or not args.backfill:
                log.info("列表第 %s 頁（%s），待抓 %s 篇", page, items[-1]["date"], len(todo))
            if not args.backfill and len(fresh) < len(items):
                break  # 增量模式：遇到已存在的公告就停
            page += 1
            time.sleep(DELAY)

        # 2) 以少量併發抓內文（3 條連線、每條間隔 DELAY 秒），每 100 篇存檔一次
        def grab(it):
            try:
                return dict(it, text=detail_text(it["id"]))
            except Exception as e:  # 單篇失敗就略過，下次執行會再補抓
                log.error("公告 %s 抓取失敗：%s", it["id"], e)
                return None
            finally:
                time.sleep(DELAY)

        with ThreadPoolExecutor(max_workers=3) as pool:
            for n, it in enumerate(pool.map(grab, todo), 1):
                if it:
                    records.append(it)
                if n % 100 == 0:
                    write_jsonl(RAW_PATH, sorted(records, key=lambda r: r["id"]))
                    log.info("內文已抓 %s / %s 篇", n, len(todo))
        write_jsonl(RAW_PATH, sorted(records, key=lambda r: r["id"]))

    rows = save_csv(records)
    current = [r for r in rows if r["format"] == "current"]
    failed = [r for r in current if not r["parse_ok"]]
    latest = rows[-1]["date"] if rows else "無"
    msg = f"共 {len(rows)} 篇，新版格式 {len(current)} 篇，解析失敗 {len(failed)} 篇，最新 {latest}"
    log.info(msg)
    # 新版格式中有超過 2% 解析失敗，視為網站格式可能改變
    ok = bool(current) and len(failed) <= max(2, len(current) * 0.02)
    write_status("mnd", ok, msg)
    if failed:
        log.warning("解析失敗的公告 id：%s", [r["id"] for r in failed[:20]])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
