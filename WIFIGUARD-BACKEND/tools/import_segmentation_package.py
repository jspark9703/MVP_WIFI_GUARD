"""Safely validate and register the supplied segmentation model package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def safe_extract(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError(f"unsafe archive path: {member.filename}")
        bundle.extractall(destination)
    candidates = [path.parent for path in destination.rglob("package_manifest.json")]
    if len(candidates) != 1:
        raise ValueError(f"expected one package_manifest.json, found {len(candidates)}")
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--package-root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--copy-checkpoint", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict:
    expected = json.loads(
        (args.repo_root / "model-manifests/inhouse-segmentation.json").read_text(encoding="utf-8")
    )
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.archive is not None:
        archive = args.archive.resolve()
        archive_sha256 = digest(archive)
        if archive_sha256 != expected["archive_sha256"]:
            raise ValueError(f"archive SHA-256 mismatch: {archive_sha256}")
        temporary = tempfile.TemporaryDirectory(prefix="wifiguard-segmentation-")
        package_root = safe_extract(archive, Path(temporary.name))
    else:
        archive = None
        archive_sha256 = None
        package_root = args.package_root.resolve()

    manifest = json.loads((package_root / "package_manifest.json").read_text(encoding="utf-8"))
    checkpoint = package_root / manifest["weight"]
    checkpoint_sha256 = digest(checkpoint)
    if checkpoint_sha256 != expected["checkpoint_sha256"]:
        raise ValueError(f"checkpoint SHA-256 mismatch: {checkpoint_sha256}")
    feature_paths = sorted((package_root / "data/features").glob("*.npz"))
    label_paths = sorted((package_root / "data/segmentation_labels").glob("*.npz"))
    if len(feature_paths) != expected["feature_recordings"] or len(label_paths) != len(feature_paths):
        raise ValueError("feature/label recording counts do not match the reviewed manifest")

    destination = args.repo_root / expected["checkpoint_relative_path"]
    if args.copy_checkpoint:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(checkpoint, destination)
        if digest(destination) != checkpoint_sha256:
            raise RuntimeError("checkpoint copy verification failed")
    feature_bytes = sum(path.stat().st_size for path in feature_paths)
    label_bytes = sum(path.stat().st_size for path in label_paths)
    result = {
        "status": "passed",
        "registered_at": datetime.now(UTC).isoformat(),
        "package_root": str(package_root),
        "archive": str(archive) if archive else None,
        "archive_sha256": archive_sha256,
        "checkpoint_sha256": checkpoint_sha256,
        "checkpoint_bytes": checkpoint.stat().st_size,
        "checkpoint_destination": str(destination) if args.copy_checkpoint else None,
        "feature_recordings": len(feature_paths),
        "feature_bytes": feature_bytes,
        "label_recordings": len(label_paths),
        "label_bytes": label_bytes,
        "feature_windows": int(manifest["feature_window_count"]),
        "generalization_validated": False,
        "generalization_note": expected["training_data_disclosure"],
    }
    output = args.output or args.repo_root / "data/inhouse-segmentation/registry.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["registry"] = str(output)
    if temporary is not None:
        # The registry must not point at the temporary extraction as a training source.
        result["package_root"] = None
        result["package_root_note"] = "archive validated; pass --package-root for training"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.cleanup()
    return result


def main() -> None:
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
