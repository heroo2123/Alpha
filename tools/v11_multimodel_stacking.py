"""Small, nonfinancial, grouped linear-pool reference evaluator.

Not a model bundle and not connected to runtime inference. Real historical point
panels cannot enter this evaluator as exact daily-extreme examples. Its numerical
and leakage contracts are exercised with explicitly synthetic complete examples.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import math
import random

from tools.v11_multimodel_panel import (
    CONFIRMATION, FAMILIES, FLAGS, PROVIDERS, SPLITS, digest, require,
)

POLICY = dict(
    version='alpha_v11_grouped_linear_pool_v1', primary_metric='CITY_DAY_MEAN_MULTICLASS_BRIER',
    tie_break='GRID_ORDER', providers=list(PROVIDERS),
    dependence_groups={'NOAA_GEFS': ['GEFS'], 'ECMWF_LINEAGE': ['IFS', 'AIFS']},
    independence_claim=False, member_count_is_sample_size=False,
    train_ifs_share_grid=[0., .5, 1.], development_gefs_share_grid=[0., .25, .5, .75, 1.],
    selection='TRAIN selects IFS share within ECMWF; DEVELOPMENT selects GEFS group share; freeze before confirmation',
    minimum_city_days={'TRAIN': 200, 'DEVELOPMENT': 50, 'HISTORICAL_CONFIRMATION': 50},
    minimum_stations=10, reliability_bins=10,
    log_loss='EXACT_NO_PROBABILITY_CLIPPING; zero winning probability is infinite',
    uncertainty='PAIRED_DATE_BLOCK_BOOTSTRAP; regional dependence may remain',
    bootstrap_resamples=500, seed=20260930, required_improvement=0., maximum_slice_regression=.02,
    promotion_policy='NO_PROMOTION_REGARDLESS_OF_METRICS', conservative_bounds=[0., 1.],
    confirmation_role=CONFIRMATION,
)


def vector(p):
    require(2 <= len(p) <= 128 and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in p)
            and abs(math.fsum(p)-1) < 1e-10, 'COHERENT_PROBABILITY_VECTOR')
    return tuple(p)


def member_probabilities(members_c, cuts_c, *, bias_c=0., sigma_c=.5):
    """Scenario mixture, with one normalized provider budget regardless of count."""
    require(1 <= len(members_c) <= 1000 and all(math.isfinite(v) for v in members_c), 'MEMBER_VALUES')
    require(math.isfinite(bias_c) and math.isfinite(sigma_c) and sigma_c > 0, 'KERNEL_PARAMETERS')
    require(len(cuts_c) >= 1 and all(math.isfinite(v) for v in cuts_c)
            and all(a < b for a, b in zip(cuts_c, cuts_c[1:])), 'BUCKET_CUTS')
    cdfs = [0.] + [math.fsum(.5*math.erfc(-(x-v-bias_c)/(sigma_c*math.sqrt(2))) for v in members_c)/len(members_c)
                  for x in cuts_c] + [1.]
    return vector([b-a for a, b in zip(cdfs, cdfs[1:])])


def pool(probabilities, gefs_share, ifs_share):
    require(set(probabilities) == set(PROVIDERS) and 0 <= gefs_share <= 1 and 0 <= ifs_share <= 1, 'POOL_SHAPE')
    ps = {p: vector(v) for p, v in probabilities.items()}
    require(len({len(v) for v in ps.values()}) == 1, 'POOL_BUCKETS')
    return vector([gefs_share*g + (1-gefs_share)*(ifs_share*i+(1-ifs_share)*a)
                   for g, i, a in zip(ps['GEFS'], ps['IFS'], ps['AIFS'])])


@dataclass(frozen=True)
class EvaluationExample:
    event_id: str
    city_day: str
    station: str
    local_date: str
    family: str
    split: str
    probabilities: dict
    winner: int
    decision_at: float
    feature_available_at: float
    label_knowable_at: float
    feature_semantics: str
    evidence_class: str


def validate_examples(rows, fit_cutoff):
    require(rows and len(rows) <= 20000 and math.isfinite(fit_cutoff), 'EVALUATION_INPUTS')
    events, city_splits, dates = set(), {}, defaultdict(set)
    station_days, city_dates, targets = {}, {}, set()
    for r in rows:
        # No production adapter presently establishes this contract. Synthetic
        # execution tests the math only; it is never reported as actual evidence.
        require(r.evidence_class == 'SYNTHETIC', 'REAL_EXACT_DAY_ADAPTER_NOT_IMPLEMENTED')
        require(r.feature_semantics == 'EXACT_LOCAL_DAY_EXTREME', 'EXACT_DAY_FEATURES_REQUIRED')
        require(r.family in FAMILIES and r.split in SPLITS and r.event_id not in events, 'EVENT_SPLIT_IDENTITY')
        require(r.city_day and r.station and date.fromisoformat(r.local_date).isoformat() == r.local_date,
                'CITY_DAY_IDENTITY')
        require(r.city_day not in city_splits or city_splits[r.city_day] == r.split, 'CITY_DAY_SPLIT_LEAKAGE')
        sd = (r.station, r.local_date)
        require(sd not in station_days or station_days[sd] == r.city_day, 'STATION_DAY_CITY_ALIAS')
        require(r.city_day not in city_dates or city_dates[r.city_day] == r.local_date, 'CITY_DAY_DATE_IDENTITY')
        require((sd, r.family) not in targets, 'DUPLICATE_STATION_DAY_FAMILY')
        station_days[sd], city_dates[r.city_day] = r.city_day, r.local_date
        targets.add((sd, r.family))
        events.add(r.event_id)
        city_splits[r.city_day] = r.split
        dates[r.split].add(r.local_date)
        require(all(math.isfinite(v) for v in (r.feature_available_at, r.decision_at, r.label_knowable_at))
                and r.feature_available_at <= r.decision_at < r.label_knowable_at <= fit_cutoff, 'CAUSAL_CUTOFF')
        require(set(r.probabilities) == set(PROVIDERS), 'PROVIDER_SET')
        ps = [vector(p) for p in r.probabilities.values()]
        require(len({len(p) for p in ps}) == 1 and type(r.winner) is int and 0 <= r.winner < len(ps[0]), 'WINNER_OR_SHAPE')
    require(all(dates[s] for s in SPLITS) and max(dates['TRAIN']) < min(dates['DEVELOPMENT'])
            and max(dates['DEVELOPMENT']) < min(dates['HISTORICAL_CONFIRMATION']), 'TEMPORAL_SPLIT_LEAKAGE')
    for before, after in zip(SPLITS, SPLITS[1:]):
        require(max(r.label_knowable_at for r in rows if r.split == before) <=
                min(r.decision_at for r in rows if r.split == after), 'LABEL_NOT_KNOWABLE_AT_HISTORICAL_FIT')


def loss(p, winner):
    return math.fsum((v-(i == winner))**2 for i, v in enumerate(p))


def city_day_mean(rows, values):
    groups = defaultdict(list)
    for r, value in zip(rows, values):
        groups[r.city_day].append(value)
    return math.fsum(math.fsum(v)/len(v) for v in groups.values())/len(groups)


def metrics(rows, predictions):
    require(rows and len(rows) == len(predictions), 'METRIC_ROWS')
    counts = defaultdict(int)
    for r in rows:
        counts[r.city_day] += 1
    bins = [dict(weight=0., predicted=0., observed=0., bucket_count=0) for _ in range(10)]
    n = len(counts)
    logs = []
    for r, p in zip(rows, predictions):
        vector(p)
        logs.append(-math.log(p[r.winner]) if p[r.winner] else math.inf)
        for j, value in enumerate(p):
            b = bins[min(9, int(value*10))]
            w = 1/(n*counts[r.city_day]*len(p))
            b['weight'] += w
            b['predicted'] += w*value
            b['observed'] += w*(j == r.winner)
            b['bucket_count'] += 1
    for b in bins:
        b['mean_probability'] = b['predicted']/b['weight'] if b['weight'] else None
        b['observed_frequency'] = b['observed']/b['weight'] if b['weight'] else None
        del b['predicted'], b['observed']
    return dict(brier=city_day_mean(rows, [loss(p, r.winner) for r, p in zip(rows, predictions)]),
                log_loss=None if any(math.isinf(v) for v in logs) else city_day_mean(rows, logs),
                infinite_log_loss=any(math.isinf(v) for v in logs),
                sharpness=city_day_mean(rows, [sum(v*v for v in p) for p in predictions]),
                calibration_error=sum(b['weight']*abs(b['mean_probability']-b['observed_frequency']) for b in bins if b['weight']),
                reliability=bins, events=len(rows), city_days=n, stations=len({r.station for r in rows}),
                date_blocks=len({r.local_date for r in rows}), effective_independent_sample_count=None)


def freeze_selection(train, development):
    """Confirmation rows cannot be passed through this API."""
    require(train and development and all(r.split == 'TRAIN' for r in train)
            and all(r.split == 'DEVELOPMENT' for r in development), 'SELECTION_PARTITIONS')
    require(not {r.city_day for r in train} & {r.city_day for r in development}
            and max(r.local_date for r in train) < min(r.local_date for r in development), 'SELECTION_LEAKAGE')
    attempts = []
    for share in POLICY['train_ifs_share_grid']:
        score = city_day_mean(train, [loss(pool(r.probabilities, 0., share), r.winner) for r in train])
        attempts.append(dict(stage='TRAIN', ifs_share=share, brier=score))
    ifs = min(attempts, key=lambda a: a['brier'])['ifs_share']
    dev = []
    for share in POLICY['development_gefs_share_grid']:
        score = city_day_mean(development, [loss(pool(r.probabilities, share, ifs), r.winner) for r in development])
        dev.append(dict(stage='DEVELOPMENT', gefs_share=share, ifs_share=ifs, brier=score))
    gefs = min(dev, key=lambda a: a['brier'])['gefs_share']
    frozen = dict(gefs_share=gefs, ifs_share=ifs, attempts=attempts+dev,
                  policy_sha256=digest(POLICY), selection_partitions=['TRAIN', 'DEVELOPMENT'])
    return dict(parameters=frozen, sha256=digest(frozen))


def paired_date_bootstrap(rows, candidate, reference):
    groups = defaultdict(list)
    for r, c, b in zip(rows, candidate, reference):
        groups[r.local_date].append((r, loss(c, r.winner)-loss(b, r.winner)))
    dates = sorted(groups)
    rng = random.Random(POLICY['seed'])
    estimates = []
    for _ in range(POLICY['bootstrap_resamples']):
        # Resample whole dates, preserving simultaneous station/HIGH/LOW dependence.
        sums, weights = 0., 0
        for key in rng.choices(dates, k=len(dates)):
            block = groups[key]
            n = len({r.city_day for r, _ in block})
            sums += n*city_day_mean([r for r, _ in block], [v for _, v in block])
            weights += n
        estimates.append(sums/weights)
    estimates.sort()
    return dict(brier_delta=city_day_mean(rows, [loss(c, r.winner)-loss(b, r.winner) for r, c, b in zip(rows, candidate, reference)]),
                lower=estimates[12], upper=estimates[487], date_blocks=len(dates),
                interval='DESCRIPTIVE_DATE_BLOCK_BOOTSTRAP_NOT_ACCEPTANCE', seed=POLICY['seed'])


def evaluate(rows, *, fit_cutoff):
    validate_examples(rows, fit_cutoff)
    result = dict(evidence_class='SYNTHETIC', training_status='FITTED_NOT_CALIBRATED',
                  promotion_status='NO_PROMOTION', confirmation_role=CONFIRMATION,
                  policy=POLICY, families={}, **FLAGS)
    for family in FAMILIES:
        groups = {s: [r for r in rows if r.family == family and r.split == s] for s in SPLITS}
        require(all(groups.values()), 'MISSING_FAMILY_SPLIT')
        frozen = freeze_selection(groups['TRAIN'], groups['DEVELOPMENT'])
        pars = frozen['parameters']
        scores = {}
        for split, batch in groups.items():
            predictions = {p+'_ONLY': [r.probabilities[p] for r in batch] for p in PROVIDERS}
            predictions['MULTI_MODEL'] = [pool(r.probabilities, pars['gefs_share'], pars['ifs_share']) for r in batch]
            scores[split] = {p: dict(overall=metrics(batch, values),
                                     stations={s: metrics([r for r in batch if r.station == s],
                                                           [v for r, v in zip(batch, values) if r.station == s])
                                               for s in sorted({r.station for r in batch})})
                             for p, values in predictions.items()}
            scores[split]['paired_comparison_vs_gefs'] = paired_date_bootstrap(batch, predictions['MULTI_MODEL'], predictions['GEFS_ONLY'])
        sparse = any(len({r.city_day for r in batch}) < POLICY['minimum_city_days'][s]
                     or len({r.station for r in batch}) < POLICY['minimum_stations'] for s, batch in groups.items())
        result['families'][family] = dict(frozen=frozen, scores=scores, sparse_evidence=sparse)
    return result
