"""Train-only normalization, source pretraining, target refit, and held-out evaluation."""
import copy
import json
import random
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score, average_precision_score, confusion_matrix
from .io import write_json
from .models import FallModel


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def scores(y, probability, threshold=.5):
    y, p = np.asarray(y), np.asarray(probability)
    predicted = p >= threshold
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
    both = len(np.unique(y)) == 2
    return dict(n=len(y), fall_f1=float(f1_score(y, predicted, zero_division=0)), macro_f1=float(f1_score(y, predicted, labels=[0, 1], average='macro', zero_division=0)), precision=float(precision_score(y, predicted, zero_division=0)), recall=float(recall_score(y, predicted, zero_division=0)), auroc=float(roc_auc_score(y, p)) if both else None, ap=float(average_precision_score(y, p)) if both else None, tn=int(tn), fp=int(fp), fn=int(fn), tp=int(tp), threshold=threshold)


def read_shard(cache, name):
    path = Path(cache)/name
    if path.is_dir():
        return {p.stem: np.load(p, mmap_mode='r', allow_pickle=False) for p in path.glob('*.npy')}
    # Also accepts the NPZ shards from initial packaging smoke tests.
    with np.load(path, allow_pickle=False) as d:
        return {k: d[k] for k in d.files}


def fit_normalization(cache, frame, cfg):
    result = {}
    for name in ['secondary'] + (['cwt'] if cfg['cwt'] else []):
        scalar = name == 'secondary' and cfg['feature'] == 'scalars'
        parts, count, total, squares = [], 0, 0., 0.
        for shard, group in frame.groupby('shard', sort=False):
            values = read_shard(cache, shard)[name][group.row.to_numpy()].astype(float)
            if scalar:
                parts.append(values)
            else:
                if not np.isfinite(values).all():
                    raise ValueError('Nonfinite model features')
                count += values.size
                total += values.sum()
                squares += np.square(values).sum()
        if scalar:
            values = np.concatenate(parts)
            median = np.array([np.median(c[np.isfinite(c)]) if np.isfinite(c).any() else 0. for c in values.T])
            filled = np.where(np.isfinite(values), values, median)
            result[name] = dict(median=median.tolist(), mean=filled.mean(0).tolist(), std=np.maximum(filled.std(0), 1e-6).tolist())
        else:
            mean = total/count
            result[name] = dict(mean=float(mean), std=max(float(max(squares/count-mean*mean, 0)**.5), 1e-6))
    result['fit_samples'] = len(frame)
    return result


def normalize(value, cfg, scalar=False):
    x = np.asarray(value, np.float32)
    if scalar:
        missing = ~np.isfinite(x)
        x = np.where(missing, np.asarray(cfg['median']), x)
        x = (x-np.asarray(cfg['mean']))/np.asarray(cfg['std'])
        return np.concatenate([x, missing.astype(float)], axis=-1).astype(np.float32)
    return ((x-cfg['mean'])/cfg['std']).astype(np.float32)


class FeatureDataset(Dataset):
    def __init__(self, cache, frame, cfg, norm):
        self.cache, self.frame, self.cfg, self.norm = Path(cache), frame.reset_index(drop=True), cfg, norm

    @lru_cache(maxsize=32)
    def shard(self, name):
        return read_shard(self.cache, name)

    def __len__(self):
        return len(self.frame)

    def __getitem__(self, index):
        r = self.frame.iloc[index]
        block = self.shard(r.shard)
        x = normalize(block['secondary'][r.row], self.norm['secondary'], self.cfg['feature'] == 'scalars')
        c = normalize(block['cwt'][r.row], self.norm['cwt']) if self.cfg['cwt'] else np.zeros(1, np.float32)
        return torch.from_numpy(x), torch.from_numpy(c), int(r.label)


@torch.no_grad()
def infer(model, loader, device):
    model.eval()
    probabilities, embeddings = [], []
    for x, c, _ in loader:
        logits, z, _ = model(x.to(device), c.to(device))
        probabilities.append(logits.softmax(-1)[:, 1].cpu().numpy())
        embeddings.append(z.cpu().numpy())
    if not probabilities:
        raise ValueError('No windows for evaluation')
    return np.concatenate(probabilities), np.concatenate(embeddings)


def save_checkpoint(path, value):
    temp = Path(path).with_suffix('.tmp.pt')
    torch.save(value, temp)
    temp.replace(path)


def stage(model, cache, train_frame, val_frame, cfg, norm, out, device, epochs, patience, lr, batch_size, seed, auxiliary_weight):
    out.mkdir(parents=True, exist_ok=True)
    seed_all(seed)
    if not len(train_frame) or set(train_frame.label) != {0, 1}:
        raise ValueError('Training requires both fall and non-fall windows')
    if val_frame is not None and (not len(val_frame) or set(val_frame.label) != {0, 1}):
        raise ValueError('Validation requires both classes in independent recordings')
    train = FeatureDataset(cache, train_frame, cfg, norm)
    loader = DataLoader(train, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(FeatureDataset(cache, val_frame, cfg, norm), batch_size=batch_size) if val_frame is not None else None
    counts = np.bincount(train_frame.label, minlength=2)
    weights = torch.tensor(len(train_frame)/(2*counts), device=device, dtype=torch.float32)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    history, best, best_epoch = [], -1., 0
    for epoch in range(1, epochs+1):
        model.train()
        total = 0.
        for x, c, y in loader:
            x, c, y = x.to(device), c.to(device), y.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits, _, auxiliary = model(x, c)
            loss = nn.functional.cross_entropy(logits, y, weight=weights)
            if auxiliary is not None:
                loss = loss + auxiliary_weight*nn.functional.cross_entropy(auxiliary, y, weight=weights)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            total += float(loss.detach())*len(y)
        measure = scores(val_frame.label, infer(model, val_loader, device)[0]) if val_loader else None
        improved = measure is None or measure['macro_f1'] > best+1e-6
        if improved:
            best_epoch = epoch
            best = measure['macro_f1'] if measure else float(epoch)
            save_checkpoint(out/'best.pt', {'model': model.state_dict(), 'epoch': epoch})
        history.append(dict(epoch=epoch, loss=total/len(train), validation=measure))
        save_checkpoint(out/'last.pt', {'model': model.state_dict(), 'optimizer': optimizer.state_dict(), 'epoch': epoch})
        write_json(out/'history.json', history)
        print(json.dumps(dict(stage=out.name, epoch=epoch, loss=history[-1]['loss'], val_macro_f1=measure['macro_f1'] if measure else None)), flush=True)
        if val_loader and epoch-best_epoch >= patience:
            break
    model.load_state_dict(torch.load(out/'best.pt', map_location=device, weights_only=False)['model'])
    train.shard.cache_clear()
    return dict(best_epoch=best_epoch, epochs_run=len(history), validation=history[best_epoch-1]['validation'])


def check_manifest(frame):
    if not frame.sample_id.is_unique:
        raise ValueError('Duplicate windows')
    for col in ('recording_id', 'group'):
        if (frame.groupby(col)[['role', 'split']].nunique() > 1).any().any():
            raise ValueError(f'{col} crosses splits')


def train(output, device='cpu', initialize=None):
    out = Path(output).resolve()
    cache = out/'cache'
    meta = json.loads((cache/'metadata.json').read_text())
    cfg = meta['config']
    params = {'seed': 42, 'batch_size': 256, 'source_epochs': 20, 'target_epochs': 30, 'source_patience': 3, 'target_patience': 5, 'source_lr': .001, 'target_lr': .0001, 'refit': True, 'auxiliary_weight': .3, **cfg.get('training', {})}
    for name in ['batch_size', 'source_epochs', 'target_epochs', 'source_patience', 'target_patience']:
        if int(params[name]) != params[name] or params[name] < 1:
            raise ValueError(f'{name} must be a positive integer')
    frame = pd.read_csv(cache/'manifest.csv')
    check_manifest(frame)
    select = lambda role, split: frame[(frame.role == role) & (frame.split == split)]
    source_train, source_val = select('source', 'train'), select('source', 'val')
    target_train, target_val = select('target', 'train'), select('target', 'val')
    if initialize and len(source_train):
        raise ValueError('Use initialize with a target-only configuration')
    fit = source_train if len(source_train) else target_train
    if not len(fit):
        raise ValueError('No training split')
    dest = out/'model'
    if dest.exists():
        raise FileExistsError('Model directory already exists; choose a new experiment output to train again')
    seed_all(params['seed'])
    model = FallModel(cfg['feature'], cfg['cwt'], cfg['backbone'], cfg.get('imagenet_pretrained', False) and initialize is None).to(device)
    if initialize:
        initial = torch.load(initialize, map_location=device, weights_only=False)
        if any(initial['config'][k] != cfg[k] for k in ('feature', 'cwt', 'backbone')):
            raise ValueError('Initialization checkpoint feature/model configuration mismatch')
        model.load_state_dict(initial['model'])
        norm = initial['normalization']
    else:
        norm = fit_normalization(cache, fit, cfg)
    dest.mkdir(parents=True)
    write_json(dest/'normalization.json', norm)
    write_json(dest/'training_config.json', params)
    outcomes = {}
    kwargs = dict(cache=cache, cfg=cfg, norm=norm, device=device, batch_size=params['batch_size'], auxiliary_weight=params['auxiliary_weight'])
    if len(source_train):
        outcomes['source'] = stage(model, train_frame=source_train, val_frame=source_val, out=dest/'source', epochs=params['source_epochs'], patience=params['source_patience'], lr=params['source_lr'], seed=params['seed'], **kwargs)
        save_checkpoint(dest/'source_model.pt', {'model': model.state_dict(), 'config': cfg, 'normalization': norm})
    if len(target_train):
        state_before_target = copy.deepcopy(model.state_dict())
        outcomes['selection'] = stage(model, train_frame=target_train, val_frame=target_val, out=dest/'selection', epochs=params['target_epochs'], patience=params['target_patience'], lr=params['target_lr'], seed=params['seed']+1, **kwargs)
        save_checkpoint(dest/'selection_model.pt', {'model': model.state_dict(), 'config': cfg, 'normalization': norm})
        if params['refit']:
            model.load_state_dict(state_before_target)
            outcomes['refit'] = stage(model, train_frame=pd.concat([target_train, target_val]), val_frame=None, out=dest/'refit', epochs=outcomes['selection']['best_epoch'], patience=1, lr=params['target_lr'], seed=params['seed']+100, **kwargs)
    checkpoint = dest/'model.pt'
    save_checkpoint(checkpoint, {'model': model.state_dict(), 'config': cfg, 'normalization': norm, 'threshold': .5, 'outcomes': outcomes, 'cache_signature': meta['signature'], 'training': params, 'version': 1})
    heldout = {}
    for role, split in [('target', 'test'), ('external', 'external')]:
        subset = select(role, split)
        if not len(subset):
            continue
        loader = DataLoader(FeatureDataset(cache, subset, cfg, norm), batch_size=params['batch_size'])
        p, z = infer(model, loader, device)
        name = role+'_'+split
        prediction = subset[['sample_id', 'recording_id', 'label']].copy()
        prediction['probability_fall'], prediction['predicted_fall'] = p, p >= .5
        prediction.to_csv(dest/(name+'_predictions.csv'), index=False)
        np.savez(dest/(name+'_embeddings.npz'), sample_id=subset.sample_id.to_numpy(dtype=str), embedding=z)
        heldout[name] = scores(subset.label, p)
    write_json(dest/'summary.json', dict(status='complete', parameters=sum(p.numel() for p in model.parameters()), stages=outcomes, heldout=heldout, threshold=.5, normalized_on='initial checkpoint training set' if initialize else 'source train' if len(source_train) else 'target train'))
    print(f'Model saved: {checkpoint}', flush=True)
    return checkpoint
