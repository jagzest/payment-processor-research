"""Shared config for the NEW-vs-OLD aggregator model experiment.

Two models built with the SAME feature set, differing only in which aggregator
produced the trade features:
    NEW -> processed_new/   (fixed aggregator)
    OLD -> processed_old/   (original aggregator)

Regular model-builder asset (model_engine.model_builder.build_model), shaped like
the working Truist custom_experiments asset:
    {'data': {app, trade, target}, 'config': {...}}

Data points DIRECTLY at the per-(bureau, role) samples the fix_processing
Sample_* notebooks produce (no '#'/model_inputs json indirection):
    app    -> every samples/<bureau>_<role>/app.parquet
    target -> every samples/<bureau>_<role>/target.parquet
    trade  -> every samples/<bureau>_<role>/processed_<variant>/part-*.parquet
The train/test split is decided downstream by DATA_SPLIT on appDate.
"""
import os
import glob
import json

import configs
from configs import DATA_DIR

SAMPLES_DIR     = os.path.join(DATA_DIR, 'samples')
FEATURES_TO_USE = os.path.join(DATA_DIR, 'Features_To_Use.json')
MODELS_DIR      = os.path.join(DATA_DIR, 'models')

BUREAUS = ['equifax', 'experian', 'transunion']
ROLES   = ['train', 'test']

# Split by appDate (half-open [start, end)), based on the ACTUAL appDates:
#   train (all bureaus, from train):  2019-04-01 .. 2019-06-30  (Q2'19)
#   test: experian (from valid)     : 2019-07-01 .. 2019-09-30  (Q3'19)
#         equifax/transunion (test) : 2020-01-01 .. 2020-03-31  (Q1'20)
DATA_SPLIT = {
    'train': {'start_date': '2019-04-01', 'end_date': '2019-07-01'},
    'test':  {'start_date': '2019-07-01', 'end_date': '2020-04-01'},
}

TARGET_COL  = 'final_DQ60_m24'
TARGET_KEEP = ['appId', 'appDate', 'ZEST_KEY', 'ACCOUNT_TYPE_CODE',
               'GEO_STATE_CODE', TARGET_COL]
APP_KEEP    = None   # None -> keep ALL app columns

# LevelSelection -> FillNA -> XGBoost (lightweight: depth 3, heavy regularization).
PIPELINE_FACTORY = {
    'transformers': [
        {'zaml_class': 'LevelSelection',
         'params': {'thresh': 0.01, 'change_to': 'Other', 'encoding': 'onehot'}},
        {'zaml_class': 'FillNA',
         'params': {'replace_by': -1, 'add_flags': False}},
    ],
    'model': {
        'zaml_class': 'XGBoostModel',
        'params': {
            'learning_rate':      0.05,
            'n_estimators':       500,
            'max_depth':          3,
            'backend_subsample':  0.5,
            'scale_pos_weight':   2.5,
            'colsample_bytree':   0.05,
            'min_child_weight':   250,
        },
    },
}


def _sample_dirs():
    return [os.path.join(SAMPLES_DIR, f'{b}_{r}') for b in BUREAUS for r in ROLES]


def _files(name):
    """app.parquet / target.parquet across all 6 sample folders (existing ones only)."""
    return [p for d in _sample_dirs()
            for p in [os.path.join(d, name)] if os.path.exists(p)]


def _trade_files(variant):
    """processed_<variant>/part-*.parquet across all 6 sample folders."""
    out = []
    for d in _sample_dirs():
        out += sorted(glob.glob(os.path.join(d, f'processed_{variant}', 'part-*.parquet')))
    return out


def load_features():
    with open(FEATURES_TO_USE) as f:
        return json.load(f)


def build_asset(variant):
    """Model-builder asset for variant in {'new', 'old'}. Globs sample files at call time."""
    assert variant in ('new', 'old'), variant
    trade_features = load_features()
    return {
        'data': {
            'app': {
                'asset': {'info': {'key': 'ZEST_KEY', 'app_date': 'appDate'},
                          'table_name': 'app', 'table_type': 'one_to_one',
                          'feature_engineering': 'one_to_one'},
                'data': _files('app.parquet'),
                'io_params': {'drop_duplicates': True, 'keep_features': APP_KEEP,
                              'memory_efficient': True},
            },
            'trade': {
                'asset': {'fe_version': 2,
                          'InputNormalizer':   'transunion/TU4R/fe2/trade.json',
                          'AggregationEngine': 'aggregation/fe2/trade.json'},
                'data': _trade_files(variant),
                'io_params': {'drop_duplicates': True, 'keep_features': trade_features,
                              'memory_efficient': True},
            },
            'target': {
                'asset': {'info': {'col': TARGET_COL, 'key': 'ZEST_KEY'},
                          'table_type': 'target'},
                'data': _files('target.parquet'),
                'io_params': {'drop_duplicates': True, 'keep_features': TARGET_KEEP,
                              'memory_efficient': True},
            },
        },
        'config': {
            'data_split':                  DATA_SPLIT,
            'fold_valid':                  False,
            'target_spec':                 {'target_table': 'target', 'target_col': TARGET_COL},
            'enforce_artifact_validation': True,
            'fe_version':                  2,
            'pipeline_factory':            PIPELINE_FACTORY,
            'base_features':               FEATURES_TO_USE,
        },
    }
