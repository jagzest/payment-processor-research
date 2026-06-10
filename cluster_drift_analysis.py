"""PCA -> 5 clusters on the NEW FE matrix, profiled by NEW-vs-OLD drift, target, and AUC.

Run in the known-good env (avoids the notebook kernel's BLAS segfault):
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
        ~/.conda/envs/newest_model_engine/bin/python cluster_drift_analysis.py
"""
import os, gc, numpy as np, pandas as pd
from sklearn.decomposition import PCA
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics import roc_auc_score

MODELS = os.path.expanduser('~/payment-processor-research/payment_processing_research_data/models')
SAMPLES = os.path.join(os.path.dirname(MODELS), 'samples')
TARGET = 'final_DQ60_m24'
FIT_N  = 200_000

new = pd.read_parquet(os.path.join(MODELS, 'model_new', 'test_fe_data.parquet'))
old = pd.read_parquet(os.path.join(MODELS, 'model_old', 'test_fe_data.parquet'))
cols = [c for c in new.columns if c in old.columns]
keys = new['ZEST_KEY'].values

Xnew = new[cols].select_dtypes('number').fillna(-1.0).astype('float32')
num_cols = Xnew.columns
Xold = old[cols][num_cols].fillna(-1.0).astype('float32')
del new, old; gc.collect()

row_drift = np.abs(Xnew.values - Xold.values).sum(axis=1)
del Xold; gc.collect()

mu = Xnew.values.mean(axis=0)
sd = Xnew.values.std(axis=0); sd[sd == 0] = 1.0
def standardize(a): return ((a - mu) / sd).astype('float32')

rng = np.random.RandomState(0)
fit_idx = rng.choice(len(Xnew), size=min(FIT_N, len(Xnew)), replace=False)

pca = PCA(n_components=5, svd_solver='randomized', random_state=0)
pcs_fit = pca.fit_transform(standardize(Xnew.values[fit_idx]))
km = MiniBatchKMeans(n_clusters=5, random_state=0, n_init=10, batch_size=10000)
km.fit(pcs_fit)

cluster = np.empty(len(Xnew), dtype='int32')
for s in range(0, len(Xnew), 100_000):
    e = min(s + 100_000, len(Xnew))
    cluster[s:e] = km.predict(pca.transform(standardize(Xnew.values[s:e])))
del Xnew; gc.collect()

prof = pd.DataFrame({'cluster': cluster, 'row_drift': row_drift, 'ZEST_KEY': keys})

tgt = pd.concat([pd.read_parquet(os.path.join(SAMPLES, f'{b}_test', 'target.parquet'),
                                 columns=['ZEST_KEY', TARGET])
                 for b in ['equifax', 'experian', 'transunion']], ignore_index=True)

def load_pred(variant):
    s = pd.read_parquet(os.path.join(MODELS, f'model_{variant}', 'test_scores.parquet'))
    if 'ZEST_KEY' not in s.columns: s = s.reset_index()
    pcol = [c for c in s.columns if c != 'ZEST_KEY'][0]
    return s[['ZEST_KEY', pcol]].rename(columns={pcol: f'pred_{variant}'})

prof = (prof.merge(tgt, on='ZEST_KEY', how='left')
            .merge(load_pred('new'), on='ZEST_KEY', how='left')
            .merge(load_pred('old'), on='ZEST_KEY', how='left'))

print('cluster sizes:'); print(prof['cluster'].value_counts().sort_index())
print('\nNEW-vs-OLD drift per cluster:')
print(prof.groupby('cluster')['row_drift'].agg(['mean', 'median', 'max']).round(4))
print('\nfraction of each cluster changed at all:')
print(prof.assign(changed=prof['row_drift'] > 0).groupby('cluster')['changed'].mean().round(4))
print('\nmatched targets:', prof[TARGET].notna().sum(), 'of', len(prof))
print('\nper-cluster target rate, drift, and AUC new vs old:')
for c, g in prof.groupby('cluster'):
    m = g[TARGET].notna() & g['pred_new'].notna()
    auc_n = roc_auc_score(g.loc[m, TARGET], g.loc[m, 'pred_new']) if m.sum() else float('nan')
    auc_o = roc_auc_score(g.loc[m, TARGET], g.loc[m, 'pred_old']) if m.sum() else float('nan')
    print(f'cluster {c}: n={len(g):>7,} | bad_rate={g[TARGET].mean():.4f} '
          f'| mean_drift={g["row_drift"].mean():.4f} | AUC new={auc_n:.4f} old={auc_o:.4f}')

prof.to_parquet(os.path.join(MODELS, 'cluster_drift_profile.parquet'))
print('\nsaved ->', os.path.join(MODELS, 'cluster_drift_profile.parquet'))
