from __future__ import annotations

import argparse
import logging
import time

from .settings import InferenceSettings
from .worker import InferenceWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="WIFI-GUARD live Kafka inference worker")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate environment and checkpoint, load/warm the model, then exit",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = InferenceSettings.from_env()
    worker = InferenceWorker(settings)
    if args.check:
        worker.prepare()
        print(f"MODEL_READY version={worker.engine.model_version}")
        return
    worker.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
