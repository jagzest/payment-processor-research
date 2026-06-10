# Payment-pattern denominator fix — what changed and why

Changes applied to the two fresh master clones:

- `~/feature-engine-parts-master/feature-engine-parts` (code, on `1b248fe`)
- `~/model-engine-master/model-engine` (asset config, on `19f1ba0a`)

## The problem

Trended features like `percent_DQ30+_6_months` divide a delinquency count by an
"effective month range" that is supposed to be the number of observed months in
the window. The shipping code only subtracts `#` (the filler we add for the gap
between the bureau report date and the pull date). Two things slip through:

1. **Bureau missing-data codes count as observed months.** Each bureau has a
   single-character code for "no rating observed this month" — `*` (Equifax),
   `-` (Experian), `X` (TransUnion). A position holding one of these still
   counts toward the denominator, so the percent features get biased toward 0.
2. **The denominator uses the nominal window, not the actual string length.**
   Experian strips `-` via `placeholder` and TransUnion NaN-fills with `""`, so
   sparse histories produce strings shorter than the window. Subtracting from
   the nominal `month_range` counts positions that don't exist.

Concrete example (6-month window, one missed payment, one month report gap):
a tradeline open 3 months (`#121**`) and one open 5 months (`#12111`) both get
`1/5` today. After the fix they get `1/3` and `1/5` — the 3-month account was
delinquent for a third of its observed history and now looks like it.

## feature-engine-parts: `fe_parts_V2/preprocessors/payment_pattern_aggregator.py`

- **`exclude_trailing` renamed to `missing_data_chars`**, and it is now a
  constructor parameter declared per bureau in the asset instead of a value
  hardcoded inside `transform`. The old name implied the codes only mattered at
  the tail of the string; the new logic also drops them mid-string.
- **`_get_effective_month_range` gained an `all_month_range` flag.**
  - `True` (default): subtract only trailing runs of missing-data chars. This is
    the old behavior, kept for `payment_history_length` so it still reads as
    account tenure (trailing codes = months before the account opened).
  - `False`: subtract every occurrence anywhere in the window. This is the
    spec-correct denominator for the trended rates — a missing-data code is
    unobserved no matter where it sits.
- **`_construct_trended_features` anchors the denominator on
  `trimmed.str.len()`** instead of the nominal `month_range`, and calls
  `_get_effective_month_range` with `all_month_range=False`. New denominator:
  `len(trimmed) - count('#') - count(missing_data_chars)`.
- **`_get_count` escapes its values with `re.escape`.** `Series.str.count`
  compiles the pattern as a regex, and Equifax's `*` is a bare quantifier —
  `re.compile('*')` raises "nothing to repeat". Escaping keeps the existing
  alphanumeric rate codes working and makes any punctuation safe.
- One side effect worth knowing: `payment_history_length` used to hardcode
  `exclude_trailing=["*"]` for every bureau. It now uses the asset's
  `missing_data_chars`, so trailing `-` / `X` start counting for Experian and
  TransUnion (previously a no-op for them since only `*` was stripped).

## model-engine: bureau FE2 trade assets

Each bureau's `PaymentPatternsAggregatorV2` block now declares its
`missing_data_chars` value, with a note citing the bureau spec:

| File | Value | Spec meaning |
|---|---|---|
| `assets/equifax/cms_6/fe2/trade.json` | `["*"]` | rate/status not available that month (EFX STS TotalView Guide, p. 3-30) |
| `assets/experian/arf7/fe2/trade.json` | `["-"]` | no history reported (EXP CIS Cross Reference Guide, p. 49) |
| `assets/transunion/TU4R/fe2/trade.json` | `["X"]` | no data received / account in dispute (TU4.0 User Guide, p. 840) |

For Experian the `-` chars are already stripped by `placeholder` before the
aggregator runs, so the declared value is effectively documentation — the
denominator correction for Experian comes entirely from the
`trimmed.str.len()` anchoring in feature-engine-parts.

## Verification

- All three asset files parse as valid JSON.
- The edited module compiles, and the README walkthrough example reproduces:
  `#121**` / `#12111` give effective ranges `[3, 5]` and `percent_DQ30+`
  `[0.333, 0.2]`; the legacy trailing-only path and the `re.escape` fix for
  counting `*` both behave as expected (run in the `newest_model_engine` env).
- Impact on modeling was tested separately (see
  `payment-processor-research/README.md` and the denominator analysis
  notebook): only ~6% of tradelines change denominator, and NEW-vs-OLD models
  showed no AUC / econ / calibration difference. This change is about
  correctness and defensibility of the feature definitions, not lift.

## Open items before a PR

- `missing_data_chars` is a required constructor arg with no default, so every
  other asset that instantiates `PaymentPatternsAggregatorV2` (authorized.json
  variants, FE1→FE2 conversion assets, other bureau formats) must either
  declare it or the param needs a default.
- Only the three FE2 `trade.json` assets are updated here; the corresponding
  `authorized.json` and conversion variants still need the same param.
