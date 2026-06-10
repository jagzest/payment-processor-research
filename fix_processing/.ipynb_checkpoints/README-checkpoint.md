# fix_processing

These notebooks (`Sample_<Bureau>_<Train|Test>.ipynb`) build the modeling samples:
for one `(bureau, role)` they sample **400,000** non-null-target applicants, save `app.parquet`
+ `target.parquet`, and aggregate **only those ZEST_KEYs** into `processed_new` and `processed_old`
(ZEST_KEY kept as the index). Output goes to `payment_processing_research_data/samples/<bureau>_<role>/`.

## The `normalized_*` data was already built correctly by the other notebooks

The upstream `PrepareData_*` notebooks already produced `normalized_new/` and `normalized_old/` for each
bureau/split, and **they did it correctly** — `ZEST_KEY` is present in `normalized` (it was only ever
dropped later, at the keyless `processed` save). So we don't rebuild `normalized` here; we read it as-is.

## Why the *other* notebooks needed a different model-engine for OLD vs NEW

The whole NEW-vs-OLD experiment is about one change: the **`PaymentPatternsAggregatorV2`** denominator fix
for the trended payment-pattern features (`percent_of_DQ..._in_last_..._months`). That step runs in
**phase 1** (`PreprocessorV2`, mapped → `normalized`), so the NEW/OLD difference is baked into the
`normalized_*` data itself:

- **NEW** — the **fixed** aggregator (from `/home/jag/model-engine`). It takes `missing_data_chars`
  (`*` Equifax, `-` Experian, `X` TransUnion) and **excludes those "no observation" positions from the
  denominator**, so the trended rates measure "DQ rate over *observed* months." The `PrepareData_*_New`
  notebooks assert `missing_data_chars` is present (i.e. that model-engine is loading the fixed code, not
  a stale `~/.local` copy).
- **OLD** — the **original** aggregator behavior. The `PrepareData_*_Old` notebooks **strip
  `missing_data_chars`** from the asset, so the denominator reverts to the old logic (only `#` filler
  subtracted), reproducing the upstream/pre-fix features.

So building `normalized_new` vs `normalized_old` genuinely required two different aggregator behaviors
(the fixed model-engine for NEW, the stripped/original behavior for OLD) — that's where the only real
difference between the two pipelines lives.

## Why *these* notebooks only need ONE model-engine

The notebooks here do **only phase 2** — the across-account `AggregationEngine`
(`aggregation/fe2/trade.json`): group by `ZEST_KEY` and roll the `normalized` columns up into the 8,848
`trade_*` features. **That step is identical for NEW and OLD** — it doesn't know or care about the
denominator fix; it just aggregates whatever columns it's handed.

Because the NEW/OLD difference is already encoded in `normalized_new` vs `normalized_old`, we can use a
**single** `AggregationEngine` (one model-engine, one kernel) and simply point it at the right normalized
folder:

```python
agg_eng = AggregationEngine(asset=load_asset('aggregation/fe2/trade.json'), table_name='trade')
# NEW:  aggregate normalized_new  -> processed_new
# OLD:  aggregate normalized_old  -> processed_old
```

No need to swap aggregators or model-engine versions between the two — the same `agg_eng` produces both,
which is why each notebook loops `for v in ['new', 'old']` over one engine.

## Run notes

- Run in the **model-engine kernel** (these import `model_engine`).
- `Sample_Experian_Test` samples from the **`valid`** split (experian has no `test`).
- Each notebook **wipes its own `samples/<bureau>_<role>/` folder first** so re-runs are clean and
  idempotent (see the "Why we wipe" markdown cell in each).
- Target filter defaults to non-null **`final_DQ60_m24`** (one-line `TARGET_COL` change if you meant
  `final_DQ60_m30`).
