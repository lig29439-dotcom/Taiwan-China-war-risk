# 台海情勢觀測

個人用的台海兩岸情勢儀表板：自動抓取公開資料，整理成趨勢圖與一個「暫定」綜合緊張指數。

## 資料來源

| 面向 | 來源 | 程式 |
|---|---|---|
| 軍事 | 國防部「中共解放軍臺海周邊海、空域動態」 | `scripts/fetch_mnd.py` |
| 市場 | 臺灣證券交易所（加權指數）、FRED `DEXTAUS`（新台幣匯率） | `scripts/fetch_market.py` |
| 新聞 | Google 新聞 RSS（關鍵字每日報導數）、中央社 RSS | `scripts/fetch_news.py` |

來源測試結果見 [`docs/1A-資料來源測試報告.md`](docs/1A-資料來源測試報告.md)。

## 專案結構

```
scripts/   抓資料與計算分數的 Python 程式（只用標準函式庫，不需安裝套件）
data/      抓下來的資料（CSV / JSON）；data/raw/ 保存國防部公告原文
site/      網頁（index.html）與網頁用資料 site/data/dashboard.json
docs/      規劃與測試文件
```

## 更新資料

```bash
python scripts/fetch_mnd.py        # 國防部（增量）
python scripts/fetch_market.py     # 加權指數、匯率
python scripts/fetch_news.py       # 新聞熱度（最近 3 天）
python scripts/build_site_data.py  # 產生 site/data/dashboard.json
```

第一次建置時可加 `--backfill`（國防部、市場）或 `--days 90`（新聞）回補歷史資料。

本機預覽：`cd site && python -m http.server`，再開啟 http://localhost:8000 。

## 綜合指數（暫定）

每個指標與「過去 365 天」的數值比較，換算成百分位（0–100，越高越緊張）；軍事、市場、新聞三個面向等權平均。
這只代表相對於過去一年的變化，**不是開戰機率或預測**。正式模型將在第 3 階段制定。
