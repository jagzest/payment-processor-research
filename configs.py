"""
National training data configs for the payment-pattern aggregator research.

One entry per bureau. Each entry carries:
    - train / valid / test date values (used as ARCHIVE_DATE for PARSED trade,
      DATE_OF_REQUEST for PROCESSED trade_fe)
    - base S3 paths for the trade (PARSED), trade_fe (PROCESSED), and target
      tables
    - pull / FEW / ME versions used to assemble the full path

Paths assembled by the helpers below:
    trade_path(cfg, split)    ->  s3://<trade_base>/ARCHIVE_DATE=<date>
    trade_fe_path(cfg, split) ->  s3://<trade_fe_base>/DATE_OF_REQUEST=<date>
    target_path(cfg, split)   ->  s3://<target_base>/ARCHIVE_DATE=<date>
"""

S3_PREFIX = "s3://"


# ---------------------------------------------------------------------------
# Equifax  --  National 1.1
# ---------------------------------------------------------------------------
EQUIFAX = {
    "bureau": "equifax",
    "schema": "EQUIFAX",                    # Snowflake schema (POWER_DB.EQUIFAX)
    "format": "cms_6",
    "pull_name": "national_1.1",
    "few_version": "0.1.0",
    "me_version": "v1.9.0.rc1",
    "train_date": "2019-03-31",
    "valid_date": "2019-06-30",
    "test_date":  "2019-12-31",

    # PARSED trade (raw bureau columns, no ZEST_KEY) -- partitioned by ARCHIVE_DATE
    "trade_base":
        "power-equifax-prod/NATIONAL/PARSED/DATA/"
        "BUREAU=equifax/FORMAT=cms_6/TABLE=trade/PULL_NAME=national_1",

    # PROCESSED trade_fe (post-FE; has ZEST_KEY) -- partitioned by DATE_OF_REQUEST
    "trade_fe_base":
        "power-equifax-prod/NATIONAL/PROCESSED/DATA/"
        "BUREAU=equifax/FORMAT=cms_6/TABLE=trade_fe/PULL_NAME=national_1.1/"
        "FEW_VERSION=0.1.0/ME_VERSION=v1.9.9rc1",

    # PROCESSED target -- partitioned by ARCHIVE_DATE
    "target_base":
        "power-equifax-prod/NATIONAL/PROCESSED/DATA/"
        "BUREAU=equifax/FORMAT=cms_6/TABLE=target/PULL_NAME=national_1.1/"
        "FEW_VERSION=0.1.0/ME_VERSION=v1.9.9rc1",
}


# ---------------------------------------------------------------------------
# Experian  --  National 1.2
# ---------------------------------------------------------------------------
EXPERIAN = {
    "bureau": "experian",
    "schema": "EXPERIAN",
    "format": "arf7",
    "pull_name": "national_1.3",
    "few_version": "0.0.9",
    "me_version": "v1.8.0rc0",
    "train_date": "2019-03-31",
    "valid_date": "2019-06-30",
    "test_date":  "2019-12-31",

    "trade_base":
        "power-experian-prod/NATIONAL/PARSED/DATA/"
        "BUREAU=experian/FORMAT=arf7/TABLE=trade/PULL_NAME=national_1.3",

    "trade_fe_base":
        "power-experian-prod/NATIONAL/PROCESSED/DATA/"
        "BUREAU=experian/FORMAT=arf7/TABLE=trade_fe/PULL_NAME=national_1.3/"
        "FEW_VERSION=0.0.9/ME_VERSION=v1.8.0rc0",

    "target_base":
        "power-experian-prod/NATIONAL/PROCESSED/DATA/"
        "BUREAU=experian/FORMAT=arf7/TABLE=target/PULL_NAME=national_1.3/"
        "FEW_VERSION=0.0.9/ME_VERSION=v1.8.0rc0",
}


# ---------------------------------------------------------------------------
# TransUnion  --  National_3
# ---------------------------------------------------------------------------
TRANSUNION = {
    "bureau": "transunion",
    "schema": "TRANSUNION",
    "format": "TU4R",
    "pull_name": "national_3_refresh",
    "few_version": "0.1.0",
    "me_version": "v1.9.0.rc1",
    "train_date": "2019-03-31",
    "valid_date": "2019-06-30",
    "test_date":  "2019-12-31",

    "trade_base":
        "power-transunion-prod/NATIONAL/PARSED/DATA/"
        "BUREAU=transunion/FORMAT=TU4R/TABLE=trade/PULL_NAME=national_3_refresh",

    "trade_fe_base":
        "power-transunion-prod/NATIONAL/PROCESSED/DATA/"
        "BUREAU=transunion/FORMAT=TU4R/TABLE=trade_fe/PULL_NAME=national_3_refresh/"
        "FEW_VERSION=0.1.0/ME_VERSION=v1.9.0rc",

    "target_base":
        "power-transunion-prod/NATIONAL/PROCESSED/DATA/"
        "BUREAU=transunion/FORMAT=TU4R/TABLE=target/PULL_NAME=national_3_refresh/"
        "FEW_VERSION=0.1.0/ME_VERSION=v1.9.0rc",
}


BUREAUS = {"equifax": EQUIFAX, "experian": EXPERIAN, "transunion": TRANSUNION}


# Local file-system root for research data we've pulled from Snowflake and
# transformed locally. Notebooks save here so they can be re-read across
# sessions without re-hitting Snowflake. Structure:
#
#   payment_processing_research_data/<bureau>/<split>/<stage>/part-NNNNN.parquet
#
# <stage> is one of:
#   unmapped    -- raw bureau columns straight from Snowflake t0_trade (the
#                  per-tradeline FEATURES JSON projected back into columns).
#                  No DateConverter, no NumericConverter, no Aggregator.
#                  ZEST_KEY + DATE_OF_REQUEST present.
#   mapped      -- after MapperV2 (DateConverter / NumericConverter / etc.) plus
#                  the PaymentPatternsAggregatorV2 preprocess step. Has the
#                  full set of trended/months_since/percent features ready.
#   normalized  -- (future) pre-aggregator intermediate; would mirror the
#                  s3://power-<b>-prod/NATIONAL/NORMALIZED FE2 intermediate
#   processed   -- (future) fully feature-engineered, mirrors S3 PROCESSED
#
# To read all chunks for one slice, point pandas at the directory:
#
#   pd.read_parquet('payment_processing_research_data/equifax/train/unmapped')
#
import os as _os
DATA_DIR = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)),
    'payment_processing_research_data',
)

VALID_STAGES = (
    'unmapped', 'mapped',
    'normalized', 'processed',           # legacy / shared
    'normalized_new', 'normalized_old',  # PrepareDataNewMethod / OldMethod
    'processed_new',  'processed_old',
    'prepped',
)


def stage_dir(bureau_cfg, split, stage):
    """Return the directory for one (bureau, split, stage)."""
    assert stage in VALID_STAGES, f"stage must be one of {VALID_STAGES}, got {stage!r}"
    return _os.path.join(DATA_DIR, bureau_cfg['bureau'], split, stage)


def unmapped_dir(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'unmapped')


def mapped_dir(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'mapped')


def normalized_dir(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'normalized')


def processed_dir(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'processed')


# --- per-method new/old splits used by PrepareDataNewMethod / OldMethod ---
# The two notebooks run the same pipeline against different aggregator
# implementations (new vs upstream PaymentPatternsAggregatorV2). They write
# to SEPARATE directories so they can coexist on disk and so neither run
# overwrites the other -- you can diff their processed outputs directly.

def normalized_dir_new(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'normalized_new')


def normalized_dir_old(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'normalized_old')


def processed_dir_new(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'processed_new')


def processed_dir_old(bureau_cfg, split):
    return stage_dir(bureau_cfg, split, 'processed_old')


def prepped_dir(bureau_cfg, split):
    """Per-(bureau, split) dir for `analyze_difference_in_denominator.ipynb`'s
    intermediate prepped output -- the slim-mapped + DateDiff'd + aggregator-
    pattern-constructed chunks (PROJECT_COLS + zest_payment_pattern)."""
    return stage_dir(bureau_cfg, split, 'prepped')


# Backward-compat alias for any older code that still calls mapped_data_dir.
mapped_data_dir = mapped_dir


def trade_path(bureau_cfg, split):
    """Return the full s3:// path for the PARSED trade table at the given split.

    PARSED trade is partitioned by ARCHIVE_DATE.
    split: one of {"train", "valid", "test"}.
    """
    date = bureau_cfg[f"{split}_date"]
    return f"{S3_PREFIX}{bureau_cfg['trade_base']}/ARCHIVE_DATE={date}"


def trade_fe_path(bureau_cfg, split):
    """Return the full s3:// path for the PROCESSED trade_fe table at the given split.

    PROCESSED trade_fe is partitioned by DATE_OF_REQUEST (one extra level deep
    than PARSED, since trade_fe also has ACCOUNT_TYPE_CODE and GEO_STATE_CODE
    sub-partitions below DATE_OF_REQUEST; reading at the DATE_OF_REQUEST level
    pulls all of them).
    split: one of {"train", "valid", "test"}.
    """
    date = bureau_cfg[f"{split}_date"]
    return f"{S3_PREFIX}{bureau_cfg['trade_fe_base']}/DATE_OF_REQUEST={date}"


def target_path(bureau_cfg, split):
    """Return the full s3:// path for the target table at the given split.

    Target is partitioned by ARCHIVE_DATE.
    """
    date = bureau_cfg[f"{split}_date"]
    return f"{S3_PREFIX}{bureau_cfg['target_base']}/ARCHIVE_DATE={date}"
