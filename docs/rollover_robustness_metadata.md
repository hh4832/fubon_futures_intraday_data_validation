# Fixed rollover metadata (reviewed 2026-10-13)

Documented fact: TAIFEX final settlement date records establish the following monthly expiries. Settlement prices are not used for external price validation.

| Months | Expiry | Fixed regular trade-date window |
|---|---|---|
| 202606 → 202607 | 2026-06-17 | 2026-06-11 → 2026-06-24 |
| 202608 → 202609 | 2026-08-19 | 2026-08-13 → 2026-08-25 |
| 202609 → 202610 | 2026-09-16 | 2026-09-10 → 2026-09-22 |
| 202512 → 202601 | 2025-12-17 | 2025-12-11 → 2025-12-23 |
| 202406 → 202407 | 2024-06-19 | 2024-06-13 → 2024-06-25 |
| 202006 → 202007 | 2020-06-17 | 2020-06-11 → 2020-06-23 |

Expiry source URLs are stored per sample in YAML (TAIFEX `cht/5/futIndxFSP`, commodityIds=1, exact month/year end and prior-year start). The selection includes four reviewed trading dates before and after expiry; 2026-06-19 is excluded (Dragon Boat holiday), so that window ends June 24.

Trading calendars: [2026](https://www.taifex.com.tw/file/taifex/CHINESE/4/2026Calendar.pdf), [2025 revised](https://www.taifex.com.tw/file/taifex/CHINESE/4/2025Calendar_cv1.pdf), [2024 revised](https://www.taifex.com.tw/file/taifex/CHINESE/4/2024Calendar_cv1.pdf). Scoped 2020 holiday evidence: [TAIFEX 109-year Dragon Boat notice](https://www.taifex.com.tw/chinese/11/attach/109%E5%B9%B4%E7%AB%AF%E5%8D%88%E7%AF%80%E8%AA%BF%E6%95%B4%E4%BC%91%E5%B8%82%E5%85%AC%E5%91%8A.pdf): June 25–26 closed; June 20 make-up working day still no trading. The fixed 2020 window ends June 23; no holiday within it. These are scheduled references, not proof that no unscheduled halt occurred.

Session sources: [TAIFEX night introduction](https://www.taifex.com.tw/cht/4/aHIntroduction), [historical launch introduction](https://report.taifex.com.tw/file/taifex/event/cht/AfterHoursTrading/index.html), [TX contract specification](https://www.taifex.com.tw/cht/2/tX). TX regular 08:45–13:45, expiring month last day ends 13:30; night 15:00–next calendar 05:00 and belongs to the next regular session. Expiring domestic month is not listed that expiry afternoon/night.

Inference: reviewed scheduled dates plus that rule generate the explicitly stored night windows; no generic calendar +1 mapping. Example trade_date 2026-06-22 maps June 18 15:00–June 19 05:00; June 19/20/21 do not become regular trading dates. Both sides of midnight retain their real calendar_date. API `from/to` date semantics must be verified from returned timestamps; out-of-window responses are archived and marked UNRESOLVED, never silently trimmed or retried on a different date range. A future exception requires source review, not a post-result sample replacement.

Observed fact must come from a formal run. No synthetic result is market evidence. Shared scheduled-slot absences cannot distinguish no-trade suppression from provider omissions; asymmetric active underlying absences and fully absent requested continuous sessions block promotion. Inactive expired months can be empty without degrading the continuous source. A special 13:45 bar remains archived and flagged even on expiry day.
