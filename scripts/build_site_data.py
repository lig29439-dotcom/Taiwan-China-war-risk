"""整合各來源資料，計算綜合緊張指數（正式模型 v1），輸出 site/data/dashboard.json。

模型說明（方法說明頁與此處一致）：
  1. 每個指標取「近 7 日平均」（市場取 5／20 日變化）。
  2. 與「截至當天為止的全部歷史」比較，換算成百分位（0–100，越高越緊張）；
     只用當天以前的資料，不偷看未來。
  3. 面向分數 = 該面向各指標百分位的平均。
  4. 綜合分數 = 軍事 60%＋市場 20%＋新聞 20%。軍事缺資料時不計分（顯示資料不足）；
     市場或新聞缺資料時，其權重按比例分給其餘面向。
  5. 緊張程度級距以「綜合分數自己的歷史分布」定義：
     <P50 平穩、P50–75 略升、P75–90 偏高、P90–97 高、≥P97 極高。
  6. 另外獨立標示「單日突發」：近 7 天內，共機／逾越中線／共艦的單日值達歷史 95 百分位者。
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import json
from collections import defaultdict

from common import DATA, ROOT, log

OUT = ROOT / "site" / "data" / "dashboard.json"


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def read_csv(name: str) -> list[dict]:
    p = DATA / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rolling_mean(series: dict[dt.date, float], days: int) -> dict[dt.date, float]:
    """以日曆天計的近 N 日平均（只平均有資料的日子）。"""
    keys = sorted(series)
    out = {}
    for k in keys:
        lo = bisect.bisect_left(keys, k - dt.timedelta(days=days - 1))
        hi = bisect.bisect_right(keys, k)
        vals = [series[x] for x in keys[lo:hi]]
        out[k] = sum(vals) / len(vals)
    return out


def pct_change(series: dict[dt.date, float], days: int) -> dict[dt.date, float]:
    keys = sorted(series)
    out = {}
    for k in keys:
        i = bisect.bisect_right(keys, k - dt.timedelta(days=days)) - 1
        if i >= 0:
            out[k] = (series[k] / series[keys[i]] - 1) * 100
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


WEIGHTS = {"military": 0.6, "market": 0.2, "news": 0.2}
NAMES = {"military": "軍事", "market": "市場", "news": "新聞輿論"}
LEVEL_NAMES = ["平穩", "略升", "偏高", "高", "極高"]
LEVEL_PCTS = [50, 75, 90, 97]  # 綜合分數歷史分布的分界百分位
SPIKE_PCT = 95
STALE_DAYS = 10


def expanding_percentiles(series: dict[dt.date, float], min_obs: int = 60) -> dict[dt.date, float]:
    """每個日期的值，在「截至該日（含）全部歷史」中的百分位。"""
    ref: list[float] = []
    out = {}
    for k in sorted(series):
        bisect.insort(ref, series[k])
        if len(ref) >= min_obs:
            out[k] = round(100 * bisect.bisect_right(ref, series[k]) / len(ref), 1)
    return out


def latest_on(series: dict, day: dt.date):
    """day 當天或之前最近一筆（超過 STALE_DAYS 視為沒有）。回傳 (日期, 值)。"""
    keys = [k for k in series if k <= day and (day - k).days <= STALE_DAYS]
    if not keys:
        return None, None
    k = max(keys)
    return k, series[k]


def quantile(sorted_vals: list[float], pct: float) -> float:
    i = min(len(sorted_vals) - 1, int(pct / 100 * len(sorted_vals)))
    return sorted_vals[i]


def level_of(score, thresholds):
    if score is None:
        return "資料不足"
    return LEVEL_NAMES[sum(score >= t for t in thresholds)]


def main() -> None:
    # ---- 軍事：只用新版格式且解析成功的公告；同日多篇取最後一篇
    mnd = {}
    for r in read_csv("mnd.csv"):
        if r["format"] == "current" and r["parse_ok"] == "True":
            mnd[r["date"]] = r
    mnd_rows = [mnd[k] for k in sorted(mnd)]
    aircraft = {d(r["date"]): float(r["aircraft"]) for r in mnd_rows}
    crossing = {d(r["date"]): float(r["aircraft_crossing"]) for r in mnd_rows}
    ships = {d(r["date"]): float(r["ships"]) for r in mnd_rows}

    # ---- 市場：加權指數 5 日跌幅（取負號，跌越多越高）、新台幣 20 日貶值幅度
    taiex = {d(r["date"]): float(r["value"]) for r in read_csv("taiex.csv")}
    twd = {d(r["date"]): float(r["value"]) for r in read_csv("twd.csv")}
    taiex_drop = {k: -v for k, v in pct_change(taiex, 5).items()}
    twd_weak = pct_change(twd, 20)  # DEXTAUS = 1 美元兌多少台幣，上升代表台幣貶值

    # ---- 新聞：所有關鍵字加總
    news_by_kw: dict[str, dict[str, int]] = defaultdict(dict)
    total = defaultdict(float)
    for r in read_csv("news_counts.csv"):
        news_by_kw[r["keyword"]][r["date"]] = int(r["count"])
        total[d(r["date"])] += int(r["count"])

    indicators = {
        "military": {
            "共機架次（7日均）": rolling_mean(aircraft, 7),
            "逾越中線/進入空域（7日均）": rolling_mean(crossing, 7),
            "共艦艘數（7日均）": rolling_mean(ships, 7),
        },
        "market": {
            "加權指數 5 日跌幅": taiex_drop,
            "新台幣 20 日貶值幅度": twd_weak,
        },
        "news": {
            "關鍵字新聞量（7日均）": rolling_mean(dict(total), 7),
        },
    }
    # 事先算好每個指標的歷史百分位序列（新聞歷史較短，門檻放寬為 30 筆）
    pcts = {key: {lb: expanding_percentiles(s, 30 if key == "news" else 60) for lb, s in inds.items()}
            for key, inds in indicators.items()}

    def score_on(day):
        dims = {}
        for key, inds in indicators.items():
            parts = []
            for label, s in inds.items():
                _, p = latest_on(pcts[key][label], day)
                k, v = latest_on(s, day)
                parts.append({"name": label, "percentile": p, "value": round(v, 2) if v is not None else None})
            dims[key] = {"name": NAMES[key], "weight": WEIGHTS[key], "score": mean(p["percentile"] for p in parts), "parts": parts}
        if dims["military"]["score"] is None:
            return None, dims  # 軍事缺資料：不計分
        avail = {k: v for k, v in dims.items() if v["score"] is not None}
        wsum = sum(WEIGHTS[k] for k in avail)
        overall = round(sum(v["score"] * WEIGHTS[k] for k, v in avail.items()) / wsum, 1)
        return overall, dims

    today = max([max(s) for s in (aircraft, taiex, twd) if s] + [dt.date.today() - dt.timedelta(days=1)])
    overall, dims = score_on(today)

    # 全部歷史分數（缺資料日為 null）；級距門檻取自這份歷史
    first = min(k for k in pcts["military"]["共機架次（7日均）"])
    history, vals, hist_rows = [], [], []
    day = first
    while day <= today:
        o, dd = score_on(day)
        history.append([day.isoformat(), o])
        hist_rows.append([day.isoformat(), o] + [dd[k]["score"] for k in ("military", "market", "news")])
        if o is not None:
            vals.append(o)
        day += dt.timedelta(days=1)
    vals.sort()
    thresholds = [quantile(vals, p) for p in LEVEL_PCTS]

    # ---- 單日突發：近 7 天，單日值達歷史 95 百分位
    daily = {"共機架次": aircraft, "逾越中線/進入空域": crossing, "共艦艘數": ships}
    spikes = []
    for label, s in daily.items():
        for k, p in expanding_percentiles(s).items():
            if today - dt.timedelta(days=6) <= k <= today and p >= SPIKE_PCT and s[k] > 0:
                spikes.append({"date": k.isoformat(), "name": label, "value": s[k], "percentile": p})
    spikes.sort(key=lambda x: (x["date"], x["percentile"]), reverse=True)

    # ---- 今日最新資訊
    headlines_path = DATA / "headlines.json"
    headlines = json.loads(headlines_path.read_text(encoding="utf-8")) if headlines_path.exists() else []
    daily_pct = {lb: expanding_percentiles(s_) for lb, s_ in daily.items()}
    air_avg7 = rolling_mean(aircraft, 7)
    today_info = None
    if mnd_rows:
        last = d(mnd_rows[-1]["date"])
        prev = d(mnd_rows[-2]["date"]) if len(mnd_rows) > 1 else None
        cur = {"date": last.isoformat(), "aircraft": int(aircraft[last]), "crossing": int(crossing[last]), "ships": int(ships[last]),
               "aircraft_prev": int(aircraft[prev]) if prev and (last - prev).days == 1 else None,
               "aircraft_avg7": round(air_avg7[last], 1),
               "aircraft_pct": daily_pct["共機架次"].get(last), "crossing_pct": daily_pct["逾越中線/進入空域"].get(last),
               "ships_pct": daily_pct["共艦艘數"].get(last)}
        prev_overall = next((o for dday, o in reversed(history[:-1]) if o is not None), None)
        txt = f"{cur['date']} 國防部公告：共機 {cur['aircraft']} 架次（逾越中線／進入空域 {cur['crossing']} 架次）、共艦 {cur['ships']} 艘。"
        if cur["aircraft_pct"] is not None:
            rel = "高於" if cur["aircraft"] > cur["aircraft_avg7"] else "低於" if cur["aircraft"] < cur["aircraft_avg7"] else "等於"
            txt += f"共機架次{rel}近 7 日平均（{cur['aircraft_avg7']}），位於歷史第 {cur['aircraft_pct']:.0f} 百分位。"
        if overall is not None:
            txt += f"綜合指數 {overall}（{level_of(overall, thresholds)}）"
            if prev_overall is not None:
                diff = round(overall - prev_overall, 1)
                txt += f"，較前一日{'上升' if diff > 0 else '下降' if diff < 0 else '持平'}" + (f" {abs(diff)}" if diff else "")
            txt += "。"
        if spikes and spikes[0]["date"] == cur["date"]:
            txt += "今日有單日突發：" + "、".join(f"{x['name']}{x['value']:.0f}" for x in spikes if x["date"] == cur["date"]) + "。"
        today_info = dict(cur, summary=txt, headlines=headlines[:5])
    fresh = {"mnd": max(aircraft).isoformat() if aircraft else None, "taiex": max(taiex).isoformat() if taiex else None,
             "twd": max(twd).isoformat() if twd else None, "news": max(total).isoformat() if total else None}

    # ---- 歷史查閱資料：每日分數、事件、新聞標題存檔
    events_path = DATA / "events.json"
    archive_path = DATA / "headlines_archive.json"
    archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.exists() else []
    by_date: dict[str, list] = defaultdict(list)
    for h in archive:
        by_date[h["time"][:10]].append({"time": h["time"], "title": h["title"], "link": h.get("link", ""), "source": h.get("source", "")})
    history_out = {
        "as_of": today.isoformat(),
        "thresholds": thresholds, "level_names": LEVEL_NAMES,
        "days": hist_rows,
        "events": json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else [],
        "headlines": by_date,
        "headlines_since": min(by_date) if by_date else None,
    }
    (OUT.parent / "history.json").write_text(json.dumps(history_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    news_dates = sorted({k for kw in news_by_kw.values() for k in kw})
    status_path = DATA / "status.json"
    out = {
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "as_of": today.isoformat(),
        "score": {"overall": overall, "level": level_of(overall, thresholds), "provisional": False, "dims": dims,
                  "thresholds": thresholds, "level_names": LEVEL_NAMES, "level_pcts": LEVEL_PCTS},
        "score_history": history,
        "spikes": spikes,
        "today": today_info,
        "freshness": fresh,
        "series": {
            "mnd": [{"date": r["date"], "aircraft": int(r["aircraft"]), "crossing": int(r["aircraft_crossing"]),
                     "ships": int(r["ships"]), "official": int(r["official_ships"]), "balloons": int(r["balloons"])}
                    for r in mnd_rows],
            "taiex": [[k.isoformat(), v] for k, v in sorted(taiex.items())],
            "twd": [[k.isoformat(), v] for k, v in sorted(twd.items())],
            "news": {"dates": news_dates,
                     "keywords": {kw: [vals_.get(x, 0) for x in news_dates] for kw, vals_ in news_by_kw.items()}},
        },
        "headlines": headlines,
        "status": json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("輸出 %s：綜合分數 %s（%s），截至 %s；級距門檻 %s；單日突發 %d 筆",
             OUT.name, overall, level_of(overall, thresholds), today, thresholds, len(spikes))


if __name__ == "__main__":
    main()
