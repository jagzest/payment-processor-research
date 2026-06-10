# payment-processor-research

The changes are committed on the **`payment_processor_change`** branch of both
repos (model-engine `d747ffb8`, feature-engine-parts `32ca0541`):

- [equifax change](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/equifax/cms_6/fe2/trade.json#L285-L315)
- [experian change](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/experian/arf7/fe2/trade.json#L374-L398)
- [transunion/TU4R change](https://github.com/Katlean/model-engine/blob/d747ffb81cb16ea7e80989c091b5d75d013eada9/model_engine/assets/transunion/TU4R/fe2/trade.json#L435-L459)
- [feature-engine-parts change](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py)
  (`payment_pattern_aggregator.py` — see the linked walkthrough below)

Research project on the `PaymentPatternsAggregatorV2` trended-feature
denominator: the `percent_<rate>_<window>_months` features divide by months
that were never observed, biasing the rates toward 0. We built the fix, ran a
NEW-vs-OLD modeling experiment, and along the way found a second (untested)
issue with the Experian placeholder.

## Where to look

- `CHANGES.md` — full walkthrough of every code + asset change and why
- `RESULTS_SUMMARY.md` — experiment verdict and where the data came from
- `RealDataExample.ipynb` — real-data examples, and the **ONLY place the
  placeholder change is shown**
- `~/feature-engine-parts-master/` and `~/model-engine-master/` — the clean
  master clones carrying the changes

## The changes, in brief

- **`missing_data_chars` added per bureau asset** — the bureau's "no rating
  observed this month" code, excluded from the percent denominators:
  Equifax `*` (TotalView Guide p. 3-30), Experian `-` (CIS Guide Appendix T,
  Segment 357.B4.5, p. 139), TransUnion `X` (TU4.1 Guide Appendix C,
  pp. 839-840).
- **feature-engine-parts** (`payment_pattern_aggregator.py`, commit `32ca0541`):
  - We added `missing_data_chars` — see the
    [param docstring](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L39-L43):
    bureau-specific codes for months with no observed rating (`*` EFX, `-`
    EXP, `X` TU), excluded from the effective month range. It replaces the
    old `exclude_trailing`, which was hardcoded to `["*"]`; now the asset
    declares it per bureau.
  - [`_get_effective_month_range` gained an `all_month_range` flag](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L175),
    because the two callers need different semantics for "missing":
    - `True` subtracts only the **trailing run** of missing-data chars (via
      `_count_trailing_matches`). Trailing codes are the bureau padding the
      fixed-width field for months **before the account opened**, so they're
      not part of the account's life — but a mid-string gap is a month the
      account existed (the bureau just got no update), and a reporting gap
      doesn't make the account younger. This is the duration semantic.
    - `False` subtracts **every occurrence anywhere** in the window (via
      `_get_count`). For an observation count, position is irrelevant — an
      unobserved month is unobserved whether it sits mid-window or at the
      end, and leaving it in the denominator would silently count it as an
      observed paid-as-agreed month.
  - [`payment_history_length` calls it with the default `True`](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L248-L251)
    — it's a duration ("months the account has existed"), so only the
    pre-account-open padding comes off — while the
    [trended-features call passes `all_month_range=False`](https://github.com/Katlean/feature-engine-parts/blob/32ca05418d8f15682c6a80328097436d2a6db01b/feature_engine_parts/fe_parts_V2/preprocessors/payment_pattern_aggregator.py#L211-L216)
    — `percent_<rate>` denominators are observation counts.
  - That same trended call passes `month_range=trimmed.str.len()` instead of
    the nominal window: Experian and TransUnion produce pattern strings
    **shorter** than the window for sparse histories, so the old
    `month_range - count('#')` counted string positions that don't exist.
    Anchoring on the observed string length keeps the denominator honest.
  - `_get_count` wraps values in `re.escape` — `Series.str.count` compiles
    its pattern as a regex, and Equifax's `*` is a bare quantifier
    (`re.error: nothing to repeat`).
- **Placeholder removal (RECENTLY FOUND — see below)** — `placeholder: "-"`
  removed from the Experian asset and the dead `placeholder: "/"` from
  TransUnion. Only demonstrated in `RealDataExample.ipynb`; NOT covered by
  the modeling experiment.

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
