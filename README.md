# Payment-pattern aggregator: bug walkthrough and proposed fix
## The current code (upstream)

```python
def _get_effective_month_range(self, trimmed, month_range, exclude_trailing=[]):
    z_count = trimmed.str.count("#")
    # exclude trailing symbols, like *, which efx uses for blanks
    if len(exclude_trailing) > 0:
        z_count = z_count + trimmed.apply(lambda x: self._count_trailing_matches(str(x), exclude_trailing))
    effective_month_range = month_range - z_count
    return effective_month_range

def _count_trailing_matches(self, input_str, char_list):
    count = 0
    for char in reversed(input_str.strip()):
        if char in char_list:
            count += 1
        else:
            break
    return count

def _construct_trended_features(self, data, new_ppt):
    for month_range in self.month_ranges:
        trimmed = new_ppt.str[:month_range]
        effective_month_count = self._get_effective_month_range(trimmed, month_range)
        for rate, values in self._rate.items():
            count = self._get_count(trimmed, values)
            data[f"number_{rate}_{month_range}_months{self.name}"] = count.astype(self.PANDAS_DTYPES["numeric"])
            data[f"percent_{rate}_{month_range}_months{self.name}"] = (count / effective_month_count).astype(
                self.PANDAS_DTYPES["numeric"]
            )
    return data
```
## When we create trended features like percent_DQ30+_6_months, the denominator is always the month range minus the number of # characters in the trimmed window, where each # represents one month between the bureau's most recent report date and the data pull date. This ensures that the number in the denominator does not reflect months that occured between the report date and the pull date. 

## However, there are no further adjustments made to the denominator. Consider percent_DQ30+_6_months again.

## Suppose a tradeline had been open for 3 months, missed one payment, and there was one month in between the report date and the pull date. Their trimmed payment history would look like

* #121** equifax
* #121 transunion
* #010 experian

## Suppose another tradeline had been open for 5 months, missed one payment, and there was one month in between the report date and the pull date. Their trimmed payment history would look like

* #12111 equifax
* #12111 transunion
* #10000 experian

## Both of these tradelines would have 1/5 for percent_DQ30+_6_months, even though the first tradeline only had 3 months of history. The first tradeline was in a delinquent state for 33 percent of its existence, whereas the second tradeline was only in a delinquent state for 16%, but the trended DQ30 6 month feature would be the same value

# More specifically, we can see that
- `_construct_trended_features` calls `_get_effective_month_range(trimmed, month_range)` **without** passing `exclude_trailing`, so the denominator only subtracts `#` (pre-report-date filler) and not the bureau "no observation" codes (`*` for Equifax, `-` for Experian, `X` for TransUnion). Every position holding one of those codes is counted as if it were an observed paid-as-agreed month, inflating the `percent_<rate>_<window>_months` denominator and biasing the rates toward 0.
- Even when `exclude_trailing` is passed (as it is for `payment_history_length`), it only strips the trailing run of those codes. Mid-string occurrences (e.g. an Equifax `*` between two observed months, which per spec means "rate not available that month") still inflate the denominator.
- The denominator uses the nominal `month_range` (e.g. 24). Experian (which strips `-` via `placeholder`) and TransUnion (NaN-fill is `""`) produce payment-pattern strings shorter than `month_range` for sparse histories, so `m - count('#')` counts string positions that don't exist.

## Proposed change

1. Rename `exclude_trailing` to `missing_data_chars` (the same codes can be dropped mid-string now, so the old name is misleading).
2. Add an `all_month_range` flag to `_get_effective_month_range`.
   - `True`  -> subtract only trailing runs of `missing_data_chars` (legacy behavior, used by `payment_history_length` so it still reads as "months the account has existed").
   - `False` -> subtract every occurrence of `missing_data_chars` anywhere in the window (spec-correct for trended rate denominators).
3. In `_construct_trended_features`, pass `month_range=trimmed.str.len()` so the denominator is anchored on the observed string length, not the nominal window.

```python
def _get_effective_month_range(self, trimmed, month_range, missing_data_chars=[], all_month_range=True):
    z_count = trimmed.str.count("#")
    if all_month_range:
        # legacy trailing-only path (payment_history_length)
        if len(missing_data_chars) > 0:
            z_count = z_count + trimmed.apply(
                lambda x: self._count_trailing_matches(str(x), missing_data_chars)
            )
    else:
        # spec-correct: every missing-data position in the window is unobserved
        z_count = z_count + self._get_count(trimmed, missing_data_chars)
    return month_range - z_count


def _construct_trended_features(self, data, new_ppt, missing_data_chars):
    for month_range in self.month_ranges:
        trimmed = new_ppt.str[:month_range]
        effective_month_count = self._get_effective_month_range(
            trimmed,
            month_range=trimmed.str.len(),
            missing_data_chars=missing_data_chars,
            all_month_range=False,
        )
        for rate, values in self._rate.items():
            count = self._get_count(trimmed, values)
            data[f"number_{rate}_{month_range}_months{self.name}"] = count.astype(self.PANDAS_DTYPES["numeric"])
            data[f"percent_{rate}_{month_range}_months{self.name}"] = (count / effective_month_count).astype(
                self.PANDAS_DTYPES["numeric"]
            )
    return data
```

## Asset wiring

The code change above only takes effect when each bureau's asset JSON declares its `missing_data_chars` value. `missing_data_chars` becomes the associated parameter on `PaymentPatternsAggregatorV2` in the asset, replacing the old `exclude_trailing` field. The assets that need updating live under `model-engine/model_engine/assets/<bureau>/.../trade.json` (and the corresponding `authorized.json` and FE1->FE2 conversion variants).

Today's asset snippet (Equifax shown for example):
```json
{
  "type": "PaymentPatternsAggregatorV2",
  "params": {
    "report_date": "rptDate",
    "payment_patterns": { ... }
  }
}
```
Note that `exclude_trailing` was hardcoded inside the aggregator (only for the `payment_history_length` call), not declared in the asset.

After the change, every bureau asset declares its own `missing_data_chars`:
```json
{
  "type": "PaymentPatternsAggregatorV2",
  "params": {
    "report_date": "rptDate",
    "payment_patterns": { ... },
    "missing_data_chars": ["*"]
  }
}
```

The value is bureau-specific (`["*"]` for Equifax, `["-"]` for Experian, `["X"]` for TransUnion); see the table below.

## Why a different `missing_data_chars` per bureau

Each bureau defines its own single-character code for "this month, no rating was observed". The aggregator needs to subtract those positions from the denominator so the trended-rate features measure "DQ rate over observed months" rather than "DQ rate over advertised lookback."

| Bureau | `missing_data_chars` | Spec meaning | Source |
|---|---|---|---|
| Equifax | `["*"]` | "Rate/Status was not available for that month" | System-to-System TotalView Programming Guide, p. 3-30 |
| Experian | `["-"]` | "No history reported" | CIS Credit Report Cross Reference Guide (ARF/XML/JSON), p. 49 |
| TransUnion | `["X"]` | "no data received from a subscriber for the month or when a trade account is in dispute" (also hold / unrated) | TU4.0 User Guide, p. 840 |

The codes are different because each bureau's payment-pattern format is different (Equifax retains `*` in the string via NaN-fill; Experian strips `-` via `placeholder`; TransUnion NaN-fills with `""` and keeps `X` as a literal character). The unifying rule across all three is the same: any single-character code that appears in the payment pattern but is not declared in any rate-value bucket of the asset's `rate` config is a missing-data code and should be excluded from the denominator.

## Additional change: `_get_count` now escapes its values before passing to `pandas.Series.str.count`

The new `_construct_trended_features` path calls `_get_effective_month_range(..., all_month_range=False)`, which in turn calls `_get_count(trimmed, missing_data_chars)`. The existing implementation passed `values[0]` (or `'|'.join(values)`) straight into `trimmed.str.count(...)`, and `pandas.Series.str.count` always interprets `pat` as a regular expression (the underlying call is `re.compile(pat)`).

That was harmless when `_get_count` was only ever called with rate codes like `'0'`, `'1'`, `'2'`, ... (none of which are regex metacharacters), but the moment Equifax's `missing_data_chars=['*']` flows through, `re.compile('*')` raises `re.error: nothing to repeat at position 0` because `*` is a quantifier with no preceding atom. Experian (`'-'`) and TransUnion (`'X'`) happened to be literal-safe; only Equifax tripped the bug.

The fix runs every value through `re.escape` so it's matched literally, not interpreted:

```python
import re

def _get_count(self, trimmed, values):
    if len(values) == 1:
        return trimmed.str.count(re.escape(values[0]))
    else:
        pattern = "|".join(re.escape(v) for v in values)
        return trimmed.str.count(pattern)
```

`re.escape('0')` returns `'0'`, so the alphanumeric rate codes that already worked still work, and any punctuation that future `missing_data_chars` configs might use (`*`, `+`, `?`, `.`, `(`, etc.) is automatically literal-safe too.
