# Final Cross-Year Historical Integrity Validation

Baseline: d5b7cf2d16b832c9cf6b86363e2016ebbc5386cf; canonical rollover run 20261014_073758_d5b7cf2d16b8. This final entrypoint does not execute previous phase runners or strategy code.

## Predeclared matrix

Ten years (2017–2026), three windows per year: first five official trading dates on/after March 1 (2017: July 1); nine official trading dates centered on June expiry (2017: September); first five official dates on/after November 1. Ordinary windows are before the monthly expiry week. There are 190 primary trading dates; five bounded June 2017 regular availability dates are separate. Boundary night queries begin June 2 trade_date, whose clock session starts June 1, avoiding clock dates before the month-only documented availability.

2017 fallback is fixed before any provider request: official provider documentation declares only 2017-06, not a verified first day; therefore full June rollover coverage cannot be guaranteed a priori. September is the specified deterministic fallback. A failed window is never substituted.

Calendar PDFs (all retrieved and inspected from TAIFEX), URLs and original byte hashes are in final_calendar_provenance.json. Expiry dates were independently verified against the official TAIFEX TX final settlement table from 2017-09 through 2026-06; no settlement prices are used for selection or price validation. PDFs are source evidence, not committed binary files.

Historical exceptions are part of the frozen calendar:

- 2017-06-03 and 2018-06-16 were Saturday make-up regular trading days (visible in the official calendars). Effective 2019, Saturday make-up days no longer trade: [official rule notice](https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/%E7%B6%B2%E9%A0%81%E5%85%AC%E5%91%8A%E9%85%8D%E5%90%88%E9%8A%80%E8%A1%8C%E6%A5%AD%E8%A3%9C%E8%A1%8C%E4%B8%8A%E7%8F%AD%E6%97%A5%E4%B8%8D%E4%BA%A4%E6%98%93%E4%BA%A4%E5%89%B2%E7%9B%B8%E9%97%9C%E6%B3%95%E8%A6%8F%E4%BF%AE%E6%AD%A3.pdf).
- [Original afterhours rule](https://www.taifex.com.tw/file/taifex/event/cht/AfterHoursTrading/%E5%B8%82%E5%A0%B4%E8%A6%8F%E7%AB%A0.pdf): make-up Saturday does not have a following afterhours session. Accordingly mapped trade_dates 2017-06-05 and 2018-06-19 have afterhours_scheduled=false, not an unexplained missing full night. Their regular dates remain in the samples and queries. Analysis of the night rollover uses only the explicitly scheduled night windows; raw retrieval window is unchanged.
- 2019-03-01 and 2021-03-01 holiday closures affect first March sample dates; 2018-06-18, 2021-06-14, 2023-06-22/23 and 2026-06-19 affect June windows. The 2018 June window includes trading Saturday June 16, rather than treating weekdays as the exchange calendar.
- [TAIFEX 2024-10-31 typhoon closure announcement](https://www.taifex.com.tw/cht/11/newsDetail?idx=15446&newsType=1): regular and following afterhours closed. The Nov 1 trade-date night window is the preceding open regular day's Oct 30 15:00–Oct 31 05:00, assigned to next regular session Nov 1; the cancelled Oct 31 15:00 window is not invented. The rule is next regular session, not calendar +1. Any observed contrary trade-date metadata stops promotion and requires source review, not an automatic repair.

Other unscheduled closures cannot be inferred from annual calendar colors. Frozen input file hashes and config fingerprints are verified before every request; calendar errors stop the entire run. No date is edited after observing provider results.

## Readiness

On 2026-10-14, the 2026 ordinary-late sample is future data (Nov 2–6). Full final validation is intentionally blocked before all scheduled sessions complete (last regular close: 2026-11-06 13:45 Asia/Taipei). It cannot be replaced with an earlier window or marked PASS. The offline freeze CLI creates a separate preflight artifact; it does not create a successful formal archive.

## Request accounting

100 primary requests: 10 years × (two ordinary windows × two sessions + one rollover × two sessions × three contracts). Six exact repeats: 2017/2021/2026 ordinary-early, both sessions, 1!. Two bounded 2017 availability queries. Total 108 logical historical.candles requests; max three attempts each, sequential, timeframe=1, TXF, asc, canonical OHLCVA fields. No intraday, product discovery or provider higher-timeframe requests. Independent official daily HTTP downloads are counted separately from Fubon requests.

## Independent external definitions

The official daily download form at https://www.taifex.com.tw/cht/3/futDailyMarketView explicitly submits to https://www.taifex.com.tw/cht/3/futDataDown with down_type=1, commodity_id=TX, commodity_id2, queryStartDate and queryEndDate. This schema was read from the official form, not invented. One bounded single-day request is attempted per frozen ordinary-early first date. Original response bytes, request parameters, source URL, definition URL, hashes and download/schema errors are archived. If old dates are unavailable, no broker or blog replacement is allowed; external status stays UNRESOLVED.

TAIFEX regular and night records are distinct; night trading dates are the volume attribution date (next regular session), per the official page's note. The adapter selects exact TX monthly contract and session. High/low extrema and identity/date are comparable under that definition. Daily close/settlement and volume inclusion of spread/combination trades have not been proven equivalent to Fubon minute fields here; their comparisons are explicitly NOT COMPARABLE, never failed solely for definition mismatch. Positive external gating requires actual compatible high/low evidence in both sessions for every sampled year. Entirely NOT COMPARABLE or unavailable evidence cannot promote the dataset.

No external matching results have been claimed by implementation or synthetic fixtures. External daily agreement still cannot prove every minute correct.

## Interpretation boundaries

Original source order, raw types/schema, duplicates, terminal bars and all bad rows are preserved. Normalized dtypes may safely vary integer/float; raw and normalized signatures are separate. Missing average cannot be silently treated as zero or erase OHLC evidence. Expiring monthly regular reference closes 13:30, other months 13:45; 05:00 exact terminal policy is reused, 05:01 stays outside.

Scheduled-slot absence is not confirmed loss. Annual rollover compares active reference timestamp asymmetry independently of inactive sparse/expired months. Missing requested continuous sessions, unexplained identity/mixing/transformations, repeat differences or compatible external contradictions block promotion. Shared omissions remain caveats, not asserted no-trade intervals.

Internal 5/15/60 aggregation uses canonical session anchors on the predetermined first scheduled ordinary-early session. It verifies resampling against independently computed bucket groupby, repeat determinism and volume conservation. Incomplete/mixed/duplicate input is not replaced with another day. Missing and partial buckets remain visible; an exact close may become a labelled terminal-only bucket. This is not provider higher-timeframe validation.

Synthetic tests are offline engineering verification. A final PASS is eligible only after all formal gates, repeatability, availability and independent external checks; it is sampled validation, not an exhaustive proof of 2017–2026. Preserve raw identity/provenance and never compute returns across different contract months.
