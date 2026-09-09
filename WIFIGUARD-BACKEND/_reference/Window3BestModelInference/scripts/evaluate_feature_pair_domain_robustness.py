#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


os.environ.setdefault("MPLCONFIGDIR", "/tmp/falldetection-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/falldetection-cache")
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)

LABELS = [0, 1]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    manifest: Path
    feature_key: str
    description: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate domain-wise robustness and complementarity for an arbitrary "
            "pair of aligned CSI feature branches."
        )
    )
    parser.add_argument(
        "--feature-a",
        required=True,
        help="Branch A spec: name|manifest|feature_key|description. Use source_s3:s3 for source S3 npz.",
    )
    parser.add_argument(
        "--feature-b",
        required=True,
        help="Branch B spec: name|manifest|feature_key|description. Use source_s3:s3 for source S3 npz.",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pca-dim", type=int, default=50)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--slice-keys",
        nargs="+",
        default=["env", "rx", "condition", "activity", "subject", "trial_block", "hard_negative"],
    )
    parser.add_argument(
        "--holdout-domain-keys",
        nargs="+",
        default=["env", "rx", "condition", "subject", "trial_block"],
    )
    parser.add_argument("--min-slice-size", type=int, default=10)
    parser.add_argument("--min-holdout-size", type=int, default=10)
    parser.add_argument(
        "--allow-single-class-holdout",
        action="store_true",
        help="Evaluate holdout values even when their test set has only one binary class.",
    )
    return parser.parse_args()


def parse_spec(text: str) -> FeatureSpec:
    parts = text.split("|", 3)
    if len(parts) != 4:
        raise ValueError(f"feature spec must be name|manifest|feature_key|description, got: {text}")
    return FeatureSpec(name=parts[0], manifest=Path(parts[1]), feature_key=parts[2], description=parts[3])


def resolve_path(path: str | Path, project_root: Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return project_root / path


def read_manifest(path: Path, project_root: Path) -> dict[str, dict[str, str]]:
    real_path = resolve_path(path, project_root)
    with real_path.open("r", newline="", encoding="utf-8") as handle:
        rows = {}
        for row in csv.DictReader(handle):
            status = row.get("status", "")
            if status and status not in {"ok", "done", "skipped_existing"}:
                continue
            rows[row["sample_id"]] = row
    if not rows:
        raise ValueError(f"no valid rows in {real_path}")
    return rows


def label_from_row(row: dict[str, str]) -> int:
    if row.get("binary_label") == "fall":
        return 1
    if row.get("binary_label") == "non_fall":
        return 0
    return int(row.get("binary_label_idx", "0"))


def feature_key(spec: FeatureSpec) -> str:
    if spec.feature_key.startswith("source_s3:"):
        return spec.feature_key.split(":", 1)[1]
    return spec.feature_key


def feature_path(row: dict[str, str], spec: FeatureSpec, project_root: Path) -> Path:
    if spec.feature_key.startswith("source_s3:"):
        return resolve_path(row["source_s3_feature_path"], project_root)
    return resolve_path(row["feature_path"], project_root)


def load_feature_matrix(
    spec: FeatureSpec,
    rows_by_id: dict[str, dict[str, str]],
    sample_ids: list[str],
    project_root: Path,
) -> np.ndarray:
    key = feature_key(spec)
    vectors: list[np.ndarray] = []
    for index, sample_id in enumerate(sample_ids, start=1):
        row = rows_by_id[sample_id]
        path = feature_path(row, spec, project_root)
        with np.load(path) as data:
            if key in data:
                arr = data[key].astype(np.float32)
            elif key == "s3" and "scalogram_s3" in data:
                arr = data["scalogram_s3"].astype(np.float32)
            elif key == "s3" and "scalogram" in data:
                arr = data["scalogram"].astype(np.float32)
            else:
                raise KeyError(f"{key!r} missing from {path}")
        vectors.append(arr.reshape(-1))
        if index == 1 or index % 250 == 0 or index == len(sample_ids):
            print(f"loaded {spec.name}: {index}/{len(sample_ids)}", flush=True)
    return np.stack(vectors, axis=0).astype(np.float32)


def branch_encode(
    x_train: np.ndarray,
    x_test: np.ndarray,
    pca_dim: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    n_components = min(int(pca_dim), x_train.shape[0] - 1, x_train.shape[1])
    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_test_scaled = scaler.transform(x_test)
    pca = PCA(n_components=n_components, svd_solver="randomized", random_state=seed)
    return pca.fit_transform(x_train_scaled).astype(np.float32), pca.transform(x_test_scaled).astype(np.float32)


def make_lr(seed: int) -> Any:
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(max_iter=3000, class_weight="balanced", random_state=seed)


def fit_branch_proba(z_train: np.ndarray, z_test: np.ndarray, y_train: np.ndarray, seed: int) -> np.ndarray:
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(StandardScaler(), make_lr(seed))
    model.fit(z_train, y_train)
    return model.predict_proba(z_test).astype(np.float32)


def fit_pair(
    x_a: np.ndarray,
    x_b: np.ndarray,
    y: np.ndarray,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    pca_dim: int,
    seed: int,
) -> dict[str, np.ndarray]:
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    z_a_train, z_a_test = branch_encode(x_a[train_idx], x_a[test_idx], pca_dim, seed)
    z_b_train, z_b_test = branch_encode(x_b[train_idx], x_b[test_idx], pca_dim, seed)
    proba_a = fit_branch_proba(z_a_train, z_a_test, y[train_idx], seed)
    proba_b = fit_branch_proba(z_b_train, z_b_test, y[train_idx], seed)
    proba_avg = ((proba_a + proba_b) / 2.0).astype(np.float32)
    concat_model = make_pipeline(StandardScaler(), make_lr(seed))
    concat_model.fit(np.concatenate([z_a_train, z_b_train], axis=1), y[train_idx])
    proba_concat = concat_model.predict_proba(np.concatenate([z_a_test, z_b_test], axis=1)).astype(np.float32)
    return {
        "proba_a": proba_a,
        "proba_b": proba_b,
        "proba_avg": proba_avg,
        "proba_concat": proba_concat,
        "pred_a": np.argmax(proba_a, axis=1).astype(np.int64),
        "pred_b": np.argmax(proba_b, axis=1).astype(np.int64),
        "pred_avg": np.argmax(proba_avg, axis=1).astype(np.int64),
        "pred_concat": np.argmax(proba_concat, axis=1).astype(np.int64),
    }


def run_oof(
    x_a: np.ndarray,
    x_b: np.ndarray,
    y: np.ndarray,
    folds: int,
    pca_dim: int,
    seed: int,
) -> dict[str, np.ndarray]:
    from sklearn.model_selection import StratifiedKFold

    outputs: dict[str, np.ndarray] = {
        "fold": np.full(len(y), -1, dtype=np.int64),
        "pred_a": np.full(len(y), -1, dtype=np.int64),
        "pred_b": np.full(len(y), -1, dtype=np.int64),
        "pred_avg": np.full(len(y), -1, dtype=np.int64),
        "pred_concat": np.full(len(y), -1, dtype=np.int64),
        "proba_a": np.zeros((len(y), 2), dtype=np.float32),
        "proba_b": np.zeros((len(y), 2), dtype=np.float32),
        "proba_avg": np.zeros((len(y), 2), dtype=np.float32),
        "proba_concat": np.zeros((len(y), 2), dtype=np.float32),
    }
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for fold_index, (train_idx, test_idx) in enumerate(skf.split(x_a, y), start=1):
        print(f"oof fold {fold_index}/{folds}: train={len(train_idx)} test={len(test_idx)}", flush=True)
        fold_outputs = fit_pair(x_a, x_b, y, train_idx, test_idx, pca_dim, seed)
        outputs["fold"][test_idx] = fold_index
        for key, value in fold_outputs.items():
            outputs[key][test_idx] = value
    return outputs


def metric_record(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

    cm = confusion_matrix(y_true, y_pred, labels=LABELS)
    unique = np.unique(y_true)
    balanced = float(balanced_accuracy_score(y_true, y_pred)) if len(unique) >= 2 else ""
    return {
        "n": int(len(y_true)),
        "nonfall_count": int(np.sum(y_true == 0)),
        "fall_count": int(np.sum(y_true == 1)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": balanced,
        "macro_f1": float(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)),
        "fall_precision": float(precision_score(y_true, y_pred, labels=LABELS, pos_label=1, zero_division=0)),
        "fall_recall": float(recall_score(y_true, y_pred, labels=LABELS, pos_label=1, zero_division=0)),
        "fall_f1": float(f1_score(y_true, y_pred, labels=LABELS, pos_label=1, zero_division=0)),
        "nonfall_precision": float(precision_score(y_true, y_pred, labels=LABELS, pos_label=0, zero_division=0)),
        "nonfall_recall": float(recall_score(y_true, y_pred, labels=LABELS, pos_label=0, zero_division=0)),
        "nonfall_f1": float(f1_score(y_true, y_pred, labels=LABELS, pos_label=0, zero_division=0)),
        "tn_nonfall": int(cm[0, 0]),
        "fp_nonfall_as_fall": int(cm[0, 1]),
        "fn_fall_as_nonfall": int(cm[1, 0]),
        "tp_fall": int(cm[1, 1]),
    }


def overlap_record(y_true: np.ndarray, pred_a: np.ndarray, pred_b: np.ndarray) -> dict[str, Any]:
    a_correct = pred_a == y_true
    b_correct = pred_b == y_true
    best_single = max(float(np.mean(a_correct)), float(np.mean(b_correct)))
    oracle = float(np.mean(a_correct | b_correct))
    return {
        "both_correct": int(np.sum(a_correct & b_correct)),
        "a_only_correct": int(np.sum(a_correct & ~b_correct)),
        "b_only_correct": int(np.sum(b_correct & ~a_correct)),
        "both_wrong": int(np.sum(~a_correct & ~b_correct)),
        "a_error_count": int(np.sum(~a_correct)),
        "b_error_count": int(np.sum(~b_correct)),
        "shared_error_count": int(np.sum(~a_correct & ~b_correct)),
        "disagreement_count": int(np.sum(pred_a != pred_b)),
        "a_correct_on_disagreement": int(np.sum((pred_a != pred_b) & a_correct)),
        "b_correct_on_disagreement": int(np.sum((pred_a != pred_b) & b_correct)),
        "oracle_any_correct_accuracy": oracle,
        "oracle_gain_vs_best_single_accuracy": oracle - best_single,
        "b_recovers_a_error_rate": float(np.sum(b_correct & ~a_correct) / max(np.sum(~a_correct), 1)),
        "a_recovers_b_error_rate": float(np.sum(a_correct & ~b_correct) / max(np.sum(~b_correct), 1)),
    }


def complementarity_rows(base: dict[str, Any], y_true: np.ndarray, outputs: dict[str, np.ndarray]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics = {
        model: metric_record(y_true, outputs[f"pred_{model}"])
        for model in ("a", "b", "avg", "concat")
    }
    overlap = overlap_record(y_true, outputs["pred_a"], outputs["pred_b"])
    best_single_macro = max(metrics["a"]["macro_f1"], metrics["b"]["macro_f1"])
    best_single_acc = max(metrics["a"]["accuracy"], metrics["b"]["accuracy"])
    for model in ("a", "b", "avg", "concat"):
        rows.append(
            {
                **base,
                "model": model,
                **metrics[model],
                "macro_f1_gain_vs_best_single": metrics[model]["macro_f1"] - best_single_macro,
                "accuracy_gain_vs_best_single": metrics[model]["accuracy"] - best_single_acc,
                **overlap,
            }
        )
    rows.append(
        {
            **base,
            "model": "oracle_any_correct",
            "n": int(len(y_true)),
            "nonfall_count": int(np.sum(y_true == 0)),
            "fall_count": int(np.sum(y_true == 1)),
            "accuracy": overlap["oracle_any_correct_accuracy"],
            "balanced_accuracy": "",
            "macro_f1": "",
            "fall_precision": "",
            "fall_recall": "",
            "fall_f1": "",
            "nonfall_precision": "",
            "nonfall_recall": "",
            "nonfall_f1": "",
            "tn_nonfall": "",
            "fp_nonfall_as_fall": "",
            "fn_fall_as_nonfall": "",
            "tp_fall": "",
            "macro_f1_gain_vs_best_single": "",
            "accuracy_gain_vs_best_single": overlap["oracle_gain_vs_best_single_accuracy"],
            **overlap,
        }
    )
    return rows


def make_prediction_subset(outputs: dict[str, np.ndarray], indices: np.ndarray) -> dict[str, np.ndarray]:
    return {key: value[indices] for key, value in outputs.items() if key.startswith("pred_") or key.startswith("proba_") or key == "fold"}


def run_slice_analysis(
    rows: list[dict[str, str]],
    y: np.ndarray,
    outputs: dict[str, np.ndarray],
    slice_keys: list[str],
    min_size: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in slice_keys:
        values = sorted({str(row.get(key, "")) for row in rows})
        for value in values:
            indices = np.asarray([idx for idx, row in enumerate(rows) if str(row.get(key, "")) == value], dtype=np.int64)
            if len(indices) < min_size:
                continue
            records.extend(complementarity_rows({"analysis": "oof_slice", "domain_key": key, "domain_value": value}, y[indices], make_prediction_subset(outputs, indices)))
    return records


def valid_binary_split(y_train: np.ndarray, y_test: np.ndarray, allow_single_class_test: bool) -> bool:
    if len(np.unique(y_train)) < 2:
        return False
    if allow_single_class_test:
        return len(y_test) > 0
    return len(np.unique(y_test)) >= 2


def run_holdout_analysis(
    rows: list[dict[str, str]],
    x_a: np.ndarray,
    x_b: np.ndarray,
    y: np.ndarray,
    domain_keys: list[str],
    min_size: int,
    allow_single_class_test: bool,
    pca_dim: int,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key in domain_keys:
        values = sorted({str(row.get(key, "")) for row in rows})
        for value in values:
            test_idx = np.asarray([idx for idx, row in enumerate(rows) if str(row.get(key, "")) == value], dtype=np.int64)
            test_set = set(test_idx.tolist())
            train_idx = np.asarray([idx for idx in range(len(rows)) if idx not in test_set], dtype=np.int64)
            reason = ""
            if len(test_idx) < min_size:
                reason = f"test_size<{min_size}"
            elif not valid_binary_split(y[train_idx], y[test_idx], allow_single_class_test):
                reason = "requires both classes in train/test"
            if reason:
                skipped.append(
                    {
                        "analysis": "leave_domain_out",
                        "domain_key": key,
                        "domain_value": value,
                        "test_size": int(len(test_idx)),
                        "train_size": int(len(train_idx)),
                        "test_nonfall_count": int(np.sum(y[test_idx] == 0)),
                        "test_fall_count": int(np.sum(y[test_idx] == 1)),
                        "reason": reason,
                    }
                )
                continue
            print(f"holdout {key}={value}: train={len(train_idx)} test={len(test_idx)}", flush=True)
            outputs = fit_pair(x_a, x_b, y, train_idx, test_idx, pca_dim, seed)
            records.extend(complementarity_rows({"analysis": "leave_domain_out", "domain_key": key, "domain_value": value}, y[test_idx], outputs))
    return records, skipped


def summarize_by_key(records: list[dict[str, Any]], analysis: str) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in records:
        if row.get("analysis") != analysis or row.get("model") == "oracle_any_correct":
            continue
        grouped.setdefault((row["domain_key"], row["model"]), []).append(row)
    for (domain_key, model), rows in sorted(grouped.items()):
        for metric in ["accuracy", "balanced_accuracy", "macro_f1", "fall_recall", "fall_f1", "macro_f1_gain_vs_best_single"]:
            values = [float(row[metric]) for row in rows if row.get(metric, "") != ""]
            if not values:
                continue
            arr = np.asarray(values, dtype=np.float64)
            summary.append(
                {
                    "analysis": analysis,
                    "domain_key": domain_key,
                    "model": model,
                    "metric": metric,
                    "mean": float(np.mean(arr)),
                    "std": float(np.std(arr)),
                    "median": float(np.median(arr)),
                    "min": float(np.min(arr)),
                    "max": float(np.max(arr)),
                    "protocol_count": int(len(values)),
                }
            )
    return summary


def fieldnames() -> list[str]:
    return [
        "analysis",
        "domain_key",
        "domain_value",
        "model",
        "n",
        "nonfall_count",
        "fall_count",
        "accuracy",
        "balanced_accuracy",
        "macro_f1",
        "fall_precision",
        "fall_recall",
        "fall_f1",
        "nonfall_precision",
        "nonfall_recall",
        "nonfall_f1",
        "tn_nonfall",
        "fp_nonfall_as_fall",
        "fn_fall_as_nonfall",
        "tp_fall",
        "macro_f1_gain_vs_best_single",
        "accuracy_gain_vs_best_single",
        "both_correct",
        "a_only_correct",
        "b_only_correct",
        "both_wrong",
        "a_error_count",
        "b_error_count",
        "shared_error_count",
        "disagreement_count",
        "a_correct_on_disagreement",
        "b_correct_on_disagreement",
        "oracle_any_correct_accuracy",
        "oracle_gain_vs_best_single_accuracy",
        "b_recovers_a_error_rate",
        "a_recovers_b_error_rate",
    ]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def save_oof_predictions(path: Path, rows: list[dict[str, str]], y: np.ndarray, outputs: dict[str, np.ndarray]) -> None:
    fields = [
        "sample_id",
        "binary_label",
        "y_true",
        "fold",
        "pred_a",
        "pred_b",
        "pred_avg",
        "pred_concat",
        "proba_a_fall",
        "proba_b_fall",
        "proba_avg_fall",
        "proba_concat_fall",
        "env",
        "rx",
        "condition",
        "activity",
        "subject",
        "trial_block",
        "hard_negative",
    ]
    out_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        out_rows.append(
            {
                "sample_id": row["sample_id"],
                "binary_label": row["binary_label"],
                "y_true": int(y[idx]),
                "fold": int(outputs["fold"][idx]),
                "pred_a": int(outputs["pred_a"][idx]),
                "pred_b": int(outputs["pred_b"][idx]),
                "pred_avg": int(outputs["pred_avg"][idx]),
                "pred_concat": int(outputs["pred_concat"][idx]),
                "proba_a_fall": float(outputs["proba_a"][idx, 1]),
                "proba_b_fall": float(outputs["proba_b"][idx, 1]),
                "proba_avg_fall": float(outputs["proba_avg"][idx, 1]),
                "proba_concat_fall": float(outputs["proba_concat"][idx, 1]),
                "env": row.get("env", ""),
                "rx": row.get("rx", ""),
                "condition": row.get("condition", ""),
                "activity": row.get("activity", ""),
                "subject": row.get("subject", ""),
                "trial_block": row.get("trial_block", ""),
                "hard_negative": row.get("hard_negative", ""),
            }
        )
    write_csv(path, out_rows, fields)


def main() -> None:
    args = parse_args()
    project_root = Path.cwd()
    output_root = resolve_path(args.output_root, project_root)
    output_root.mkdir(parents=True, exist_ok=True)
    spec_a = parse_spec(args.feature_a)
    spec_b = parse_spec(args.feature_b)

    rows_a = read_manifest(spec_a.manifest, project_root)
    rows_b = read_manifest(spec_b.manifest, project_root)
    sample_ids = sorted(set(rows_a) & set(rows_b))
    if not sample_ids:
        raise ValueError("feature manifests have no overlapping sample_id values")
    rows = [rows_a[sample_id] for sample_id in sample_ids]
    labels = np.asarray([label_from_row(row) for row in rows], dtype=np.int64)
    labels_b = np.asarray([label_from_row(rows_b[sample_id]) for sample_id in sample_ids], dtype=np.int64)
    if not np.array_equal(labels, labels_b):
        raise ValueError("feature manifests disagree on labels for overlapping sample IDs")

    print(f"sample_count={len(sample_ids)} feature_a={spec_a.name} feature_b={spec_b.name}", flush=True)
    x_a = load_feature_matrix(spec_a, rows_a, sample_ids, project_root)
    x_b = load_feature_matrix(spec_b, rows_b, sample_ids, project_root)
    outputs = run_oof(x_a, x_b, labels, args.folds, args.pca_dim, args.seed)

    overall_rows = complementarity_rows({"analysis": "overall_oof", "domain_key": "all", "domain_value": "all"}, labels, outputs)
    slice_rows = run_slice_analysis(rows, labels, outputs, args.slice_keys, args.min_slice_size)
    holdout_rows, skipped_rows = run_holdout_analysis(
        rows,
        x_a,
        x_b,
        labels,
        args.holdout_domain_keys,
        args.min_holdout_size,
        args.allow_single_class_holdout,
        args.pca_dim,
        args.seed,
    )
    summary_rows = summarize_by_key(slice_rows, "oof_slice") + summarize_by_key(holdout_rows, "leave_domain_out")

    fields = fieldnames()
    write_csv(output_root / "overall_oof_metrics.csv", overall_rows, fields)
    write_csv(output_root / "oof_slice_metrics.csv", slice_rows, fields)
    write_csv(output_root / "leave_domain_out_metrics.csv", holdout_rows, fields)
    write_csv(output_root / "summary_by_domain_key.csv", summary_rows)
    write_csv(output_root / "skipped_holdouts.csv", skipped_rows)
    save_oof_predictions(output_root / "out_of_fold_predictions.csv", rows, labels, outputs)

    model_lookup = {row["model"]: row for row in overall_rows}
    summary = {
        "sample_count": len(sample_ids),
        "feature_a": {
            "name": spec_a.name,
            "manifest": str(resolve_path(spec_a.manifest, project_root)),
            "feature_key": spec_a.feature_key,
            "description": spec_a.description,
            "shape": list(x_a.shape),
        },
        "feature_b": {
            "name": spec_b.name,
            "manifest": str(resolve_path(spec_b.manifest, project_root)),
            "feature_key": spec_b.feature_key,
            "description": spec_b.description,
            "shape": list(x_b.shape),
        },
        "pca_dim": int(args.pca_dim),
        "folds": int(args.folds),
        "overall": {
            model: {
                "macro_f1": model_lookup[model].get("macro_f1", ""),
                "accuracy": model_lookup[model].get("accuracy", ""),
                "macro_f1_gain_vs_best_single": model_lookup[model].get("macro_f1_gain_vs_best_single", ""),
            }
            for model in ("a", "b", "avg", "concat", "oracle_any_correct")
        },
        "outputs": {
            "overall_oof_metrics": str(output_root / "overall_oof_metrics.csv"),
            "oof_slice_metrics": str(output_root / "oof_slice_metrics.csv"),
            "leave_domain_out_metrics": str(output_root / "leave_domain_out_metrics.csv"),
            "summary_by_domain_key": str(output_root / "summary_by_domain_key.csv"),
            "out_of_fold_predictions": str(output_root / "out_of_fold_predictions.csv"),
            "skipped_holdouts": str(output_root / "skipped_holdouts.csv"),
        },
    }
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
