"""Cross-platform live CPU, memory, GPU and storage monitor with Prometheus output."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psutil


def _float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def gpu_samples() -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return []
    command = [
        executable,
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=3, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            continue
        rows.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "utilization_percent": _float(parts[2]),
                "memory_used_mib": _float(parts[3]),
                "memory_total_mib": _float(parts[4]),
                "power_watts": _float(parts[5]),
                "temperature_c": _float(parts[6]),
            }
        )
    return rows


class Monitor:
    def __init__(self, path: Path, interval: float, history_size: int, output: Path | None):
        self.path = path
        self.interval = interval
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self.output = output
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        psutil.cpu_percent(interval=None)

    def start(self) -> None:
        if self.output:
            self.output.parent.mkdir(parents=True, exist_ok=True)
        self.thread.start()

    def _sample(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self.path))
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "epoch_seconds": time.time(),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "logical_cpu_count": psutil.cpu_count(logical=True),
            "memory_used_bytes": memory.used,
            "memory_available_bytes": memory.available,
            "memory_total_bytes": memory.total,
            "memory_percent": memory.percent,
            "disk_used_bytes": disk.used,
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
            "disk_percent": disk.percent,
            "gpus": gpu_samples(),
        }

    def _run(self) -> None:
        while not self.stop_event.is_set():
            row = self._sample()
            with self.lock:
                self.history.append(row)
            if self.output:
                with self.output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.stop_event.wait(self.interval)

    def latest(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.history[-1]) if self.history else self._sample()

    def rows(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.history)

    def prometheus(self) -> str:
        row = self.latest()
        metrics = [
            "# TYPE wifiguard_host_cpu_percent gauge",
            f"wifiguard_host_cpu_percent {row['cpu_percent']}",
            "# TYPE wifiguard_host_memory_used_bytes gauge",
            f"wifiguard_host_memory_used_bytes {row['memory_used_bytes']}",
            "# TYPE wifiguard_host_memory_total_bytes gauge",
            f"wifiguard_host_memory_total_bytes {row['memory_total_bytes']}",
            "# TYPE wifiguard_host_disk_used_bytes gauge",
            f"wifiguard_host_disk_used_bytes {row['disk_used_bytes']}",
            "# TYPE wifiguard_host_disk_total_bytes gauge",
            f"wifiguard_host_disk_total_bytes {row['disk_total_bytes']}",
        ]
        for gpu in row["gpus"]:
            labels = f'gpu="{gpu["index"]}",name="{gpu["name"]}"'
            for key, metric in (
                ("utilization_percent", "wifiguard_gpu_utilization_percent"),
                ("memory_used_mib", "wifiguard_gpu_memory_used_mib"),
                ("memory_total_mib", "wifiguard_gpu_memory_total_mib"),
                ("power_watts", "wifiguard_gpu_power_watts"),
                ("temperature_c", "wifiguard_gpu_temperature_celsius"),
            ):
                if gpu[key] is not None:
                    metrics.append(f"{metric}{{{labels}}} {gpu[key]}")
        return "\n".join(metrics) + "\n"


DASHBOARD = """<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>WIFI-GUARD Resource Monitor</title><style>
body{font-family:Inter,system-ui;background:#07111f;color:#eaf2ff;margin:0;padding:24px}h1{margin:0 0 6px}
.sub{color:#91a4bd;margin-bottom:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.card{background:#101d2e;border:1px solid #20344d;border-radius:14px;padding:16px}.label{color:#8fa6c0;font-size:12px;text-transform:uppercase}.value{font-size:28px;font-weight:700;margin-top:8px}.bar{height:8px;background:#21354e;border-radius:8px;overflow:hidden;margin-top:12px}.fill{height:100%;background:#41d1a6}.warn{background:#ffb454}.bad{background:#ff6b7a}pre{white-space:pre-wrap;color:#b9cae0}.stamp{margin-top:18px;color:#8297af;font-size:13px}</style></head>
<body><h1>WIFI-GUARD 실시간 리소스</h1><div class=\"sub\">관측값 · 2초 자동 갱신 · AWS 추정치와 분리</div><div id=\"grid\" class=\"grid\"></div><div class=\"stamp\" id=\"stamp\"></div>
<script>const gb=x=>x/1073741824,fmt=x=>x.toFixed(1);function card(label,value,pct){const c=pct>90?'bad':pct>75?'warn':'';return `<div class=card><div class=label>${label}</div><div class=value>${value}</div><div class=bar><div class=\"fill ${c}\" style=\"width:${Math.min(100,pct)}%\"></div></div></div>`}async function tick(){const r=await fetch('/api/snapshot');const d=await r.json();let h=card('CPU',fmt(d.cpu_percent)+'%',d.cpu_percent)+card('Memory',fmt(gb(d.memory_used_bytes))+' / '+fmt(gb(d.memory_total_bytes))+' GiB',d.memory_percent)+card('Disk',fmt(gb(d.disk_used_bytes))+' / '+fmt(gb(d.disk_total_bytes))+' GiB',d.disk_percent);for(const g of d.gpus)h+=card('GPU '+g.index+' utilization',fmt(g.utilization_percent||0)+'%',g.utilization_percent||0)+card('GPU '+g.index+' VRAM',fmt((g.memory_used_mib||0)/1024)+' / '+fmt((g.memory_total_mib||0)/1024)+' GiB',100*(g.memory_used_mib||0)/(g.memory_total_mib||1));document.getElementById('grid').innerHTML=h;document.getElementById('stamp').textContent='last sample '+d.timestamp}tick();setInterval(tick,2000)</script></body></html>"""


def handler_factory(monitor: Monitor):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                payload, content_type = DASHBOARD.encode(), "text/html; charset=utf-8"
            elif self.path == "/api/snapshot":
                payload, content_type = json.dumps(monitor.latest()).encode(), "application/json"
            elif self.path == "/api/history":
                payload, content_type = json.dumps(monitor.rows()).encode(), "application/json"
            elif self.path == "/metrics":
                payload, content_type = monitor.prometheus().encode(), "text/plain; version=0.0.4"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9108)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--history-size", type=int, default=900)
    parser.add_argument("--path", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    monitor = Monitor(args.path.resolve(), args.interval, args.history_size, args.output)
    monitor.start()
    server = ThreadingHTTPServer((args.host, args.port), handler_factory(monitor))
    print(f"resource dashboard: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
