import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import torch
from csi_fall.io import window_labels, unwrap_timestamp, validate_records, load_record
from csi_fall.multichannel import channel_products
from csi_fall.features import extract, CHOICES
from csi_fall.engine import fit_normalization, train
from csi_fall.models import FallModel
from csi_fall.prepare import prepare
from csi_fall.demo import generate
from csi_fall.cli import predict

torch.set_num_threads(2)


def test_acf_time_average_matches_independent_dot_products():
    rng = np.random.default_rng(7)
    a = rng.normal(size=(1, 120, 30)).astype(np.float32)
    mean, energy, curve, _ = channel_products(torch.tensor(a), 40)
    centered = a[0].astype(float)-a[0].astype(float).mean(0)
    expected = np.array([(centered[k:]*centered[:120-k]).sum(0)/np.square(centered).sum(0) for k in range(17)])
    grid = np.linspace(0, .4, 129)
    expected = np.stack([np.interp(grid, np.arange(17)/40, v) for v in expected.T], axis=1)
    np.testing.assert_allclose(curve[0, 0], expected, atol=2e-6)
    np.testing.assert_allclose(mean[0, 0].mean(-1), expected.mean(-1), atol=2e-6)
    weights = np.square(centered).sum(0)/np.square(centered).sum()
    np.testing.assert_allclose(energy[0, 0].mean(-1), expected @ weights, atol=2e-6)


def test_opposite_channels_are_not_canceled_after_acf():
    signal = torch.sin(torch.arange(120).float()*.2)
    x = torch.stack([signal, -signal], -1)[None]
    mean = channel_products(x, 40)[0]
    before = channel_products(x.mean(-1, keepdim=True), 40)[0]
    assert abs(float(mean[0, 0, 0].mean())-1) < 1e-6
    assert not torch.count_nonzero(before)


def test_anchor_overlap_and_guard_boundaries():
    labels = np.zeros(100, np.int8)
    labels[40:50] = 1
    starts = np.array([0, 15, 20, 30, 45, 55, 60])
    keep, y = window_labels(starts, 30, labels, fs=10, overlap=.5, guard=.5)
    # A 50% overlap is ambiguous (strict > .5), and anchor must be inside.
    np.testing.assert_array_equal(starts[keep], [0, 20, 30, 55, 60])
    np.testing.assert_array_equal(y, [0, 1, 1, 0, 0])


def test_timestamp_wrap():
    np.testing.assert_array_equal(unwrap_timestamp(np.array([2**32-10, 5, 15], float)), [2**32-10, 2**32+5, 2**32+15])


def test_complex_iq_is_not_silently_cast_to_real(tmp_path):
    np.savez(tmp_path/'complex.npz', amplitude=np.ones((120, 30), complex)*(1+2j), fs=40)
    with pytest.raises(ValueError, match='real magnitudes'):
        load_record(dict(format='amplitude', path='complex.npz'), tmp_path)


def test_group_leakage_rejected(tmp_path):
    for name in ('a', 'b'):
        np.savez(tmp_path/f'{name}.npz', amplitude=np.ones((120, 30)), fs=40)
    records = [dict(id=name, group='same_task', path=f'{name}.npz', format='amplitude', role='target', split=split) for name, split in [('a', 'train'), ('b', 'test')]]
    with pytest.raises(ValueError, match='crosses'):
        validate_records(records, tmp_path)


def test_mendeley_csv_adapter(tmp_path):
    segments = []
    for activity, amplitude in [('A01', 3), ('A02', 4)]:
        values = {'timestamp_low': np.arange(10)*100000}
        for rx in range(1, 4):
            for c in range(1, 31):
                values[f'csi_1_{rx}_{c}'] = [f'{amplitude}+0i']*10
        pd.DataFrame(values).to_csv(tmp_path/f'{activity}.csv', index=False)
        segments.append(dict(path=f'{activity}.csv', activity=activity))
    a, y, fs, audit = load_record(dict(format='mendeley', segments=segments, fs=10, resampled_rows=20), tmp_path)
    assert a.shape == (20, 90) and fs == 10 and audit['repaired_gaps'] == 1
    np.testing.assert_allclose(a[:10], 3)
    np.testing.assert_array_equal(y, np.r_[np.zeros(10), np.ones(10)])


@pytest.mark.parametrize('kind', CHOICES)
def test_every_feature_and_encoder_can_backpropagate(kind):
    x = np.random.default_rng(4).normal(30, 3, (2, 120, 30)).astype(np.float32)
    result = extract(x, 40, kind)
    value = result['secondary'].astype(np.float32)
    if kind == 'scalars':
        value = np.concatenate([np.nan_to_num(value), (~np.isfinite(value)).astype(np.float32)], axis=1)
    model = FallModel(kind)
    logits, _, _ = model(torch.tensor(value))
    loss = torch.nn.functional.cross_entropy(logits, torch.tensor([0, 1]))
    loss.backward()
    assert torch.isfinite(loss) and all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())


def test_normalization_only_uses_provided_training_rows(tmp_path):
    np.savez(tmp_path/'x.npz', secondary=np.array([[[1., 3.]], [[1e6, 1e6]]]))
    train_frame = pd.DataFrame([dict(shard='x.npz', row=0)])
    norm = fit_normalization(tmp_path, train_frame, dict(feature='channel_mean', cwt=False))
    assert norm['secondary'] == dict(mean=2., std=1.)


@pytest.mark.parametrize('cwt', [False, True])
def test_raw_to_train_to_prediction_replays_cache(tmp_path, cwt):
    if cwt:
        pytest.importorskip('ssqueezepy')
    generate(tmp_path/'demo', feature='scalars' if cwt else 'channel_mean', cwt=cwt)
    output = prepare(tmp_path/'demo/config.json', batch_size=4)
    checkpoint = train(output)
    predicted = predict(checkpoint, tmp_path/'demo/predict_record.json', tmp_path/'predictions.csv', batch_size=4)
    saved = pd.read_csv(output/'model/external_external_predictions.csv')
    saved['window_start'] = saved.sample_id.str.extract(r'_s(\d+)$').astype(int)
    joined = saved.merge(predicted, on=['recording_id', 'window_start'])
    assert len(joined) == len(saved)
    np.testing.assert_allclose(joined.probability_fall_x, joined.probability_fall_y, atol=2e-6)
    summary = json.loads((output/'model/summary.json').read_text())
    assert summary['status'] == 'complete'
    if not cwt:
        assert summary['parameters'] == 44098
    assert summary['stages']['refit']['epochs_run'] == summary['stages']['selection']['best_epoch']
    # Changed raw data cannot silently reuse a cache.
    f = tmp_path/'demo/synthetic_0.csv'
    f.write_text(f.read_text()+'\n')
    with pytest.raises(ValueError, match='different inputs'):
        prepare(tmp_path/'demo/config.json')


def test_cwt_fusion_backward():
    pytest.importorskip('ssqueezepy')
    x = np.random.default_rng(4).normal(30, 3, (2, 120, 30)).astype(np.float32)
    data = extract(x, 40, 'scalars', cwt=True)
    v = data['secondary']
    v = np.concatenate([np.nan_to_num(v), (~np.isfinite(v)).astype(np.float32)], axis=1)
    model = FallModel('scalars', cwt=True)
    logits, z, aux = model(torch.tensor(v), torch.tensor(data['cwt'].astype(np.float32)))
    (logits.square().mean()+aux.square().mean()).backward()
    assert z.shape == (2, 192)
    assert next(model.cwt_encoder.parameters()).grad is not None
    assert next(model.encoder.parameters()).grad is not None
