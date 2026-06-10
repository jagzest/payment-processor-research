# payment-processor-research

Research project on the `PaymentPatternsAggregatorV2` trended-feature
denominator: the `percent_<rate>_<window>_months` features divide by months
that were never observed, biasing the rates toward 0. We built the fix, ran a
NEW-vs-OLD modeling experiment, and along the way found a second (untested)
issue with the Experian placeholder.

The changes are committed on the **`payment_processor_change`** branch of both
repos (model-engine `d747ffb8`, feature-engine-parts `32ca0541`).

## Where to look

- `CHANGES.md` — full walkthrough of every code + asset change and why
- `RESULTS_SUMMARY.md` — experiment verdict and where the data came from
- `RealDataExample.ipynb` — real-data examples, and the **ONLY place the
  placeholder change is shown**
- `~/feature-engine-parts-master/` and `~/model-engine-master/` — the clean
  master clones carrying the changes

## The changes, in brief

### model-engine changes

For each equifax/cms_6, experian/arf7 and transunion/TU4R we added
`missing_data_chars` (the bureau's "no rating observed this month" code —
`*`, `-`, `X`) and a `notes` field citing the bureau spec, and for experian
and transunion we removed the `placeholder` so `-` and `/` are no longer
stripped from the payment pattern. See:

- [equifax change](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/equifax/cms_6/fe2/trade.json#L285-L315)
- [experian change](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/experian/arf7/fe2/trade.json#L374-L398)
- [transunion/TU4R change](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/transunion/TU4R/fe2/trade.json#L435-L459)

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

## Known gaps / follow-ups

- `payment_history_length` quietly changes for Experian/TU: trailing `-`/`X`
  now subtract (was hardcoded to `*` only).
- `missing_data_chars` is a required constructor param — other assets that
  use `PaymentPatternsAggregatorV2` (authorized.json, FE1→FE2 conversion
  variants) must declare it or the param needs a default.
- TU trended (TruVision) patterns would need `Y` (reporting gap) added
  alongside `X`.

## How the experiment ran, step by step

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
3. **`fix_processing/`** — redid phase 2 only: read the keyed `normalized_*`
   as-is, sampled 400k non-null-target applicants per (bureau, role), and
   aggregated just those into keyed `samples/<bureau>_<role>/processed_{new,old}/`.
4. **`Build_Model_*` / `Evaluate_Models*`** — trained NEW vs OLD on identical
   applicants and features. **Pretty much identical**: AUC 0.7763 vs 0.7764,
   flat in every cluster, holds for a deep tree too. The denominator fix is
   correctness-only on this book — see `RESULTS_SUMMARY.md`.
5. **`RealDataExample.ipynb`** — while building real-data examples of the
   denominator difference, we found the placeholder issue: Experian's `-` is
   a real month ("No update received"), but the asset's `placeholder: "-"`
   was deleting it — shifting every older month one position more recent,
   corrupting window membership and `months_since_*` features for the ~18%
   of Experian tradelines that carry a dash. (TU's `/` placeholder was just
   dead config — the character never occurs.) **This has NOT been fully
   tested**: all the `normalized_*` data and the trained models were built
   with the dash still stripped, so evaluating it requires re-processing the
   bureau data and re-training. The notebook builds its Experian/TU patterns
   by hand (skipping the placeholder removal) to show the corrected behavior.
