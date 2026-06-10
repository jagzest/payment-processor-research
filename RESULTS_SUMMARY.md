# Results summary — denominator-fix experiment

Question: does the payment-pattern denominator fix (`missing_data_chars`)
improve the model, or is it correctness-only? Concluded 2026-06-04.

## Where the data came from

Modeling data was loaded from `payment_processing_research_data/samples/<bureau>_<role>/`
(app + target + keyed `processed_{new,old}`). Those samples were built by the
`fix_processing/` notebooks, which redo only the phase-2 aggregation over the
`normalized_*` data because the original full runs in
`Notebooks_Where_We_Mapped_Data/` saved their processed output without
ZEST_KEY (see each folder's README for the details).

## How the comparison was set up

- **NEW vs OLD on identical applicants and identical feature lists.** Phase 1
  was run twice per bureau/split (`normalized_new` with the fixed aggregator,
  `normalized_old` with `missing_data_chars` stripped to reproduce shipping
  behavior); phase 2 and everything downstream were identical.
- **Sample choice:** 400k applicants per (bureau, role), chosen as the
  non-null-target population on `final_DQ60_m24` (see
  `fix_processing/README.md`). Same ZEST_KEYs in both arms, so every score
  difference is attributable to the denominator change alone.
- **Models:** same XGBoost config for both arms, plus a deliberately deep
  variant (depth 8, colsample 0.30, min_child 50) to rule out "the shallow
  tree just can't exploit the fixed features" as an explanation.

## Verdict: correctness-only — no measurable model, econ, or calibration value on this book

Checked four ways (details in `Evaluate_Models.ipynb` / `Evaluate_Models_Deep.ipynb`,
scores and profiles in `payment_processing_research_data/models/`):

- **Rank ordering:** AUC 0.7763 (NEW) vs 0.7764 (OLD) overall, flat in every
  PCA/KMeans cluster including the highest-drift cluster
  (`cluster_drift_analysis.py`). Holds for the deep tree too, so it's
  structural, not a regularization artifact.
- **Why it's flat:** the fix only moves `percent_of_DQ*` features, which are
  0 for accounts with no delinquency, monotonically rescaled where non-zero,
  and redundant with the unchanged `number_*` count features the model
  actually reads risk from. Only ~6% of applicants change at all, by ~0.001
  after aggregation.

The fix's value is correctness and defensibility of the feature definitions
(denominators that match the bureau specs), not lift.

## IMPORTANT caveat: the placeholder change is NOT covered by this experiment

After the experiment concluded, we made one more change (see `CHANGES.md`):
removed `placeholder: "-"` from the Experian asset (and the dead
`placeholder: "/"` from TransUnion). The trained NEW model was built with the
Experian dash still being **stripped** — i.e. with histories shifted and
shortened for the ~18% of Experian tradelines that carry a dash.

The placeholder removal changes more than the percent denominators: window
membership and every `months_since_*` feature shift for dash-carrying
tradelines, so `number_*` counts can move too. None of the conclusions above
apply to it. To evaluate it we would need to **re-run phase 1 (re-process all
bureau data) and re-train the models** — the `normalized_*` data on disk was
all built with the dash stripped. The TransUnion placeholder removal needs no
re-run (verified `/` never occurs in TU data, so output is bit-identical).
