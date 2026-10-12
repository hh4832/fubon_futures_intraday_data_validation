# 官方文件核對（2026-10-12）

- SDK：https://www.fbs.com.tw/TradeAPI/llms-full.txt
- Intraday：https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/candles.txt
- Products：https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/products.txt
- Tickers：https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/tickers.txt
- Historical：https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/historical/candles/

目前官方 SDK 最新列為 2.4.0，Python 支援 3.8–3.13。
原 prompt 的 2.2.8 版本資訊已更新。依官方現有 Linux x86_64 載點固定 2.4.0。
未取得官方可信 SHA256；安裝程式只記錄實際下載 hash，不稱為官方驗證。

intraday.candles 官方參數仍為 symbol、session、timeframe，沒有歷史日期參數。
products / tickers 的 session 使用大寫；candles 夜盤使用 afterhours；日盤省略 session。
products 提供商品根代碼，tickers 提供實際月份合約，不能將 products 根代碼當月份合約。

SDK 2.4.0 新增獨立 historical/candles/{product}，文件列有日期與月份參數；
此 repo 未呼叫該 endpoint，也不生成連續合約、不擴大下載。
不得將 intraday_only 結論延伸成整個富邦 API 沒有歷史資料。

文件內 candle date 在範例出現但 Response 表未列；volume、average 是選填。
average 的含義不由欄位名稱推定，保持 unclear。
