# Notebooks_Where_We_Mapped_Data

These are the `PrepareData_<Bureau>_<Split>_<New|Old>.ipynb` notebooks — the
full FE2 runs that turned the raw `unmapped/` Snowflake pulls into
`normalized_*` and `processed_*` data for the NEW-vs-OLD denominator
experiment. One notebook per (bureau, split, method) so each combo can be
restarted on its own with its own log (the original combined notebook stalled
mid-TransUnion and had to be split up).

Each notebook runs two phases per chunk:

- **Phase 1** — `MapperV2` + `PreprocessorV2` (mapped → normalized, one row
  per tradeline). This is where the NEW/OLD difference lives: the
  `PaymentPatternsAggregatorV2` denominator fix runs as a preprocess step, so
  NEW notebooks use the fixed aggregator (asserting `missing_data_chars` is
  present) and OLD notebooks strip `missing_data_chars` from the asset to
  reproduce the shipping behavior.
- **Phase 2** — `AggregationEngine` (normalized → processed, one row per
  applicant): group every tradeline by `ZEST_KEY` and roll up into the 8,848
  `trade_*` features.

## How we lost ZEST_KEY at the aggregation step

Phase 2's `AggregationEngine.transform` groups by `ZEST_KEY`, so the result
comes back with the key **in the index**, not as a column. The save line in
these notebooks was:

```python
processed = agg_eng.transform(sub)
processed.to_parquet(out_path, index=False)   # <- index=False drops ZEST_KEY
```

`index=False` threw the index away, so every `processed_*` part file came out
with 8,848 feature columns and **no applicant key** — unjoinable to
`app.parquet` / `target.parquet`. Verified on disk: the original keyless saves
(since renamed `processed_new_without_zest_key/` and
`processed_old_without_zest_key/` under `transunion/test/`) have 8,848 columns
and no `ZEST_KEY`; the keyed rebuilds have 8,849 with `ZEST_KEY` present.

Phase 1 did NOT have this problem — `normalized_*` keeps `ZEST_KEY` as an
ordinary column (also verified). Only the phase-2 output was keyless.

## Why the redo lives in `fix_processing/`

Because the expensive, behavior-dependent work (phase 1) was already correct,
nothing needed to be re-mapped or re-preprocessed. The `fix_processing/`
notebooks redo **only phase 2**: read `normalized_new` / `normalized_old`
as-is, sample 400k non-null-target applicants per (bureau, role), aggregate
only those ZEST_KEYs, and save with the key kept as the index
(`samples/<bureau>_<role>/processed_{new,old}/`).

That redo also only needs **one** model-engine/kernel: the NEW/OLD difference
is already baked into the `normalized_*` data by phase 1, and the
`AggregationEngine` is identical for both — it just rolls up whatever columns
it's handed. See `../fix_processing/README.md` for the full details and run
notes.

## TL;DR

- These notebooks built `normalized_*` correctly (keyed) — still the source
  of truth for per-tradeline NEW/OLD features.
- Their `processed_*` output was saved keyless (`index=False` after a
  ZEST_KEY groupby) and is superseded by `fix_processing/` /
  `samples/` — the keyless originals are kept as
  `processed_*_without_zest_key/`.
