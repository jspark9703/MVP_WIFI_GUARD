#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader, Dataset


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from train_losnlos_resnet18_s3_acf_dual_end2end import DualBranchResNet  # noqa: E402


class ValidationDataset(Dataset[tuple[torch.Tensor, torch.Tensor, torch.Tensor, str]]):
    def __init__(self, rows: list[dict[str, str]], normalization: dict[str, dict[str, float]]) -> None:
        self.rows = rows
        self.normalization = normalization
        self.cache: dict[Path, tuple[np.ndarray, np.ndarray]] = {}

    def __len__(self) -> int:
        return len(self.rows)

    def load_archive(self, path: Path) -> tuple[np.ndarray, np.ndarray]:
        if path not in self.cache:
            with np.load(path, allow_pickle=False) as data:
                s3 = data["s3"].astype(np.float16, copy=True)
                acf = data["pca_acf_lag0p4s"].astype(np.float16, copy=True)
            if s3.ndim != 3 or s3.shape[1:] != (224, 224):
                raise ValueError(f"Unexpected S3 shape {s3.shape}: {path}")
            if acf.ndim != 4 or acf.shape[1:] != (1, 128, 64):
                raise ValueError(f"Unexpected ACF shape {acf.shape}: {path}")
            if len(s3) != len(acf):
                raise ValueError(f"Archive row mismatch: {path}")
            self.cache[path] = (s3, acf)
        return self.cache[path]

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, str]:
        row = self.rows[index]
        path = ROOT / row["feature_path"]
        s3_archive, acf_archive = self.load_archive(path)
        local_index = int(row["feature_local_index"])
        s3 = s3_archive[local_index].astype(np.float32)[None, :, :]
        acf = acf_archive[local_index].astype(np.float32)
        s3_norm = self.normalization["feature_a"]
        acf_norm = self.normalization["feature_b"]
        s3 = (s3 - float(s3_norm["mean"])) / max(float(s3_norm["std"]), 1e-6)
        acf = (acf - float(acf_norm["mean"])) / max(float(acf_norm["std"]), 1e-6)
        return (
            torch.from_numpy(s3),
            torch.from_numpy(acf),
            torch.tensor(int(row["true_label"]), dtype=torch.long),
            row["sample_id"],
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "dataset/validation_manifest.csv")
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "weights/best_model.pt")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.468)
    parser.add_argument("--mode-size", type=int, default=5)
    return parser.parse_args()


def select_device(requested: str) -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        return torch.device("cuda")
    if requested == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but unavailable")
        return torch.device("mps")
    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def moving_mode(values: list[int], size: int) -> list[int]:
    if size < 3:
        raise ValueError("mode size must be at least 3")
    original = list(values)
    filtered = list(values)
    if len(values) < size:
        return filtered
    if size % 2:
        left = right = size // 2
    else:
        left, right = size // 2 - 1, size // 2
    for index in range(left, len(values) - right):
        window = original[index - left : index + right + 1]
        ones = sum(window)
        zeros = len(window) - ones
        filtered[index] = 1 if ones > zeros else 0 if zeros > ones else original[index]
    return filtered


def apply_recording_mode(rows: list[dict[str, str]], prediction: np.ndarray, size: int) -> np.ndarray:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[row["recording_id"]].append(index)
    output = prediction.copy()
    for indices in groups.values():
        indices.sort(key=lambda index: int(rows[index]["window_index"]))
        filtered = moving_mode([int(prediction[index]) for index in indices], size)
        for index, value in zip(indices, filtered, strict=True):
            output[index] = value
    return output


def safe_div(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def metrics(y_true: np.ndarray, prediction: np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    tn = int(np.sum((y_true == 0) & (prediction == 0)))
    fp = int(np.sum((y_true == 0) & (prediction == 1)))
    fn = int(np.sum((y_true == 1) & (prediction == 0)))
    tp = int(np.sum((y_true == 1) & (prediction == 1)))
    fall_precision = safe_div(tp, tp + fp)
    fall_recall = safe_div(tp, tp + fn)
    fall_f1 = safe_div(2 * fall_precision * fall_recall, fall_precision + fall_recall)
    nonfall_precision = safe_div(tn, tn + fn)
    nonfall_recall = safe_div(tn, tn + fp)
    nonfall_f1 = safe_div(2 * nonfall_precision * nonfall_recall, nonfall_precision + nonfall_recall)
    return {
        "n": int(len(y_true)),
        "accuracy": float(np.mean(y_true == prediction)),
        "balanced_accuracy": (fall_recall + nonfall_recall) / 2,
        "macro_f1": (fall_f1 + nonfall_f1) / 2,
        "fall_precision": fall_precision,
        "fall_recall": fall_recall,
        "fall_f1": fall_f1,
        "nonfall_precision": nonfall_precision,
        "nonfall_recall": nonfall_recall,
        "nonfall_f1": nonfall_f1,
        "auroc": float(roc_auc_score(y_true, probabilities)),
        "tn_nonfall": tn,
        "fp_nonfall_as_fall": fp,
        "fn_fall_as_nonfall": fn,
        "tp_fall": tp,
    }


@torch.no_grad()
def infer(
    model: DualBranchResNet,
    loader: DataLoader,
    device: torch.device,
    image_size: int,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    model.eval()
    sample_ids: list[str] = []
    labels: list[np.ndarray] = []
    probabilities: list[np.ndarray] = []
    for s3, acf, target, batch_ids in loader:
        output = model(s3.to(device), acf.to(device), image_size=image_size)
        proba = torch.softmax(output["logits"], dim=1)[:, 1]
        sample_ids.extend(str(value) for value in batch_ids)
        labels.append(target.numpy())
        probabilities.append(proba.cpu().numpy().astype(np.float32))
    return sample_ids, np.concatenate(labels), np.concatenate(probabilities)


def main() -> None:
    args = parse_args()
    rows = read_csv(args.manifest)
    rows.sort(key=lambda row: (row["recording_id"], int(row["window_index"])))
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    config = checkpoint["model_config"]
    model = DualBranchResNet(
        backbone=str(config["backbone"]),
        embedding_dim=int(config["embedding_dim"]),
        hidden_dim=int(config["fusion_hidden_dim"]),
        dropout=float(config["dropout"]),
    )
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    device = select_device(args.device)
    model.to(device)
    dataset = ValidationDataset(rows, checkpoint["normalization"])
    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=device.type == "cuda",
    )
    sample_ids, y_true, probabilities = infer(model, loader, device, int(config["image_size"]))
    row_by_id = {row["sample_id"]: row for row in rows}
    ordered_rows = [row_by_id[sample_id] for sample_id in sample_ids]
    pred_0p5 = (probabilities >= 0.5).astype(np.int64)
    pred_selected = (probabilities >= args.threshold).astype(np.int64)
    pred_mode = apply_recording_mode(ordered_rows, pred_selected, args.mode_size)
    prediction_rows: list[dict[str, Any]] = []
    for index, row in enumerate(ordered_rows):
        prediction_rows.append(
            {
                "sample_id": row["sample_id"],
                "recording_id": row["recording_id"],
                "window_index": int(row["window_index"]),
                "window_start_sec": float(row["window_start_sec"]),
                "window_end_sec": float(row["window_end_sec"]),
                "true_label": int(y_true[index]),
                "proba_fall": float(probabilities[index]),
                "pred_threshold_0p5": int(pred_0p5[index]),
                "pred_threshold_selected": int(pred_selected[index]),
                "pred_threshold_selected_mode5": int(pred_mode[index]),
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "validation_predictions.csv", prediction_rows)
    summary = {
        "status": "complete",
        "device": str(device),
        "checkpoint": str(args.checkpoint),
        "checkpoint_epoch": int(checkpoint["epoch"]),
        "window_seconds": 3.0,
        "stride_seconds": 0.25,
        "recording_count": len({row["recording_id"] for row in ordered_rows}),
        "window_count": len(ordered_rows),
        "selected_threshold": float(args.threshold),
        "mode_size": int(args.mode_size),
        "threshold_0p5": metrics(y_true, pred_0p5, probabilities),
        "threshold_selected": metrics(y_true, pred_selected, probabilities),
        "threshold_selected_mode5": metrics(y_true, pred_mode, probabilities),
        "predictions": str(args.output_dir / "validation_predictions.csv"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

