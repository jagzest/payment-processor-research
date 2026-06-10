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
| `assets/equifax/cms_6/fe2/trade.json` | `["*"]` | rate/status not available that month (EFX STS TotalView Programming Guide, p. 3-30) |
| `assets/experian/arf7/fe2/trade.json` | `["-"]` | "No update received" (EXP CIS Cross Reference Guide, Appendix T "Payment Profile Indicators", Segment 357.B4.5, p. 139) |
| `assets/transunion/TU4R/fe2/trade.json` | `["X"]` | no data received from the subscriber / account in dispute (TU4.1 User Guide, Appendix C, pp. 839–840) |

## model-engine: `placeholder` removed from the Experian and TransUnion assets

`placeholder` is an asset-driven input: the aggregator reads it from the asset
(`payment_pattern_aggregator.py:76`) and `_remove_placeholder` (line 142)
deletes every occurrence from the combined pattern string — before trimming
and before the `#` fillers are added. It exists for Equifax, whose patterns
contain a `/` separator after every 12 months of history; that is pure
formatting, not a month, so stripping it is correct and Equifax keeps its
placeholder.

The other two bureaus had placeholders they shouldn't:

- **Experian had `"placeholder": "-"` — removed.** Per Appendix T the dash is
  a real month whose status is "No update received", not formatting. Deleting
  it shifted every older month one position more recent, which (a) put months
  into lookback windows they don't belong in, (b) corrupted the
  `months_since_most_recent_<rate>` features (string position = months ago),
  and (c) shortened the string, masking how much history was actually
  observed. About 18% of Experian test tradelines carry at least one dash.
  With the placeholder gone, the dash stays at its true calendar position and
  the `missing_data_chars: ["-"]` declaration becomes load-bearing: the new
  denominator code subtracts it from the percent features, and the numerators
  ignore it since `-` is in no rate bucket.

- **TransUnion had `"placeholder": "/"` — removed.** The TU4.1 User Guide's
  payment pattern character set (`1`–`5`, `E`, `X`, `J`, `K`, `H`, `G`, `L`,
  `Y`; Appendix C, pp. 838–840) contains no `/`, and a scan of 100k real TU
  tradelines found none either. The entry was copied from the Equifax asset
  and never did anything; removing it is a no-op that stops the asset from
  claiming TU has a separator it doesn't have. The guide also defines `Y` =
  "Represents a gap in monthly payment reporting" (p. 840) for *trended*
  patterns — our pulls use the standard pattern (gaps appear as `X`), but if
  TruVision trended patterns are ever used, `Y` belongs in
  `missing_data_chars` alongside `X`.

Note the Experian removal widens the change beyond the percent denominators:
window membership and `months_since_*` values shift for any tradeline with a
mid-string dash, so the earlier "no model impact" experiment (which ran with
the dash still being stripped) does not cover this part and would need a
re-run to confirm impact on the realigned features.

