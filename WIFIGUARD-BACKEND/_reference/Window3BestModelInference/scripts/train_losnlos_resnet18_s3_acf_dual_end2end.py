#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from evaluate_feature_pair_domain_robustness import parse_spec, resolve_path
from train_losnlos_resnet18_s3_acf_fusion import (
    BasicBlock,
    FusionSample,
    build_samples,
    class_weights,
    compute_scalar_stats,
    feature_key,
    label_counts,
    load_npz_feature,
    metrics_from_predictions,
    select_device,
    set_seed,
    split_samples,
    write_csv,
)


RESNET_BLOCKS = {
    "resnet18": (2, 2, 2, 2),
    "resnet34": (3, 4, 6, 3),
}


class DualFeatureImageDataset(Dataset[tuple[torch.Tensor, torch.Tensor, torch.Tensor, str]]):
    def __init__(
        self,
        samples: list[FusionSample],
        key_a: str,
        key_b: str,
        mean_a: float,
        std_a: float,
        mean_b: float,
        std_b: float,
    ) -> None:
        self.samples = samples
        self.key_a = key_a
        self.key_b = key_b
        self.mean_a = float(mean_a)
        self.std_a = max(float(std_a), 1e-6)
        self.mean_b = float(mean_b)
        self.std_b = max(float(std_b), 1e-6)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, str]:
        sample = self.samples[index]
        arr_a = (load_npz_feature(sample.feature_path_a, self.key_a) - self.mean_a) / self.std_a
        arr_b = (load_npz_feature(sample.feature_path_b, self.key_b) - self.mean_b) / self.std_b
        return (
            torch.from_numpy(arr_a).float(),
            torch.from_numpy(arr_b).float(),
            torch.tensor(sample.label, dtype=torch.long),
            sample.sample_id,
        )


class HuberizedCrossEntropyLoss(nn.Module):
    """Huberize per-sample cross entropy while keeping standard class labels."""

    def __init__(self, delta: float, weight: torch.Tensor | None = None) -> None:
        super().__init__()
        self.delta = float(delta)
        self.register_buffer("weight", weight.detach().clone() if weight is not None else None)

    def forward(self, logits: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        ce = torch.nn.functional.cross_entropy(logits, labels, weight=self.weight, reduction="none")
        delta = max(self.delta, 1e-6)
        return torch.where(ce <= delta, 0.5 * ce.square() / delta, ce - 0.5 * delta).mean()


def make_dual_loader(
    dataset: DualFeatureImageDataset,
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


def prepare_resnet_image(x: torch.Tensor, image_size: int) -> torch.Tensor:
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
    return x


class ResNetImageEncoder(nn.Module):
    def __init__(self, backbone: str, embedding_dim: int = 512, dropout: float = 0.2) -> None:
        super().__init__()
        if backbone not in RESNET_BLOCKS:
            raise ValueError(f"unsupported backbone={backbone!r}")
        self.backbone = backbone
        self.inplanes = 64
        blocks = RESNET_BLOCKS[backbone]
        self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(64, blocks[0])
        self.layer2 = self._make_layer(128, blocks[1], stride=2)
        self.layer3 = self._make_layer(256, blocks[2], stride=2)
        self.layer4 = self._make_layer(512, blocks[3], stride=2)
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


class DualBranchResNet(nn.Module):
    def __init__(self, backbone: str, embedding_dim: int, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        self.encoder_a = ResNetImageEncoder(backbone=backbone, embedding_dim=embedding_dim, dropout=dropout)
        self.encoder_b = ResNetImageEncoder(backbone=backbone, embedding_dim=embedding_dim, dropout=dropout)
        concat_dim = int(embedding_dim) * 2
        self.classifier = nn.Sequential(
            nn.LayerNorm(concat_dim),
            nn.Linear(concat_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 2),
        )

    def forward(self, x_a: torch.Tensor, x_b: torch.Tensor, image_size: int) -> dict[str, torch.Tensor]:
        z_a = self.encoder_a(prepare_resnet_image(x_a, image_size))
        z_b = self.encoder_b(prepare_resnet_image(x_b, image_size))
        embedding = torch.cat([z_a, z_b], dim=1)
        return {
            "embedding_a": z_a,
            "embedding_b": z_b,
            "embedding": embedding,
            "logits": self.classifier(embedding),
        }


def load_encoder_pretrained(encoder: nn.Module, path: Path, project_root: Path) -> dict[str, Any]:
    real_path = resolve_path(path, project_root)
    state = torch.load(real_path, map_location="cpu")
    encoder_state = encoder.state_dict()
    filtered = {
        key: value
        for key, value in state.items()
        if key in encoder_state and tuple(value.shape) == tuple(encoder_state[key].shape)
    }
    missing, unexpected = encoder.load_state_dict(filtered, strict=False)
    return {
        "path": str(real_path),
        "loaded_key_count": len(filtered),
        "missing_key_count": len(missing),
        "unexpected_key_count": len(unexpected),
        "missing_keys_preview": list(missing)[:10],
        "unexpected_keys_preview": list(unexpected)[:10],
    }


def load_encoder_checkpoint(encoder: nn.Module, path: Path, project_root: Path) -> dict[str, Any]:
    real_path = resolve_path(path, project_root)
    checkpoint = torch.load(real_path, map_location="cpu", weights_only=False)
    state = checkpoint.get("model_state_dict", checkpoint)
    encoder_state = encoder.state_dict()
    filtered: dict[str, torch.Tensor] = {}
    for key, value in state.items():
        encoder_key = key.split("encoder.", 1)[1] if key.startswith("encoder.") else key
        if encoder_key in encoder_state and tuple(value.shape) == tuple(encoder_state[encoder_key].shape):
            filtered[encoder_key] = value
    missing, unexpected = encoder.load_state_dict(filtered, strict=False)
    return {
        "path": str(real_path),
        "loaded_key_count": len(filtered),
        "missing_key_count": len(missing),
        "unexpected_key_count": len(unexpected),
        "source_epoch": checkpoint.get("epoch", ""),
        "source_val_metrics": checkpoint.get("val_metrics", {}),
        "missing_keys_preview": list(missing)[:10],
        "unexpected_keys_preview": list(unexpected)[:10],
    }


def make_criterion(loss_kind: str, huber_delta: float, weights: torch.Tensor | None) -> nn.Module:
    if loss_kind == "ce":
        return nn.CrossEntropyLoss(weight=weights)
    if loss_kind == "huber_ce":
        return HuberizedCrossEntropyLoss(delta=float(huber_delta), weight=weights)
    raise ValueError(f"unsupported loss_kind={loss_kind!r}")


def grad_norm(parameters: list[torch.nn.Parameter], device: torch.device) -> torch.Tensor:
    grads = [param.grad.detach().norm(p=2).to(device) for param in parameters if param.grad is not None]
    if not grads:
        return torch.tensor(0.0, device=device)
    return torch.norm(torch.stack(grads), p=2)


def sam_first_step(
    parameters: list[torch.nn.Parameter],
    rho: float,
    device: torch.device,
) -> list[tuple[torch.nn.Parameter, torch.Tensor]]:
    norm = grad_norm(parameters, device)
    scale = float(rho) / (norm + 1e-12)
    perturbations: list[tuple[torch.nn.Parameter, torch.Tensor]] = []
    with torch.no_grad():
        for param in parameters:
            if param.grad is None:
                continue
            e_w = param.grad * scale.to(param)
            param.add_(e_w)
            perturbations.append((param, e_w))
    return perturbations


def sam_second_step(perturbations: list[tuple[torch.nn.Parameter, torch.Tensor]]) -> None:
    with torch.no_grad():
        for param, e_w in perturbations:
            param.sub_(e_w)


def train_one_epoch(
    model: DualBranchResNet,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: torch.device,
    image_size: int,
    grad_clip: float,
    sam_rho: float,
) -> dict[str, float]:
    model.train()
    parameters = [param for param in model.parameters() if param.requires_grad]
    total_loss = 0.0
    total = 0
    correct = 0
    for images_a, images_b, labels, _sample_ids in loader:
        images_a = images_a.to(device)
        images_b = images_b.to(device)
        labels = labels.to(device)
        optimizer.zero_grad(set_to_none=True)
        logits = model(images_a, images_b, image_size=image_size)["logits"]
        loss = criterion(logits, labels)
        loss.backward()
        if sam_rho > 0:
            perturbations = sam_first_step(parameters, float(sam_rho), device)
            optimizer.zero_grad(set_to_none=True)
            second_logits = model(images_a, images_b, image_size=image_size)["logits"]
            second_loss = criterion(second_logits, labels)
            second_loss.backward()
            sam_second_step(perturbations)
        if grad_clip > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=grad_clip)
        optimizer.step()
        total_loss += float(loss.item()) * int(labels.numel())
        total += int(labels.numel())
        correct += int((logits.argmax(dim=1) == labels).sum().item())
    return {"loss": total_loss / max(total, 1), "accuracy": correct / max(total, 1)}


@torch.no_grad()
def evaluate_model(
    model: DualBranchResNet,
    loader: DataLoader,
    device: torch.device,
    image_size: int,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    model.eval()
    sample_ids: list[str] = []
    y_true: list[int] = []
    probas: list[np.ndarray] = []
    embeddings: list[np.ndarray] = []
    for images_a, images_b, labels, batch_sample_ids in loader:
        images_a = images_a.to(device)
        images_b = images_b.to(device)
        outputs = model(images_a, images_b, image_size=image_size)
        proba = torch.softmax(outputs["logits"], dim=1)
        sample_ids.extend(str(value) for value in batch_sample_ids)
        y_true.extend(int(value) for value in labels.numpy().tolist())
        probas.append(proba.cpu().numpy().astype(np.float32))
        embeddings.append(outputs["embedding"].cpu().numpy().astype(np.float32))
    proba_arr = np.concatenate(probas, axis=0) if probas else np.empty((0, 2), dtype=np.float32)
    y_true_arr = np.asarray(y_true, dtype=np.int64)
    y_pred_arr = np.argmax(proba_arr, axis=1).astype(np.int64)
    arrays = {
        "sample_ids": np.asarray(sample_ids),
        "y_true": y_true_arr,
        "y_pred": y_pred_arr,
        "proba": proba_arr,
        "embedding": np.concatenate(embeddings, axis=0) if embeddings else np.empty((0, 0), dtype=np.float32),
    }
    return metrics_from_predictions(y_true_arr, y_pred_arr, proba_arr[:, 1]), arrays


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train S3 and ACF ResNet branches end-to-end with a shared concat classifier."
    )
    parser.add_argument("--feature-a", required=True)
    parser.add_argument("--feature-b", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backbone", choices=tuple(RESNET_BLOCKS), default="resnet18")
    parser.add_argument(
        "--pretrained-resnet18",
        type=Path,
        default=Path("artifacts/pretrained/resnet18_imagenet1k_v1_f37072fd.pth"),
    )
    parser.add_argument(
        "--pretrained-resnet34",
        type=Path,
        default=Path("artifacts/pretrained/resnet34_imagenet1k_v1_b627a593.pth"),
    )
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--embedding-dim", type=int, default=512)
    parser.add_argument("--fusion-hidden-dim", type=int, default=512)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--head-learning-rate", type=float, default=None)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--loss-kind", choices=("ce", "huber_ce"), default="ce")
    parser.add_argument("--huber-delta", type=float, default=1.0)
    parser.add_argument("--sam-rho", type=float, default=0.0)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--grad-clip", type=float, default=1.0)
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
    parser.add_argument("--init-feature-a-checkpoint", type=Path, default=None)
    parser.add_argument("--init-feature-b-checkpoint", type=Path, default=None)
    parser.add_argument(
        "--resume-model-checkpoint",
        type=Path,
        default=None,
        help="Optional full dual-branch checkpoint to continue from. Optimizer state is re-created.",
    )
    return parser.parse_args()


def pretrained_path_for_args(args: argparse.Namespace) -> Path:
    if args.backbone == "resnet18":
        return args.pretrained_resnet18
    if args.backbone == "resnet34":
        return args.pretrained_resnet34
    raise ValueError(f"unsupported backbone={args.backbone!r}")


def save_predictions(path: Path, arrays: dict[str, np.ndarray], samples: list[FusionSample]) -> None:
    sample_by_id = {sample.sample_id: sample for sample in samples}
    rows: list[dict[str, Any]] = []
    for index, sample_id in enumerate(arrays["sample_ids"]):
        sample = sample_by_id[str(sample_id)]
        rows.append(
            {
                "sample_id": str(sample_id),
                "true_label": int(arrays["y_true"][index]),
                "pred_label": int(arrays["y_pred"][index]),
                "proba_nonfall": float(arrays["proba"][index, 0]),
                "proba_fall": float(arrays["proba"][index, 1]),
                "binary_label": sample.binary_label,
                "task_id": sample.task_id,
                "split_group": sample.split_group,
                "env": sample.env,
                "subject": sample.subject,
                "rx": sample.rx,
                "condition": sample.condition,
                "activity": sample.activity,
                "trial_block": sample.trial_block,
            }
        )
    write_csv(path, rows)


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
    train_dataset = DualFeatureImageDataset(train_samples, key_a, key_b, mean_a, std_a, mean_b, std_b)
    val_dataset = DualFeatureImageDataset(val_samples, key_a, key_b, mean_a, std_a, mean_b, std_b)
    train_loader = make_dual_loader(
        train_dataset,
        train_samples,
        int(args.batch_size),
        shuffle=True,
        balanced_sampler=bool(args.balanced_sampler),
        num_workers=int(args.num_workers),
    )
    train_eval_loader = make_dual_loader(
        train_dataset,
        train_samples,
        int(args.batch_size),
        shuffle=False,
        balanced_sampler=False,
        num_workers=int(args.num_workers),
    )
    val_loader = make_dual_loader(
        val_dataset,
        val_samples,
        int(args.batch_size),
        shuffle=False,
        balanced_sampler=False,
        num_workers=int(args.num_workers),
    )

    device = select_device(args.device)
    model = DualBranchResNet(
        backbone=str(args.backbone),
        embedding_dim=int(args.embedding_dim),
        hidden_dim=int(args.fusion_hidden_dim),
        dropout=float(args.dropout),
    )
    pretrained_path = pretrained_path_for_args(args)
    preload_a = load_encoder_pretrained(model.encoder_a, pretrained_path, project_root)
    preload_b = load_encoder_pretrained(model.encoder_b, pretrained_path, project_root)
    checkpoint_init_a = None
    checkpoint_init_b = None
    if args.init_feature_a_checkpoint is not None:
        checkpoint_init_a = load_encoder_checkpoint(model.encoder_a, args.init_feature_a_checkpoint, project_root)
    if args.init_feature_b_checkpoint is not None:
        checkpoint_init_b = load_encoder_checkpoint(model.encoder_b, args.init_feature_b_checkpoint, project_root)
    resume_info = None
    resume_start_epoch = 1
    resume_best_metric = -1.0
    resume_best_epoch = -1
    if args.resume_model_checkpoint is not None:
        resume_path = resolve_path(args.resume_model_checkpoint, project_root)
        resume_checkpoint = torch.load(resume_path, map_location="cpu", weights_only=False)
        model.load_state_dict(resume_checkpoint["model_state_dict"])
        resume_start_epoch = int(resume_checkpoint.get("epoch", 0)) + 1
        resume_metrics = resume_checkpoint.get("val_metrics", {}) or {}
        resume_best_metric = float(resume_metrics.get("macro_f1", -1.0))
        resume_best_epoch = int(resume_checkpoint.get("epoch", -1))
        resume_info = {
            "path": str(resume_path),
            "source_epoch": int(resume_checkpoint.get("epoch", 0)),
            "source_val_metrics": resume_metrics,
            "optimizer_state_restored": False,
        }
    model = model.to(device)
    criterion = make_criterion(
        str(args.loss_kind),
        float(args.huber_delta),
        class_weights(train_samples, device) if args.class_weighted_loss else None,
    )
    if args.head_learning_rate is None:
        optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.learning_rate), weight_decay=float(args.weight_decay))
    else:
        optimizer = torch.optim.AdamW(
            [
                {
                    "params": list(model.encoder_a.parameters()) + list(model.encoder_b.parameters()),
                    "lr": float(args.learning_rate),
                },
                {"params": model.classifier.parameters(), "lr": float(args.head_learning_rate)},
            ],
            weight_decay=float(args.weight_decay),
        )

    print(
        f"training dual end-to-end backbone={args.backbone} samples={len(samples)} train={len(train_samples)} val={len(val_samples)} "
        f"device={device} feature_a={spec_a.name}:{key_a} feature_b={spec_b.name}:{key_b} epochs={args.epochs}",
        flush=True,
    )
    best_metric = resume_best_metric
    best_epoch = resume_best_epoch
    history: list[dict[str, Any]] = read_history(output_dir / "history.csv") if args.resume_model_checkpoint is not None else []
    start_time = time.time()
    best_model_path = output_dir / "best_model.pt"
    if args.resume_model_checkpoint is not None and resume_start_epoch > int(args.epochs):
        print(
            f"resume checkpoint epoch={resume_start_epoch - 1} already reached requested epochs={args.epochs}",
            flush=True,
        )
    for epoch in range(resume_start_epoch, int(args.epochs) + 1):
        train_metrics = train_one_epoch(
            model,
            train_loader,
            optimizer,
            criterion,
            device,
            int(args.image_size),
            float(args.grad_clip),
            float(args.sam_rho),
        )
        val_metrics, _ = evaluate_model(model, val_loader, device, int(args.image_size))
        record = {
            "epoch": epoch,
            "train_loss": train_metrics["loss"],
            "train_accuracy": train_metrics["accuracy"],
            **{f"val_{key}": value for key, value in val_metrics.items()},
        }
        history.append(record)
        write_csv(output_dir / "history.csv", history)
        print(
            f"epoch {epoch}/{args.epochs} train_loss={train_metrics['loss']:.4f} "
            f"train_acc={train_metrics['accuracy']:.4f} val_macro_f1={val_metrics['macro_f1']:.4f} "
            f"val_acc={val_metrics['accuracy']:.4f} val_auroc={val_metrics['auroc']:.4f}",
            flush=True,
        )
        if float(val_metrics["macro_f1"]) > best_metric:
            best_metric = float(val_metrics["macro_f1"])
            best_epoch = epoch
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "epoch": epoch,
                    "val_metrics": val_metrics,
                    "args": vars(args),
                    "feature_a": {"name": spec_a.name, "manifest": str(spec_a.manifest), "feature_key": key_a},
                    "feature_b": {"name": spec_b.name, "manifest": str(spec_b.manifest), "feature_key": key_b},
                    "normalization": {
                        "feature_a": {"mean": mean_a, "std": std_a},
                        "feature_b": {"mean": mean_b, "std": std_b},
                    },
                },
                best_model_path,
            )
            print(f"  saved new best macro_f1={best_metric:.4f} at epoch={epoch}", flush=True)

    checkpoint = torch.load(best_model_path, map_location=device, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    train_best_metrics, train_arrays = evaluate_model(model, train_eval_loader, device, int(args.image_size))
    val_best_metrics, val_arrays = evaluate_model(model, val_loader, device, int(args.image_size))
    save_predictions(output_dir / "val_predictions.csv", val_arrays, val_samples)
    write_csv(output_dir / "metrics_summary.csv", [{"model": "dual_branch_end_to_end", **val_best_metrics}])
    if args.save_embeddings:
        np.savez_compressed(
            output_dir / "embeddings_best_model.npz",
            train_sample_ids=train_arrays["sample_ids"],
            train_embeddings=train_arrays["embedding"],
            train_y=train_arrays["y_true"],
            val_sample_ids=val_arrays["sample_ids"],
            val_embeddings=val_arrays["embedding"],
            val_y=val_arrays["y_true"],
        )

    summary = {
        "sample_count": len(samples),
        "train_count": len(train_samples),
        "val_count": len(val_samples),
        "label_counts": {
            "all": label_counts(samples),
            "train": label_counts(train_samples),
            "val": label_counts(val_samples),
        },
        "split": split_info,
        "feature_a": {
            "name": spec_a.name,
            "manifest": str(spec_a.manifest),
            "feature_key": key_a,
            "description": spec_a.description,
            "mean": mean_a,
            "std": std_a,
        },
        "feature_b": {
            "name": spec_b.name,
            "manifest": str(spec_b.manifest),
            "feature_key": key_b,
            "description": spec_b.description,
            "mean": mean_b,
            "std": std_b,
        },
        "model": {
            "architecture": f"dual_{args.backbone}_end_to_end_concat",
            "backbone": str(args.backbone),
            "embedding_dim": int(args.embedding_dim),
            "fusion_hidden_dim": int(args.fusion_hidden_dim),
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "learning_rate": float(args.learning_rate),
            "head_learning_rate": "" if args.head_learning_rate is None else float(args.head_learning_rate),
            "weight_decay": float(args.weight_decay),
            "loss_kind": str(args.loss_kind),
            "huber_delta": float(args.huber_delta),
            "sam_rho": float(args.sam_rho),
            "dropout": float(args.dropout),
            "grad_clip": float(args.grad_clip),
            "class_weighted_loss": bool(args.class_weighted_loss),
            "balanced_sampler": bool(args.balanced_sampler),
            "pretrained_weights": str(resolve_path(pretrained_path, project_root)),
        },
        "pretrained": {
            "feature_a_encoder": preload_a,
            "feature_b_encoder": preload_b,
        },
        "checkpoint_initialization": {
            "feature_a_encoder": checkpoint_init_a,
            "feature_b_encoder": checkpoint_init_b,
        },
        "resume": resume_info,
        "best_epoch": int(best_epoch),
        "elapsed_sec": round(time.time() - start_time, 3),
        "train_metrics_best_model": train_best_metrics,
        "val_metrics_best_model": val_best_metrics,
        "best_checkpoint_metrics": checkpoint.get("val_metrics", {}),
        "outputs": {
            "best_model": str(best_model_path),
            "history": str(output_dir / "history.csv"),
            "metrics_summary": str(output_dir / "metrics_summary.csv"),
            "val_predictions": str(output_dir / "val_predictions.csv"),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"best epoch={best_epoch} val_macro_f1={val_best_metrics['macro_f1']:.4f} "
        f"val_acc={val_best_metrics['accuracy']:.4f} val_auroc={val_best_metrics['auroc']:.4f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
