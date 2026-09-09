#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from evaluate_feature_pair_domain_robustness import parse_spec, read_manifest, resolve_path


LABELS = [0, 1]


@dataclass(frozen=True)
class FusionSample:
    sample_id: str
    feature_path_a: str
    feature_path_b: str
    label: int
    binary_label: str
    task_id: str
    split_group: str
    source_csv: str
    env: str
    subject: str
    rx: str
    condition: str
    activity: str
    trial_block: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train ResNet18 encoders for S3 scalograms and ACF maps, then compare "
            "single-branch, probability ensemble, and frozen-embedding concat fusion."
        )
    )
    parser.add_argument("--feature-a", required=True, help="Usually S3 spec: name|manifest|key|description")
    parser.add_argument("--feature-b", required=True, help="Usually ACF spec: name|manifest|key|description")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--pretrained-resnet18",
        type=Path,
        default=Path("artifacts/pretrained/resnet18_imagenet1k_v1_f37072fd.pth"),
    )
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--fusion-epochs", type=int, default=80)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--embedding-dim", type=int, default=512)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--fusion-learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--balanced-sampler", action="store_true")
    parser.add_argument("--class-weighted-loss", action="store_true")
    parser.add_argument("--split-mode", choices=("group_random", "random", "leave_env"), default="group_random")
    parser.add_argument("--heldout-domain", default=None)
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--device", choices=("auto", "mps", "cuda", "cpu"), default="auto")
    parser.add_argument("--save-embeddings", action="store_true")
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def select_device(requested: str) -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable.")
        return torch.device("cuda")
    if requested == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but unavailable.")
        return torch.device("mps")
    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def label_from_row(row: dict[str, str]) -> int:
    if row.get("binary_label") == "fall":
        return 1
    if row.get("binary_label") == "non_fall":
        return 0
    return int(float(row.get("binary_label_idx", "0")))


def binary_label_text(row: dict[str, str]) -> str:
    label = row.get("binary_label", "")
    if label:
        return label
    return "fall" if label_from_row(row) == 1 else "non_fall"


def feature_key(spec: Any) -> str:
    if spec.feature_key.startswith("source_s3:"):
        return spec.feature_key.split(":", 1)[1]
    return spec.feature_key


def feature_path(row: dict[str, str], spec: Any, project_root: Path) -> Path:
    if spec.feature_key.startswith("source_s3:"):
        return resolve_path(row["source_s3_feature_path"], project_root)
    return resolve_path(row["feature_path"], project_root)


def split_group_for_row(row: dict[str, str], sample_id: str) -> str:
    task_id = row.get("task_id", "")
    rx = str(row.get("rx", ""))
    if task_id and rx:
        return f"{task_id}_rx{rx}"
    if task_id:
        return task_id
    return row.get("source_csv", "") or sample_id


def build_samples(spec_a: Any, spec_b: Any, project_root: Path, max_samples: int | None, seed: int) -> list[FusionSample]:
    rows_a = read_manifest(spec_a.manifest, project_root)
    rows_b = read_manifest(spec_b.manifest, project_root)
    sample_ids = sorted(set(rows_a) & set(rows_b))
    samples: list[FusionSample] = []
    for sample_id in sample_ids:
        row_a = rows_a[sample_id]
        row_b = rows_b[sample_id]
        if label_from_row(row_a) != label_from_row(row_b):
            raise ValueError(f"label mismatch for sample_id={sample_id}")
        path_a = feature_path(row_a, spec_a, project_root)
        path_b = feature_path(row_b, spec_b, project_root)
        if not path_a.exists() or not path_b.exists():
            continue
        samples.append(
            FusionSample(
                sample_id=sample_id,
                feature_path_a=str(path_a),
                feature_path_b=str(path_b),
                label=label_from_row(row_a),
                binary_label=binary_label_text(row_a),
                task_id=row_a.get("task_id", ""),
                split_group=split_group_for_row(row_a, sample_id),
                source_csv=row_a.get("source_csv", ""),
                env=row_a.get("env", ""),
                subject=row_a.get("subject", ""),
                rx=str(row_a.get("rx", "")),
                condition=row_a.get("condition", ""),
                activity=row_a.get("activity", ""),
                trial_block=row_a.get("trial_block", ""),
            )
        )
    if max_samples is not None and len(samples) > max_samples:
        rng = np.random.default_rng(seed)
        by_label: dict[int, list[FusionSample]] = {0: [], 1: []}
        for sample in samples:
            by_label[sample.label].append(sample)
        selected: list[FusionSample] = []
        per_label = max(1, max_samples // 2)
        for label_samples in by_label.values():
            shuffled = list(label_samples)
            rng.shuffle(shuffled)
            selected.extend(shuffled[:per_label])
        if len(selected) < max_samples:
            selected_set = set(selected)
            leftovers = [sample for sample in samples if sample not in selected_set]
            rng.shuffle(leftovers)
            selected.extend(leftovers[: max_samples - len(selected)])
        samples = sorted(selected, key=lambda sample: sample.sample_id)
    if not samples:
        raise ValueError("No aligned samples found.")
    return samples


def split_samples(
    samples: list[FusionSample],
    split_mode: str,
    val_ratio: float,
    heldout_domain: str | None,
    seed: int,
) -> tuple[list[FusionSample], list[FusionSample], dict[str, Any]]:
    rng = np.random.default_rng(seed)
    if split_mode == "leave_env":
        if not heldout_domain:
            raise ValueError("--heldout-domain is required for leave_env")
        train = [sample for sample in samples if sample.env != heldout_domain]
        val = [sample for sample in samples if sample.env == heldout_domain]
        return train, val, {"mode": "leave_env", "heldout_domain": heldout_domain}
    if split_mode == "random":
        indices = np.arange(len(samples))
        labels = np.asarray([sample.label for sample in samples])
        val_indices: set[int] = set()
        for label in sorted(set(labels.tolist())):
            label_indices = indices[labels == label]
            rng.shuffle(label_indices)
            val_count = max(1, int(round(len(label_indices) * val_ratio)))
            val_indices.update(int(index) for index in label_indices[:val_count])
        train = [sample for idx, sample in enumerate(samples) if idx not in val_indices]
        val = [sample for idx, sample in enumerate(samples) if idx in val_indices]
        return train, val, {"mode": "random", "val_ratio": val_ratio}

    label_by_group: dict[str, int] = {}
    groups_by_label: dict[int, list[str]] = {0: [], 1: []}
    for sample in samples:
        group = sample.split_group or sample.source_csv or sample.sample_id
        label_by_group.setdefault(group, sample.label)
    for group, label in label_by_group.items():
        groups_by_label[int(label)].append(group)
    val_groups: set[str] = set()
    for groups in groups_by_label.values():
        groups = sorted(groups)
        rng.shuffle(groups)
        val_count = max(1, int(round(len(groups) * val_ratio)))
        val_groups.update(groups[:val_count])
    train = [sample for sample in samples if (sample.split_group or sample.source_csv or sample.sample_id) not in val_groups]
    val = [sample for sample in samples if (sample.split_group or sample.source_csv or sample.sample_id) in val_groups]
    return train, val, {
        "mode": "group_random",
        "group_key": "task_id_rx_fallback_source_csv",
        "val_ratio": val_ratio,
        "val_group_count": len(val_groups),
    }


def label_counts(samples: list[FusionSample]) -> dict[str, int]:
    return {
        "non_fall": int(sum(sample.label == 0 for sample in samples)),
        "fall": int(sum(sample.label == 1 for sample in samples)),
    }


def load_npz_feature(path_text: str, key: str) -> np.ndarray:
    with np.load(path_text, allow_pickle=False) as data:
        if key in data.files:
            arr = data[key]
        elif key == "s3" and "scalogram_s3" in data.files:
            arr = data["scalogram_s3"]
        elif key == "s3" and "scalogram" in data.files:
            arr = data["scalogram"]
        else:
            raise KeyError(f"{key!r} missing from {path_text}")
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[None, :, :]
    elif arr.ndim == 3:
        pass
    else:
        raise ValueError(f"Expected 2-D or 3-D array for {path_text}:{key}, got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr.astype(np.float32, copy=False)


def compute_scalar_stats(samples: list[FusionSample], key: str, branch: str, max_items: int = 1024) -> tuple[float, float]:
    selected = samples[:max_items]
    total = 0.0
    total_sq = 0.0
    count = 0
    for sample in selected:
        path = sample.feature_path_a if branch == "a" else sample.feature_path_b
        arr = load_npz_feature(path, key)
        total += float(arr.sum(dtype=np.float64))
        total_sq += float(np.square(arr, dtype=np.float32).sum(dtype=np.float64))
        count += int(arr.size)
    mean = total / max(count, 1)
    var = max(total_sq / max(count, 1) - mean * mean, 1e-12)
    return float(mean), float(np.sqrt(var))


class SingleFeatureImageDataset(Dataset[tuple[torch.Tensor, torch.Tensor, str]]):
    def __init__(
        self,
        samples: list[FusionSample],
        key: str,
        branch: str,
        mean: float,
        std: float,
    ) -> None:
        self.samples = samples
        self.key = key
        self.branch = branch
        self.mean = float(mean)
        self.std = max(float(std), 1e-6)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor, str]:
        sample = self.samples[index]
        path = sample.feature_path_a if self.branch == "a" else sample.feature_path_b
        arr = (load_npz_feature(path, self.key) - self.mean) / self.std
        return torch.from_numpy(arr).float(), torch.tensor(sample.label, dtype=torch.long), sample.sample_id


def make_loader(
    dataset: SingleFeatureImageDataset,
    samples: list[FusionSample],
    batch_size: int,
    shuffle: bool,
    balanced_sampler: bool,
    num_workers: int,
) -> DataLoader:
    sampler = None
    if balanced_sampler:
        counts = label_counts(samples)
        weights_by_label = {
            0: 1.0 / max(counts["non_fall"], 1),
            1: 1.0 / max(counts["fall"], 1),
        }
        weights = torch.DoubleTensor([weights_by_label[sample.label] for sample in samples])
        sampler = WeightedRandomSampler(weights, num_samples=len(weights), replacement=True)
        shuffle = False
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle if sampler is None else False,
        sampler=sampler,
        num_workers=num_workers,
        pin_memory=torch.cuda.is_available(),
    )


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes: int, planes: int, stride: int = 1, downsample: nn.Module | None = None) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        if self.downsample is not None:
            identity = self.downsample(x)
        return self.relu(out + identity)


class ResNet18Encoder(nn.Module):
    def __init__(self, embedding_dim: int = 512, dropout: float = 0.2) -> None:
        super().__init__()
        self.inplanes = 64
        self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(64, 2)
        self.layer2 = self._make_layer(128, 2, stride=2)
        self.layer3 = self._make_layer(256, 2, stride=2)
        self.layer4 = self._make_layer(512, 2, stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.projection = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(512, embedding_dim),
            nn.LayerNorm(embedding_dim),
            nn.ReLU(inplace=True),
        )

    def _make_layer(self, planes: int, blocks: int, stride: int = 1) -> nn.Sequential:
        downsample = None
        if stride != 1 or self.inplanes != planes:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes),
            )
        layers: list[nn.Module] = [BasicBlock(self.inplanes, planes, stride, downsample)]
        self.inplanes = planes
        for _ in range(1, blocks):
            layers.append(BasicBlock(self.inplanes, planes))
        return nn.Sequential(*layers)

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        x = self.maxpool(self.relu(self.bn1(self.conv1(x))))
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        return torch.flatten(self.avgpool(x), 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.projection(self.forward_features(x))


class ResNet18Classifier(nn.Module):
    def __init__(self, embedding_dim: int, dropout: float) -> None:
        super().__init__()
        self.encoder = ResNet18Encoder(embedding_dim=embedding_dim, dropout=dropout)
        self.classifier = nn.Linear(embedding_dim, 2)

    def forward(self, x: torch.Tensor, image_size: int) -> dict[str, torch.Tensor]:
        if x.ndim != 4:
            raise ValueError(f"Expected BCHW tensor, got {tuple(x.shape)}")
        if x.shape[1] == 1:
            x = x.repeat(1, 3, 1, 1)
        elif x.shape[1] == 2:
            x = torch.cat([x, x.mean(dim=1, keepdim=True)], dim=1)
        elif x.shape[1] > 3:
            x = x[:, :3]
        if x.shape[-2:] != (image_size, image_size):
            x = F.interpolate(x, size=(image_size, image_size), mode="bilinear", align_corners=False)
        embedding = self.encoder(x)
        return {"embedding": embedding, "logits": self.classifier(embedding)}


def load_resnet18_pretrained(model: ResNet18Classifier, path: Path, project_root: Path) -> dict[str, Any]:
    real_path = resolve_path(path, project_root)
    state = torch.load(real_path, map_location="cpu")
    encoder_state = model.encoder.state_dict()
    filtered = {
        key: value
        for key, value in state.items()
        if key in encoder_state and tuple(value.shape) == tuple(encoder_state[key].shape)
    }
    missing, unexpected = model.encoder.load_state_dict(filtered, strict=False)
    return {
        "path": str(real_path),
        "loaded_key_count": len(filtered),
        "missing_key_count": len(missing),
        "unexpected_key_count": len(unexpected),
        "missing_keys_preview": list(missing)[:10],
        "unexpected_keys_preview": list(unexpected)[:10],
    }


def class_weights(samples: list[FusionSample], device: torch.device) -> torch.Tensor:
    counts = label_counts(samples)
    total = max(counts["non_fall"] + counts["fall"], 1)
    return torch.tensor(
        [
            total / max(2 * counts["non_fall"], 1),
            total / max(2 * counts["fall"], 1),
        ],
        dtype=torch.float32,
        device=device,
    )


def metrics_from_predictions(y_true: np.ndarray, y_pred: np.ndarray, proba_fall: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score

    cm = confusion_matrix(y_true, y_pred, labels=LABELS)
    return {
        "n": int(len(y_true)),
        "nonfall_count": int(np.sum(y_true == 0)),
        "fall_count": int(np.sum(y_true == 1)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)),
        "fall_precision": float(precision_score(y_true, y_pred, pos_label=1, zero_division=0)),
        "fall_recall": float(recall_score(y_true, y_pred, pos_label=1, zero_division=0)),
        "fall_f1": float(f1_score(y_true, y_pred, pos_label=1, zero_division=0)),
        "nonfall_precision": float(precision_score(y_true, y_pred, pos_label=0, zero_division=0)),
        "nonfall_recall": float(recall_score(y_true, y_pred, pos_label=0, zero_division=0)),
        "nonfall_f1": float(f1_score(y_true, y_pred, pos_label=0, zero_division=0)),
        "auroc": float(roc_auc_score(y_true, proba_fall)) if len(np.unique(y_true)) == 2 else "",
        "tn_nonfall": int(cm[0, 0]),
        "fp_nonfall_as_fall": int(cm[0, 1]),
        "fn_fall_as_nonfall": int(cm[1, 0]),
        "tp_fall": int(cm[1, 1]),
    }


def train_one_epoch(
    model: ResNet18Classifier,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: torch.device,
    image_size: int,
) -> dict[str, float]:
    model.train()
    total_loss = 0.0
    total = 0
    correct = 0
    for images, labels, _sample_ids in loader:
        images = images.to(device)
        labels = labels.to(device)
        optimizer.zero_grad(set_to_none=True)
        logits = model(images, image_size=image_size)["logits"]
        loss = criterion(logits, labels)
        loss.backward()
        optimizer.step()
        total_loss += float(loss.item()) * int(labels.numel())
        total += int(labels.numel())
        correct += int((logits.argmax(dim=1) == labels).sum().item())
    return {"loss": total_loss / max(total, 1), "accuracy": correct / max(total, 1)}


@torch.no_grad()
def evaluate_model(
    model: ResNet18Classifier,
    loader: DataLoader,
    device: torch.device,
    image_size: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    model.eval()
    sample_ids: list[str] = []
    y_true: list[int] = []
    y_pred: list[int] = []
    proba_fall: list[float] = []
    embeddings: list[np.ndarray] = []
    probas: list[np.ndarray] = []
    for images, labels, batch_sample_ids in loader:
        images = images.to(device)
        labels = labels.to(device)
        outputs = model(images, image_size=image_size)
        proba = torch.softmax(outputs["logits"], dim=1)
        pred = proba.argmax(dim=1)
        sample_ids.extend(str(value) for value in batch_sample_ids)
        y_true.extend(int(value) for value in labels.cpu().numpy().tolist())
        y_pred.extend(int(value) for value in pred.cpu().numpy().tolist())
        proba_fall.extend(float(value) for value in proba[:, 1].cpu().numpy().tolist())
        embeddings.append(outputs["embedding"].cpu().numpy().astype(np.float32))
        probas.append(proba.cpu().numpy().astype(np.float32))
    arrays = {
        "sample_ids": np.asarray(sample_ids),
        "y_true": np.asarray(y_true, dtype=np.int64),
        "y_pred": np.asarray(y_pred, dtype=np.int64),
        "proba": np.concatenate(probas, axis=0) if probas else np.empty((0, 2), dtype=np.float32),
        "embedding": np.concatenate(embeddings, axis=0) if embeddings else np.empty((0, 0), dtype=np.float32),
    }
    metrics = metrics_from_predictions(arrays["y_true"], arrays["y_pred"], arrays["proba"][:, 1])
    return metrics, arrays


def train_single_branch(
    *,
    branch_name: str,
    train_loader: DataLoader,
    train_eval_loader: DataLoader,
    val_loader: DataLoader,
    train_samples: list[FusionSample],
    output_dir: Path,
    pretrained_path: Path,
    project_root: Path,
    args: argparse.Namespace,
    device: torch.device,
) -> tuple[ResNet18Classifier, dict[str, Any], dict[str, np.ndarray], dict[str, np.ndarray]]:
    model = ResNet18Classifier(embedding_dim=int(args.embedding_dim), dropout=float(args.dropout))
    preload = load_resnet18_pretrained(model, pretrained_path, project_root)
    model = model.to(device)
    criterion = nn.CrossEntropyLoss(weight=class_weights(train_samples, device) if args.class_weighted_loss else None)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.learning_rate), weight_decay=float(args.weight_decay))
    best_metric = -1.0
    best_epoch = -1
    history: list[dict[str, Any]] = []
    branch_dir = output_dir / branch_name
    branch_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    for epoch in range(1, int(args.epochs) + 1):
        train_metrics = train_one_epoch(model, train_loader, optimizer, criterion, device, int(args.image_size))
        val_metrics, _val_arrays = evaluate_model(model, val_loader, device, int(args.image_size))
        record = {
            "epoch": epoch,
            "train_loss": train_metrics["loss"],
            "train_accuracy": train_metrics["accuracy"],
            **{f"val_{key}": value for key, value in val_metrics.items()},
        }
        history.append(record)
        print(
            f"{branch_name} epoch {epoch}/{args.epochs} "
            f"train_loss={train_metrics['loss']:.4f} "
            f"val_macro_f1={val_metrics['macro_f1']:.4f} "
            f"val_acc={val_metrics['accuracy']:.4f}",
            flush=True,
        )
        if float(val_metrics["macro_f1"]) > best_metric:
            best_metric = float(val_metrics["macro_f1"])
            best_epoch = epoch
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "pretrained": preload,
                    "epoch": epoch,
                    "val_metrics": val_metrics,
                },
                branch_dir / "best_model.pt",
            )
    checkpoint = torch.load(branch_dir / "best_model.pt", map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    train_metrics, train_arrays = evaluate_model(model, train_eval_loader, device, int(args.image_size))
    val_metrics, val_arrays = evaluate_model(model, val_loader, device, int(args.image_size))
    write_csv(branch_dir / "history.csv", history)
    summary = {
        "branch": branch_name,
        "pretrained": preload,
        "best_epoch": int(best_epoch),
        "elapsed_sec": round(time.time() - start, 3),
        "train_metrics_best_model": train_metrics,
        "val_metrics": val_metrics,
        "best_checkpoint_metrics": checkpoint.get("val_metrics", {}),
        "outputs": {
            "best_model": str(branch_dir / "best_model.pt"),
            "history": str(branch_dir / "history.csv"),
        },
    }
    (branch_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return model, summary, train_arrays, val_arrays


class EmbeddingConcatDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    def __init__(self, x: np.ndarray, y: np.ndarray) -> None:
        self.x = torch.from_numpy(x.astype(np.float32, copy=False))
        self.y = torch.from_numpy(y.astype(np.int64, copy=False))

    def __len__(self) -> int:
        return int(self.y.numel())

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.x[index], self.y[index]


class ConcatClassifier(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(input_dim),
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def train_concat_classifier(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    *,
    device: torch.device,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], np.ndarray]:
    train_dataset = EmbeddingConcatDataset(x_train, y_train)
    train_loader = DataLoader(train_dataset, batch_size=max(64, int(args.batch_size)), shuffle=True)
    model = ConcatClassifier(
        input_dim=int(x_train.shape[1]),
        hidden_dim=max(128, int(args.embedding_dim)),
        dropout=float(args.dropout),
    ).to(device)
    counts = np.bincount(y_train, minlength=2)
    total = max(int(np.sum(counts)), 1)
    weights = torch.tensor(
        [total / max(2 * int(counts[0]), 1), total / max(2 * int(counts[1]), 1)],
        dtype=torch.float32,
        device=device,
    )
    criterion = nn.CrossEntropyLoss(weight=weights if args.class_weighted_loss else None)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.fusion_learning_rate), weight_decay=float(args.weight_decay))
    best_metric = -1.0
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = -1
    history: list[dict[str, Any]] = []
    x_val_t = torch.from_numpy(x_val.astype(np.float32, copy=False)).to(device)
    for epoch in range(1, int(args.fusion_epochs) + 1):
        model.train()
        total_loss = 0.0
        for xb, yb in train_loader:
            xb = xb.to(device)
            yb = yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * int(yb.numel())
        model.eval()
        with torch.no_grad():
            val_proba = torch.softmax(model(x_val_t), dim=1).cpu().numpy().astype(np.float32)
        val_pred = np.argmax(val_proba, axis=1).astype(np.int64)
        val_metrics = metrics_from_predictions(y_val, val_pred, val_proba[:, 1])
        record = {"epoch": epoch, "train_loss": total_loss / max(len(train_dataset), 1), **val_metrics}
        history.append(record)
        if float(val_metrics["macro_f1"]) > best_metric:
            best_metric = float(val_metrics["macro_f1"])
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        val_proba = torch.softmax(model(x_val_t), dim=1).cpu().numpy().astype(np.float32)
    val_pred = np.argmax(val_proba, axis=1).astype(np.int64)
    metrics = metrics_from_predictions(y_val, val_pred, val_proba[:, 1])
    return {
        "best_epoch": int(best_epoch),
        "metrics": metrics,
        "history": history,
    }, val_proba


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> None:
    args = parse_args()
    if args.num_threads > 0:
        torch.set_num_threads(int(args.num_threads))
    set_seed(int(args.seed))
    project_root = Path.cwd()
    output_dir = resolve_path(args.output_dir, project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_a = parse_spec(args.feature_a)
    spec_b = parse_spec(args.feature_b)
    key_a = feature_key(spec_a)
    key_b = feature_key(spec_b)
    samples = build_samples(spec_a, spec_b, project_root, args.max_samples, int(args.seed))
    train_samples, val_samples, split_info = split_samples(
        samples,
        split_mode=str(args.split_mode),
        val_ratio=float(args.val_ratio),
        heldout_domain=args.heldout_domain,
        seed=int(args.seed),
    )
    if not train_samples or not val_samples:
        raise ValueError(f"empty split train={len(train_samples)} val={len(val_samples)}")
    mean_a, std_a = compute_scalar_stats(train_samples, key_a, "a")
    mean_b, std_b = compute_scalar_stats(train_samples, key_b, "b")
    device = select_device(args.device)

    train_a = SingleFeatureImageDataset(train_samples, key_a, "a", mean_a, std_a)
    val_a = SingleFeatureImageDataset(val_samples, key_a, "a", mean_a, std_a)
    train_b = SingleFeatureImageDataset(train_samples, key_b, "b", mean_b, std_b)
    val_b = SingleFeatureImageDataset(val_samples, key_b, "b", mean_b, std_b)
    train_loader_a = make_loader(train_a, train_samples, int(args.batch_size), True, bool(args.balanced_sampler), int(args.num_workers))
    train_eval_loader_a = make_loader(train_a, train_samples, int(args.batch_size), False, False, int(args.num_workers))
    val_loader_a = make_loader(val_a, val_samples, int(args.batch_size), False, False, int(args.num_workers))
    train_loader_b = make_loader(train_b, train_samples, int(args.batch_size), True, bool(args.balanced_sampler), int(args.num_workers))
    train_eval_loader_b = make_loader(train_b, train_samples, int(args.batch_size), False, False, int(args.num_workers))
    val_loader_b = make_loader(val_b, val_samples, int(args.batch_size), False, False, int(args.num_workers))

    print(
        f"training ResNet18 branches samples={len(samples)} train={len(train_samples)} val={len(val_samples)} "
        f"device={device} feature_a={spec_a.name}:{key_a} feature_b={spec_b.name}:{key_b}",
        flush=True,
    )
    model_a, summary_a, train_arrays_a, val_arrays_a = train_single_branch(
        branch_name="feature_a_resnet18",
        train_loader=train_loader_a,
        train_eval_loader=train_eval_loader_a,
        val_loader=val_loader_a,
        train_samples=train_samples,
        output_dir=output_dir,
        pretrained_path=args.pretrained_resnet18,
        project_root=project_root,
        args=args,
        device=device,
    )
    del model_a
    if device.type == "mps":
        torch.mps.empty_cache()
    model_b, summary_b, train_arrays_b, val_arrays_b = train_single_branch(
        branch_name="feature_b_resnet18",
        train_loader=train_loader_b,
        train_eval_loader=train_eval_loader_b,
        val_loader=val_loader_b,
        train_samples=train_samples,
        output_dir=output_dir,
        pretrained_path=args.pretrained_resnet18,
        project_root=project_root,
        args=args,
        device=device,
    )
    del model_b
    if device.type == "mps":
        torch.mps.empty_cache()

    if not np.array_equal(val_arrays_a["sample_ids"], val_arrays_b["sample_ids"]):
        raise ValueError("validation sample ID order mismatch between branches")
    if not np.array_equal(train_arrays_a["sample_ids"], train_arrays_b["sample_ids"]):
        raise ValueError("train sample ID order mismatch between branches")
    y_val = val_arrays_a["y_true"]
    y_train = train_arrays_a["y_true"]

    proba_ensemble = ((val_arrays_a["proba"] + val_arrays_b["proba"]) / 2.0).astype(np.float32)
    pred_ensemble = np.argmax(proba_ensemble, axis=1).astype(np.int64)
    ensemble_metrics = metrics_from_predictions(y_val, pred_ensemble, proba_ensemble[:, 1])

    x_train_concat = np.concatenate([train_arrays_a["embedding"], train_arrays_b["embedding"]], axis=1)
    x_val_concat = np.concatenate([val_arrays_a["embedding"], val_arrays_b["embedding"]], axis=1)
    concat_summary, concat_proba = train_concat_classifier(
        x_train_concat,
        y_train,
        x_val_concat,
        y_val,
        device=device,
        args=args,
    )

    metric_rows = [
        {"model": "feature_a_resnet18", **summary_a["val_metrics"]},
        {"model": "feature_b_resnet18", **summary_b["val_metrics"]},
        {"model": "probability_ensemble_avg", **ensemble_metrics},
        {"model": "embedding_concat_classifier", **concat_summary["metrics"]},
    ]
    write_csv(output_dir / "metrics_summary.csv", metric_rows)
    write_csv(output_dir / "concat_history.csv", concat_summary["history"])
    prediction_rows = []
    for idx, sample_id in enumerate(val_arrays_a["sample_ids"].tolist()):
        prediction_rows.append(
            {
                "sample_id": sample_id,
                "y_true": int(y_val[idx]),
                "feature_a_proba_fall": float(val_arrays_a["proba"][idx, 1]),
                "feature_b_proba_fall": float(val_arrays_b["proba"][idx, 1]),
                "ensemble_proba_fall": float(proba_ensemble[idx, 1]),
                "concat_proba_fall": float(concat_proba[idx, 1]),
                "feature_a_pred": int(val_arrays_a["y_pred"][idx]),
                "feature_b_pred": int(val_arrays_b["y_pred"][idx]),
                "ensemble_pred": int(pred_ensemble[idx]),
                "concat_pred": int(np.argmax(concat_proba[idx])),
            }
        )
    write_csv(output_dir / "val_predictions.csv", prediction_rows)
    if args.save_embeddings:
        np.save(output_dir / "train_embedding_feature_a.npy", train_arrays_a["embedding"])
        np.save(output_dir / "train_embedding_feature_b.npy", train_arrays_b["embedding"])
        np.save(output_dir / "val_embedding_feature_a.npy", val_arrays_a["embedding"])
        np.save(output_dir / "val_embedding_feature_b.npy", val_arrays_b["embedding"])

    best_row = max(metric_rows, key=lambda row: float(row["macro_f1"]))
    summary = {
        "sample_count": int(len(samples)),
        "train_count": int(len(train_samples)),
        "val_count": int(len(val_samples)),
        "label_counts": {
            "all": label_counts(samples),
            "train": label_counts(train_samples),
            "val": label_counts(val_samples),
        },
        "split": split_info,
        "device": str(device),
        "feature_a": {
            "name": spec_a.name,
            "manifest": str(resolve_path(spec_a.manifest, project_root)),
            "feature_key": key_a,
            "description": spec_a.description,
            "mean": mean_a,
            "std": std_a,
        },
        "feature_b": {
            "name": spec_b.name,
            "manifest": str(resolve_path(spec_b.manifest, project_root)),
            "feature_key": key_b,
            "description": spec_b.description,
            "mean": mean_b,
            "std": std_b,
        },
        "model": {
            "encoder": "resnet18",
            "embedding_dim": int(args.embedding_dim),
            "image_size": int(args.image_size),
            "pretrained_resnet18": str(resolve_path(args.pretrained_resnet18, project_root)),
            "epochs": int(args.epochs),
            "fusion_epochs": int(args.fusion_epochs),
            "batch_size": int(args.batch_size),
            "learning_rate": float(args.learning_rate),
            "fusion_learning_rate": float(args.fusion_learning_rate),
            "weight_decay": float(args.weight_decay),
            "dropout": float(args.dropout),
            "balanced_sampler": bool(args.balanced_sampler),
            "class_weighted_loss": bool(args.class_weighted_loss),
        },
        "branch_summaries": {
            "feature_a_resnet18": summary_a,
            "feature_b_resnet18": summary_b,
        },
        "fusion": {
            "probability_ensemble_avg": ensemble_metrics,
            "embedding_concat_classifier": concat_summary["metrics"],
            "embedding_concat_best_epoch": concat_summary["best_epoch"],
        },
        "best_model_by_macro_f1": best_row,
        "outputs": {
            "metrics_summary": str(output_dir / "metrics_summary.csv"),
            "val_predictions": str(output_dir / "val_predictions.csv"),
            "concat_history": str(output_dir / "concat_history.csv"),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
