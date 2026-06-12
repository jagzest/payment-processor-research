# payment-processor-research

Research project on the `PaymentPatternsAggregatorV2` trended-feature
denominator: the `percent_<rate>_<window>_months` features divide by months
that were never observed, biasing the rates toward 0. While testing the fix
we also found that Experian's `placeholder: "-"` was deleting real months
from the payment pattern.

The changes are committed on the **`payment_processor_change`** branch of
both repos:
[model-engine](https://github.com/Katlean/model-engine/tree/payment_processor_change)
(commit `d747ffb8`) and
[feature-engine-parts](https://github.com/Katlean/feature-engine-parts/tree/payment_processor_change)
(commit `32ca0541`). `CHANGES.md` has the full walkthrough;
`RESULTS_SUMMARY.md` has the original experiment verdict.

## The changes

### model-engine changes

For each equifax/cms_6, experian/arf7 and transunion/TU4R we added
`missing_data_chars` (the bureau's "no rating observed this month" code) with
a `notes` field citing the bureau spec, and for experian and transunion we
removed the `placeholder`:

- [equifax](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/equifax/cms_6/fe2/trade.json#L285-L315)
  `["*"]` — "Rate/Status was not available for that month"
  (STS TotalView Programming Guide, p. 3-30)
- [experian](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/experian/arf7/fe2/trade.json#L374-L398)
  `["-"]` — "No update received" (CIS Cross Reference Guide,
  Appendix T "Payment Profile Indicators", Segment 357.B4.5, p. 139)
- [transunion/TU4R](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/transunion/TU4R/fe2/trade.json#L435-L459)
  `["X"]` — "no data received from a subscriber for the month or
  when a trade account is in dispute" (TU4.1 User Guide, Appendix C,
  pp. 839-840)
- placeholder removed for experian — per Appendix T the `-` is a real month,
  not formatting, so stripping it misaligned the history
- placeholder removed for transunion — the TU pattern character set
  (`1-5, E, X, J, K, H, G, L, Y`; Appendix C, pp. 838-840) contains no `/`;
  it was copied from the equifax asset and never occurs in TU data

### feature-engine-parts changes

All in [`payment_pattern_aggregator.py`](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py):

- We adjusted payment-processor (`PaymentPatternsAggregatorV2`) so that it
  reads `missing_data_chars` from the asset. See
  [here](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L62).
  This is a required input, so every asset that uses
  `PaymentPatternsAggregatorV2` must declare it (pass `[]` to disable the
  exclusion).
- We updated `_get_effective_month_range` so that it takes in
  `missing_data_chars` and a variable called `all_month_range`. If
  `all_month_range` is True it only excludes months where the missing data
  character is trailing. Else it will exclude all months with a missing data
  character from the month range. See
  [here](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L184-L189).
- The `percent_<rate>` denominators call `_get_effective_month_range` with
  `all_month_range=False` because these denominators count observed months —
  a month with a missing-data character was not observed no matter where it
  sits in the window, and leaving it in would count it as an observed
  paid-as-agreed month, biasing the rate toward 0. We also set the
  `month_range` for this call to the length of the payment-pattern string
  because Experian and TransUnion produce strings shorter than the nominal
  window for sparse histories, so subtracting from the nominal window would
  count positions that don't exist. See
  [here](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L211-L216).
  - Note, we also updated `_get_count` to wrap its values in `re.escape`,
    since `Series.str.count` compiles the pattern as a regex and Equifax's
    `*` would raise `re.error: nothing to repeat`. See
    [here](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L169-L170).
- However for the `ppt_len_name` (`payment_history_length`), our call of
  `_get_effective_month_range` leaves `all_month_range=True` because that
  feature measures the entire history the account existed: only the trailing
  run of missing-data characters is time before the account opened, while a
  mid-string gap is still a month the account was alive (the bureau just got
  no update), so it stays in the count. See
  [here](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L248-L249).
- Importantly, **that trailing exclusion is no longer a hardcoded `*` for every
  bureau**: the old code always passed `exclude_trailing=["*"]`, and since `*`
  never appears in experian/TU patterns the subtraction never fired for them
  — it only ever worked for equifax. The trailing exclusion is now based on
  each bureau's own missing-data character (`*` equifax, `-` experian, `X`
  transunion), so trailing "no update" runs subtract for all three bureaus.

## The experian placeholder change affects MORE than the percent features

With the dashes no longer deleted, every month stays at its true calendar
position — so window membership and string positions shift, which moves
**every payment-pattern feature family**, not just the denominators:

- **`number_of_<rate>_in_last_<m>_months`** — a delinquency can move into or
  out of a window, so the COUNT features change
- **`months_since_most_recent_<rate>`** — string position = months ago, so
  these shift by the number of dashes between now and the event (we measured
  9–13 months on average where they change, up to 37)
- **`payment_history_length`** — trailing dashes now subtract
- **`percent_of_<rate>_in_last_<m>_months`** — both numerator (window
  membership) and denominator move

Equifax and transunion number features do NOT change — their numerators were
never touched (verified: 0 changed rows).

## Known gaps / follow-ups

- `payment_history_length` REALLY changes for Experian/TU (measured in
  `RealDataExample.ipynb`): the old code subtracted trailing `*` for every
  bureau, and 0.00% of TU/experian patterns end in `*` — so the subtraction
  never fired for them. With the bureau chars it does: 5.45% of TU
  tradelines end in `X` (median 3 months removed, mean 10.4) and 2.28% of
  experian end in `-` (median 11, mean 16). The new values are the correct
  tenure semantics (trailing "no update" runs are unreported months, same as
  equifax's `*`). No effect on our models — `payment_history_length` is not
  aggregated into the processed trade features (0 of 8,848 columns) — but
  any pipeline that consumes it directly will see the new values.
- `missing_data_chars` is a required constructor param — other assets that
  use `PaymentPatternsAggregatorV2` (authorized.json, FE1→FE2 conversion
  variants) must declare it or the param needs a default.
- TU trended (TruVision) patterns would need `Y` (reporting gap) added
  alongside `X`.

## How the research ran, step by step

1. **`save_unmapped_data.ipynb`** — pulls the national training data from
   Snowflake (`POWER_DB.<bureau>.t0_trade`): Equifax `national_1.1`,
   Experian `national_1.3`, TransUnion `national_3_refresh`, at
   train/valid/test archive dates 2019-03-31 / 2019-06-30 / 2019-12-31.
   Saves raw chunks to `payment_processing_research_data/<bureau>/<split>/unmapped/`.

2. **`Notebooks_Where_We_Mapped_Data/`** — the full FE2 runs producing the
   NEW (fixed denominator) and OLD (shipping behavior) data, **without the
   placeholder change** — Experian dashes were still being stripped. Phase 1
   (`normalized_*`) came out correct and keyed, but phase 2 saved its
   processed output with `index=False` after a ZEST_KEY groupby — **we lost
   the ZEST_KEY index**, making the processed files unjoinable.

3. **`fix_processing/`** — redid ONLY the phase-2 processing (to keep the
   ZEST_KEYs this time): read the keyed `normalized_*` as-is, sampled 400k
   non-null-target applicants per bureau for train and another 400k per
   bureau for test (1.2M train + 1.2M test total), and aggregated just those
   into keyed `samples/<bureau>_<role>/processed_{new,old}/`.

4. **`RealDataExample.ipynb`** — built real-data examples of the denominator
   difference and found the placeholder issue: Experian's `-` is a real
   month ("No update received"), not formatting, and the asset's
   `placeholder: "-"` was deleting it — shifting every older month one
   position more recent. ~18% of experian tradelines carry a dash; for the
   affected ones, the corrected pattern's length matches the account's age
   at report (median gap 0.0 months) while the old pattern was short by
   11.6+ months at the 75th percentile. The TU asset was also fixed (its
   `/` placeholder removed), but that changes no data — `/` never occurs in
   TU patterns, so old and new are bit-identical (verified: 0 changed rows;
   this doubles as a control).

5. **`NewProcessing_Experian_{Train,Test}.ipynb`** — because the placeholder
   change was NOT in the original processing, these re-ran phase 1 + 2 for
   the same 400k experian samples with the branch-installed model-engine +
   feature-engine-parts (output:
   `new_normalized_and_processed/experian_<role>/`, keyed).

6. **`PSI_Analysis.ipynb`** — feature-level look at everything that changed,
   for both the **percent** and **number** families (number included because
   the placeholder change moves counts — and only for experian; eq/TU number
   features show 0 changed rows, as designed). Findings: PSI ≈ 0 overall on
   every feature (worst 0.013 vs the 0.1 watch threshold) — but restricted
   to only the applicants whose value actually changed, PSI is HIGH: the
   changed values move to entirely different bins, meaning the corrections
   are systematic shifts, not jitter. (The extreme changed-rows PSIs land on
   niche `min_*` features in tiny populations, where PSI is unbounded — the
   honest read is "big shift where it applies, invisible in the
   population.")

7. **`Predictive_Power_New_vs_Old.ipynb`** — per-feature univariate AUC vs
   the target, NEW vs OLD on the same applicants (one-column models, so no
   masking from redundant features). Percent features on average slightly
   POSITIVE (more improved than degraded, top family +0.003–0.004 univariate
   AUC on 1.18M applicants); number features slightly worse on average but
   basically 0 difference; restricted to the experian applicants whose
   counts actually changed, the AUC deltas are slightly positive but still
   very close.

8. **`Evaluate_Models_With_Change.ipynb`** — trained models on just the
   1,143 percent features (`Build_Model_New_With_Change.ipynb`; same
   applicants, same features, only the aggregator behavior differs).
   Results basically FLAT: AUC 0.7763 (with-change) vs 0.7763 (fix-only) vs
   0.7764 (old), the same on every slice, score Spearman 0.998.

9. **`Evaluate_Models_NumberPercentOpen.ipynb`** — same comparison on a
   288-feature subset of all-open-account **number + percent** features
   (`Build_Model_NumberPercentOpen_{New,Old}.ipynb`), so the model can see
   the count features the placeholder change moves. Again nothing dramatic:
   basically the same performance level for new and old on every slice.

10. **`Difference_Drivers_Train.ipynb`** — multivariate two-sample test: a
    classifier given 100k rows/side can barely tell NEW data from OLD
    (separation AUC 0.53–0.55); among only the ~10% of applicants whose
    features changed it separates at 0.84–0.93 — i.e. the corrections are a
    consistent directional shift where they apply, and invisible everywhere
    else.

## Other analysis

- **`analyze_difference_in_denominator.ipynb`** — pure-data look at the
  denominator change, no models: recomputes `eff_old` vs `eff_new` by hand
  (open pandas string ops, not the aggregator code) for every tradeline and
  every month window, per bureau, and writes the summary tables
  (`% of rows changed`, avg old/new effective months, percentiles for the
  changed rows) to
  `payment_processing_research_data/analysis/denominator_analysis_<split>.xlsx`.
