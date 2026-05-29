"""
Shared helpers for payment-processor-research notebooks.

Everything reusable across `save_unmapped_data.ipynb`,
`map_and_save_mapped_data.ipynb`, and `analyze_difference_in_denominator.ipynb`
lives here so the notebooks stay short and the implementation has one home.

Module layout:

  Constants
    ASSET_PATHS, MODEL_ENGINE_ASSETS_ROOT, CHUNK_SIZE, MONTH_RANGES,
    MISSING_DATA_CHARS, PROJECT_COLS, TU_PPT_STATUS_MAP

  Asset utilities
    load_asset_json(bureau)          -> dict
    get_raw_features(bureau)         -> sorted list of raw_feature strings
    get_aggregator_params(bureau)    -> dict ready to splat into PaymentPatternsAggregatorV2(...)

  Snowflake
    get_conn(schema, ...)            -> snowflake.connector.connection
    build_t0_trade_query(cfg, ...)   -> SQL string
    load_t0_trade(cfg, split, ...)   -> pd.DataFrame

  Mapper
    build_mappers()                  -> dict[bureau, MapperV2]

  File IO
    save_in_chunks(df, out_dir, ...) -> n_chunks
"""
import os
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

import pandas as pd
import snowflake.connector


# =============================================================================
# Constants
# =============================================================================

# Paths are RELATIVE to model_engine/assets/ -- `load_asset()` resolves them.
ASSET_PATHS = {
    'equifax':    'equifax/cms_6/fe2/trade.json',
    'experian':   'experian/arf7/fe2/trade.json',
    'transunion': 'transunion/TU4R/fe2/trade.json',
}

CHUNK_SIZE   = 100_000
MONTH_RANGES = [3, 6, 12, 24, 48]

# Bureau-specific missing-data char (spec citations in old_context/).
MISSING_DATA_CHARS = {
    'equifax':    '*',
    'experian':   '-',
    'transunion': 'X',
}

# Columns to keep after MapperV2 runs (mirrors GoThroughTest.ipynb).
PROJECT_COLS = {
    'equifax': [
        'ZEST_KEY', 'date_of_request', 'rptDate',
        'RATE_STATUS_CODE', 'PAYMENT_HISTORY_1_24',
        'PAYMENT_HISTORY_25_36', 'PAYMENT_HISTORY_37_48',
    ],
    'experian': [
        'ZEST_KEY', 'date_of_request', 'rptDate', 'PAYMENT_PROFILE',
    ],
    'transunion': [
        'ZEST_KEY', 'date_of_request', 'rptDate', 'ppt_status', 'PAYMENT_PATTERN',
    ],
}

# TransUnion MANNER_OF_PAYMENT -> ppt_status mapping, lifted from the asset.
TU_PPT_STATUS_MAP = {
    '01': '1', '02': '2', '03': '3', '04': '4', '05': '5',
    '07': '7', '08': 'K', '8A': 'J', '8P': 'K',
    '09': 'L', '9B': 'G', '9P': 'L', 'UR': 'X',
}


# =============================================================================
# Asset utilities
# =============================================================================

def load_asset_json(bureau):
    """Load a bureau's FE2 trade asset via model_engine's load_asset.

    Uses the same call the production pipeline uses, so we get the asset
    exactly as model-engine sees it. If the import chain fails (e.g. the
    env is missing `aiohttp` -- a transitive dep of s3fs), install it with
    `pip install aiohttp` and re-import.
    """
    from model_engine.assets.utils import load_asset
    return load_asset(ASSET_PATHS[bureau])


def get_raw_features(bureau):
    """Sorted list of every distinct `raw_feature` string in the asset's mapping.

    These are the columns the MapperV2 step (StringConverterV2 / DateConverterV2 /
    NumericConverterV2 / etc.) expects on the input DataFrame. Used by
    `build_t0_trade_query` to project only what's needed out of Snowflake.
    """
    asset = load_asset_json(bureau)
    raws = set()

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get('raw_feature'), str):
                raws.add(o['raw_feature'])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(asset.get('mapping', asset))
    return sorted(raws)


def get_aggregator_params(asset_or_bureau):
    """Pull the PaymentPatternsAggregatorV2 params block out of an asset.

    Accepts either a bureau name (str) or an already-loaded asset dict.
    Returns a shallow copy of the asset's `params` dict for the
    PaymentPatternsAggregatorV2 step, ready to splat into the modified
    constructor:

        PaymentPatternsAggregatorV2(**get_aggregator_params(asset))

    The asset's `params` carries `missing_data_chars` at the top level
    (alongside `report_date` and `payment_patterns`) -- the same shape the
    modified `__init__(self, payment_patterns, missing_data_chars,
    report_date=None, ...)` expects. The shallow copy guards against the
    caller mutating the cached asset.
    """
    if isinstance(asset_or_bureau, str):
        asset = load_asset_json(asset_or_bureau)
    else:
        asset = asset_or_bureau

    params = next(
        s['params'] for s in asset.get('preprocess', [])
        if s.get('type') == 'PaymentPatternsAggregatorV2'
    )
    return dict(params)


def get_relevant_mapping_steps(asset_or_bureau, needed_cols):
    """Filter the asset's `mapping` list to converters that produce one of
    `needed_cols` as their output column.

    Drives a slimmed `MapperV2(api=filtered)` in analysis notebooks: every
    Date/String/NumericConverterV2 whose `new_feature` (or `raw_feature` if
    `new_feature` is None) is in `needed_cols` is kept; everything else
    (numerics over balances/limits, codes we don't read, etc.) is dropped.

    Order is preserved -- the filter iterates `asset['mapping']` in declared
    order so dependent converters still run in the correct sequence.
    """
    if isinstance(asset_or_bureau, str):
        asset = load_asset_json(asset_or_bureau)
    else:
        asset = asset_or_bureau

    needed = set(needed_cols)
    out = []
    for step in asset.get('mapping', []):
        params = step.get('params', {})
        produces = params.get('new_feature') or params.get('raw_feature')
        if produces in needed:
            out.append(step)
    return out


# =============================================================================
# Snowflake
# =============================================================================

def get_conn(schema,
             key_path='~/.snowflake/rsa_key.p8',
             account='zest.us-east-1',
             warehouse='POWER_WH',
             database='POWER_DB',
             role='POWER_ROLE'):
    """Open a Snowflake connection rooted in POWER_DB.<schema>.

    Uses RSA private-key auth from ~/.snowflake/rsa_key.p8 -- same pattern as
    sample_truist_preprocessed.ipynb and feature-engine-worker's ServiceConfig.
    Derives the username from $USER@zest.ai.
    """
    user = f"{os.environ['USER']}@zest.ai"
    with open(os.path.expanduser(key_path), 'rb') as f:
        p_key = serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )
    pkb = p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return snowflake.connector.connect(
        user=user, private_key=pkb,
        account=account, warehouse=warehouse,
        database=database, schema=schema, role=role,
    )


def build_t0_trade_query(cfg, archive_date, limit=None):
    """SQL: project only the asset-declared raw_feature columns out of t0_trade.

    Snowflake-side JSON-path extraction (FEATURES:"col"::TEXT) is much faster
    than fetching the full FEATURES blob and json_normalize'ing client-side.
    """
    raws = get_raw_features(cfg['bureau'])

    select_cols = ['ZEST_KEY', 'ARCHIVE_DATE']
    for r in raws:
        if r in ('ZEST_KEY', 'DATE_OF_REQUEST'):
            continue   # top-level column / aliased below
        select_cols.append(f'FEATURES:"{r}"::TEXT AS "{r}"')

    q = f"""
        SELECT
            {', '.join(select_cols)}
        FROM POWER_DB.{cfg['schema']}.t0_trade
        WHERE pull_name = '{cfg['pull_name']}'
          AND archive_date = '{archive_date}'
          AND ZEST_KEY IS NOT NULL
    """
    if limit is not None:
        q += f"\n        LIMIT {limit}"
    return q


def load_t0_trade(cfg, split, limit=None):
    """Pull one (bureau, split) from Snowflake t0_trade.

    Returns a DataFrame with ZEST_KEY, ARCHIVE_DATE, the asset's raw columns,
    and date_of_request (= ARCHIVE_DATE).
    """
    archive_date = cfg[f'{split}_date']
    query = build_t0_trade_query(cfg, archive_date, limit=limit)

    print(f"[{cfg['bureau']}/{split}] querying POWER_DB.{cfg['schema']}.t0_trade  "
          f"(pull={cfg['pull_name']}, archive_date={archive_date}"
          + (f", LIMIT={limit})" if limit else ")"))

    with get_conn(cfg['schema']) as conn:
        df = conn.cursor().execute(query).fetch_pandas_all()
    df['date_of_request'] = pd.to_datetime(df['ARCHIVE_DATE'])

    print(f"[{cfg['bureau']}/{split}]   {df.shape[0]:,} rows x {df.shape[1]} cols")
    return df


# =============================================================================
# Mapper
# =============================================================================

def build_mappers():
    """Build one MapperV2 per bureau via model_engine.load_asset.

    MapperV2 is imported lazily so notebooks that don't need it (e.g.
    save_unmapped_data.ipynb) aren't forced to import the model-engine chain
    just by importing this module.
    """
    from model_engine.feature_engine_V2.listed_objects_engines import MapperV2

    mappers = {}
    for bureau, rel_path in ASSET_PATHS.items():
        asset = load_asset_json(bureau)
        mappers[bureau] = MapperV2(api=asset['mapping'])
        print(f'{bureau}: mapper built from {rel_path}')
    return mappers


# =============================================================================
# File IO
# =============================================================================

def save_in_chunks(df, out_dir, chunk_size=None):
    """Write `df` to `out_dir/part-NNNNN.parquet` in chunks of `chunk_size`.

    Reading back is a one-liner: pd.read_parquet(out_dir) auto-discovers
    every part-*.parquet under the path. Re-running this function clears
    any previous run's chunks first so reruns are safe.
    """
    if chunk_size is None:
        chunk_size = CHUNK_SIZE

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob('part-*.parquet'):
        old.unlink()

    n_chunks = (len(df) + chunk_size - 1) // chunk_size
    for i in range(n_chunks):
        chunk = df.iloc[i * chunk_size : (i + 1) * chunk_size]
        chunk.to_parquet(out_dir / f'part-{i:05d}.parquet', index=False)
    return n_chunks
