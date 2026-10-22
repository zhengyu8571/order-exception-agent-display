import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from order_agent.config import settings

ROOT = Path(__file__).resolve().parents[1]


def kill_tree(process):
    if process and process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=True,
            )
        else:
            process.kill()
        process.wait(timeout=10)


@pytest.fixture(autouse=True)
def deterministic_planner(monkeypatch):
    monkeypatch.setenv("PLANNER_MODE", "demo")
    settings.cache_clear()
    yield
    settings.cache_clear()


class LiveSystem:
    def __init__(self):
        self.agent = None
        self.business = None
        self.url = "http://127.0.0.1:18100"
        self.orders_url = "http://127.0.0.1:18101"
        self.env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "ENABLE_FAULTS": "true",
            "PLANNER_MODE": "demo",
            "ORDER_SERVICE_URL": self.orders_url,
            "OPERATOR_TOKEN": "local-operator",
            "SUPERVISOR_TOKEN": "local-supervisor",
            "SERVICE_TOKEN": "local-service",
        }
        self.headers = {"Authorization": "Bearer local-supervisor"}
        self.service_headers = {"Authorization": "Bearer local-service"}

    def spawn(self, module, port):
        instance_id = uuid4().hex
        log_dir = ROOT / ".runtime" / "test-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f"{port}.log").open("ab") as log:
            proc = subprocess.Popen(
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
                env={**self.env, "INSTANCE_ID": instance_id},
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"{module} exited, see {log_dir}")
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{port}/health", timeout=0.5, trust_env=False
                )
                if (
                    response.is_success
                    and response.json().get("instance_id") == instance_id
                ):
                    if module == "api":
                        self.agent_instance = instance_id
                        self.agent_server_pid = response.json()["pid"]
                    return proc
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        kill_tree(proc)
        raise RuntimeError(f"Service {module} startup timed out")

    def restart_agent(self):
        if self.agent and self.agent.poll() is None:
            kill_tree(self.agent)
        self.agent = self.spawn("api", 18100)

    def request(self, method, path, *, body=None, expected=200, headers=None):
        response = httpx.request(
            method,
            self.url + path,
            json=body,
            headers=headers or self.headers,
            timeout=getattr(self, "request_timeout", 40),
            trust_env=False,
        )
        assert response.status_code == expected, response.text
        return response.json()

    def scenario(self, **kwargs):
        response = httpx.post(
            self.orders_url + "/demo/scenarios",
            headers=self.service_headers,
            json=kwargs,
            trust_env=False,
        )
        response.raise_for_status()
        return response.json()["order_id"]

    def task(self, fault="none", **scenario):
        order_id = self.scenario(**scenario)
        return self.request(
            "POST", "/api/tasks", body={"order_id": order_id, "fault": fault}
        )

    def approval(self, task, **kwargs):
        candidate = kwargs.get("candidate_id", "consolidated")
        return {
            **task.get("approval_context", {}).get(candidate, {}),
            "proposal_version": task["state"]["revision"],
            "decision": "approve",
            "candidate_id": "consolidated",
            "request_id": "approval-" + task["id"],
            **kwargs,
        }


@pytest.fixture(scope="session")
def live():
    original_dsn = os.environ.get("DATABASE_URL")
    base_dsn = settings().database_url.get_secret_value()
    test_dsn = os.environ.get("TEST_DATABASE_URL")
    if not test_dsn:
        params = conninfo_to_dict(base_dsn)
        database = params.get("dbname", "order_agent") + "_test"
        with psycopg.connect(base_dsn, autocommit=True) as conn:
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
            ).fetchone():
                conn.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
                )
        test_dsn = make_conninfo(base_dsn, dbname=database)
    if not conninfo_to_dict(test_dsn).get("dbname", "").endswith("_test"):
        raise RuntimeError(
            "Integration database name must end in _test; demo/production data must stay separate"
        )
    os.environ["DATABASE_URL"] = test_dsn
    settings.cache_clear()
    system = LiveSystem()
    try:
        system.business = system.spawn("business", 18101)
        system.restart_agent()
        yield system
    finally:
        for process in (system.agent, system.business):
            kill_tree(process)
        if original_dsn is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_dsn
        settings.cache_clear()
