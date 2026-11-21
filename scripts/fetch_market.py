"""抓取市場資料：證交所加權指數（每日收盤）與 FRED 新台幣兌美元匯率。

輸出：data/taiex.csv、data/twd.csv
用法：
  python scripts/fetch_market.py              # 更新最近 2 個月
  python scripts/fetch_market.py --backfill   # 從 2020-09 起回補
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time

from common import DATA, fetch, log, write_status

TAIEX_PATH = DATA / "taiex.csv"
TWD_PATH = DATA / "twd.csv"
START = dt.date(2020, 9, 1)


def load(path) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {r["date"]: float(r["value"]) for r in csv.DictReader(f)}


def save(path, data: dict[str, float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for d in sorted(data):
            w.writerow([d, data[d]])


def months(since: dt.date):
    d = since.replace(day=1)
    today = dt.date.today()
    while d <= today:
        yield d
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def update_taiex(backfill: bool) -> str:
    """逐月抓取；每月存檔一次。被證交所暫時封鎖時停止，下次執行從缺的月份接續。"""
    data = load(TAIEX_PATH)
    have = {k[:7] for k in data}
    this_month = f"{dt.date.today():%Y-%m}"
    recent = dt.date.today() - dt.timedelta(days=45)
    todo = [m for m in months(START if backfill else recent)
            if f"{m:%Y-%m}" not in have or m >= recent.replace(day=1) or f"{m:%Y-%m}" == this_month]
    blocked = False
    for m in todo:
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={m:%Y%m}01&response=json"
        try:
            j = json.loads(fetch(url, retries=2, backoff=10))
        except (RuntimeError, json.JSONDecodeError) as e:
            log.warning("證交所 %s 失敗（可能暫時封鎖），停止本次回補：%s", f"{m:%Y-%m}", e)
            blocked = True
            break
        if j.get("stat") == "OK":
            for row in j["data"]:  # ["114/11/03", 成交股數, 成交金額, 成交筆數, 加權指數, 漲跌點數]
                y, mo, d = (int(x) for x in row[0].split("/"))
                data[f"{y + 1911:04d}-{mo:02d}-{d:02d}"] = float(row[4].replace(",", ""))
            save(TAIEX_PATH, data)
        time.sleep(6)  # 證交所對頻繁請求會封鎖，放慢速度
    if not data:
        raise RuntimeError("沒有任何加權指數資料")
    missing = sum(1 for m in months(START) if f"{m:%Y-%m}" not in {k[:7] for k in data})
    msg = f"加權指數 {len(data)} 筆，最新 {max(data)}"
    if missing:
        msg += f"；尚缺 {missing} 個月歷史，之後的排程會自動補齊"
    if blocked and max(data) < (dt.date.today() - dt.timedelta(days=5)).isoformat():
        raise RuntimeError(msg + "（證交所暫時封鎖，最新資料可能延遲）")
    return msg


def update_twd() -> str:
    text = fetch("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXTAUS")
    data = {}
    for line in text.splitlines()[1:]:
        d, _, v = line.partition(",")
        if d >= START.isoformat() and v not in ("", "."):
            data[d] = float(v)
    if not data:
        raise RuntimeError("FRED 回傳內容沒有資料")
    save(TWD_PATH, data)
    return f"新台幣匯率 {len(data)} 筆，最新 {max(data)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()
    code = 0
    for name, fn in (("taiex", lambda: update_taiex(args.backfill)), ("twd", update_twd)):
        try:
            msg = fn()
            log.info(msg)
            write_status(name, True, msg)
        except Exception as e:  # 單一來源失敗不影響其他來源
            log.error("%s 失敗：%s", name, e)
            write_status(name, False, str(e))
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
