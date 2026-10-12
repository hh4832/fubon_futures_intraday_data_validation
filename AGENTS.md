# 工作規範
本 repo 僅驗證市場行情資料，不研究策略，不加入下單功能。
GitHub 為 source of truth；Python 3.11、Google Colab 為主要環境。
官方文件與 observed behavior 分開；未觀察欄位不得猜測。
不得製造連續期貨、換月調整或補分鐘；observed gap 不等於資料缺漏。
交易日歸屬與 bucket alignment 必須實證；不得僅因 API 有資料宣稱可歷史回測。
所有結論可追溯 raw response、設定與 Git commit；Drive 每次建立獨立 Asia/Taipei timestamp archive。
Secrets、憑證、binary wheel 與研究輸出不得 commit。
登入與 SDK 失敗立即停止；個別行情端點失敗需記錄並繼續其他驗證。
驗證使用向量化；測試不依賴 SDK 或帳密。修改後執行 pytest 與 Notebook 靜態檢查。

## Historical phase
連續合約必須先驗證 rollover；1! 不可直接視為可回測的 continuous return series。
嚴格依 phase 1→7 gates；FAIL/UNRESOLVED 阻擋後續，不能把未執行項目當 PASS。
不得 silent back-adjustment、forward fill、刪除 mismatch 或改寫 canonical intraday output。
historical vs intraday 是 provider internal consistency；external validation 需獨立標示。
單日 PASS 不等於多年 PASS；固定年度樣本不得因結果不佳更換。
Documented fact / Observed fact / Inference / Unresolved 必須區分。
夜盤交易日映射須使用具來源 session windows，不能將週末/假日視為普通 +1 天。
calendar 超出已審核範圍必須停止；calendar/reference count 不能充當 confirmed missing-data 證據。
