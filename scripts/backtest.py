"""回測：檢查已知重大軍演前後，綜合指數是否明顯上升。用法：python scripts/backtest.py"""
import datetime as dt
import json

from common import ROOT

EVENTS = [("2023-04-08", "聯合利劍（蔡英文會見麥卡錫後）"), ("2023-08-19", "賴清德過境美國期間軍演"),
          ("2024-05-23", "聯合利劍-2024A"), ("2024-10-14", "聯合利劍-2024B"),
          ("2025-04-01", "海峽雷霆-2025A"), ("2025-12-29", "正義使命-2025")]

D = json.loads((ROOT / "site/data/dashboard.json").read_text(encoding="utf-8"))
H = {d: v for d, v in D["score_history"]}
th = D["score"]["thresholds"]
names = D["score"]["level_names"]


def lv(s):
    return "資料不足" if s is None else names[sum(s >= t for t in th)]


for day, label in EVENTS:
    d0 = dt.date.fromisoformat(day)
    before = [H.get((d0 - dt.timedelta(days=i)).isoformat()) for i in range(15, 5, -1)]
    before = [x for x in before if x is not None]
    window = [H.get((d0 + dt.timedelta(days=i)).isoformat()) for i in range(-2, 6)]
    window = [x for x in window if x is not None]
    base = sum(before) / len(before) if before else None
    peak = max(window) if window else None
    print(f"{day} {label}：事前基準 {base and round(base,1)}，前後峰值 {peak}（{lv(peak)}），"
          f"變化 {None if base is None or peak is None else round(peak-base,1)}")
