"""Launch/stop the two local services without opening console windows."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
STATE = RUNTIME / "services.json"


def wait(port, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Service exited; read .runtime/{port}.log")
        try:
            if httpx.get(
                f"http://127.0.0.1:{port}/health", timeout=0.5, trust_env=False
            ).is_success:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Service not ready on port {port}")


def stop():
    if not STATE.exists():
        print("No managed services.")
        return
    for process in json.loads(STATE.read_text()).values():
        pid = int(process["pid"])
        if os.name == "nt":
            # Validate the process identity before stopping a possibly reused PID.
            query = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", query],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if not result.stdout.strip():
                continue
            info = json.loads(result.stdout)
            if str(ROOT).lower() in (
                info.get("ExecutablePath") or ""
            ).lower() and "uvicorn" in (info.get("CommandLine") or ""):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        else:
            os.kill(pid, 15)
    STATE.unlink(missing_ok=True)
    print("Managed API services stopped. PostgreSQL data is retained.")


def start(faults=False):
    RUNTIME.mkdir(exist_ok=True)
    if STATE.exists():
        raise SystemExit(
            "Managed services already exist. Use scripts/dev.py stop before starting again."
        )
    if os.name == "nt" and not os.environ.get("DATABASE_URL"):
        from postgres_local import start as postgres_start

        postgres_start()
    processes = {}
    env = {**os.environ, "PYTHONUTF8": "1", "ENABLE_FAULTS": str(faults).lower()}
    try:
        for module, port in [("business", 8101), ("api", 8100)]:
            with (RUNTIME / f"{port}.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        f"order_agent.{module}:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            processes[module] = {"pid": process.pid, "port": port}
            STATE.write_text(json.dumps(processes), encoding="utf-8")
            wait(port, process)
    except Exception:
        stop()
        raise
    print("OrderFlow: http://127.0.0.1:8100")
    print("API docs: http://127.0.0.1:8100/docs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"], nargs="?", default="start")
    parser.add_argument("--faults", action="store_true")
    args = parser.parse_args()
    start(args.faults) if args.action == "start" else stop()
