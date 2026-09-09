"""OpenAPI 스냅샷 → packages/contracts/openapi.json (프론트 `bun run api:types` 의 입력).

    uv run python tools/export_openapi.py            # 기본 경로
    uv run python tools/export_openapi.py --check    # 커밋본과 다르면 종료코드 1 (CI 용)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "packages" / "contracts" / "openapi.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    from wifiguard_api.app import app

    spec = app.openapi()
    text = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"openapi.json 이 최신이 아닙니다: {args.out}", file=sys.stderr)
            return 1
        print("openapi.json up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    paths = len(spec.get("paths", {}))
    schemas = len(spec.get("components", {}).get("schemas", {}))
    print(f"wrote {args.out} ({paths} paths, {schemas} schemas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
