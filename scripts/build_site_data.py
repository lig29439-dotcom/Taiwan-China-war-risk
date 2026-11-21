"""整合各來源資料，計算「暫定」綜合分數，輸出 site/data/dashboard.json。

暫定評分方法（第 3 階段會換成正式模型）：
  每個指標取「近 7 日平均」（市場取 5/20 日變化），
  與過去 365 天的同類數值比較，換算成百分位（0–100，越高越緊張）。
  三個面向（軍事、市場、新聞）各自平均後，再等權平均成綜合分數。
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import json
from collections import defaultdict

from common import DATA, ROOT, log

OUT = ROOT / "site" / "data" / "dashboard.json"
WINDOW = 365
LEVELS = [(80, "高"), (60, "偏高"), (40, "中"), (0, "低")]


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


def percentile_at(series: dict[dt.date, float], day: dt.date, min_obs: int = 30) -> float | None:
    """day 當天（或之前最近一筆）的值，在過去 365 天中的百分位。"""
    keys = sorted(series)
    i = bisect.bisect_right(keys, day) - 1
    if i < 0 or (day - keys[i]).days > 10:  # 資料太舊就不算
        return None
    lo = bisect.bisect_left(keys, day - dt.timedelta(days=WINDOW))
    ref = [series[k] for k in keys[lo:i + 1]]
    if len(ref) < min_obs:
        return None
    cur = series[keys[i]]
    return round(100 * sum(1 for v in ref if v <= cur) / len(ref), 1)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def level(score):
    if score is None:
        return "資料不足"
    return next(name for th, name in LEVELS if score >= th)


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
    names = {"military": "軍事", "market": "市場", "news": "新聞輿論"}

    def score_on(day):
        dims = {}
        for key, inds in indicators.items():
            parts = []
            for label, s in inds.items():
                keys = [k for k in s if k <= day]
                parts.append({"name": label, "percentile": percentile_at(s, day),
                              "value": round(s[max(keys)], 2) if keys else None})
            dims[key] = {"name": names[key], "score": mean(p["percentile"] for p in parts), "parts": parts}
        overall = mean(v["score"] for v in dims.values())
        return overall, dims

    today = max([max(s) for s in (aircraft, taiex, twd) if s] + [dt.date.today() - dt.timedelta(days=1)])
    overall, dims = score_on(today)
    history = []
    for i in range(WINDOW, -1, -1):
        day = today - dt.timedelta(days=i)
        o, _ = score_on(day)
        if o is not None:
            history.append([day.isoformat(), o])

    news_dates = sorted({k for kw in news_by_kw.values() for k in kw})
    status_path = DATA / "status.json"
    headlines_path = DATA / "headlines.json"
    out = {
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "as_of": today.isoformat(),
        "score": {"overall": overall, "level": level(overall), "provisional": True, "dims": dims},
        "score_history": history,
        "series": {
            "mnd": [{"date": r["date"], "aircraft": int(r["aircraft"]), "crossing": int(r["aircraft_crossing"]),
                     "ships": int(r["ships"]), "official": int(r["official_ships"]), "balloons": int(r["balloons"])}
                    for r in mnd_rows],
            "taiex": [[k.isoformat(), v] for k, v in sorted(taiex.items())],
            "twd": [[k.isoformat(), v] for k, v in sorted(twd.items())],
            "news": {"dates": news_dates,
                     "keywords": {kw: [vals.get(x, 0) for x in news_dates] for kw, vals in news_by_kw.items()}},
        },
        "headlines": json.loads(headlines_path.read_text(encoding="utf-8")) if headlines_path.exists() else [],
        "status": json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("輸出 %s：綜合分數 %s（%s），截至 %s", OUT.name, overall, level(overall), today)


if __name__ == "__main__":
    main()
