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
