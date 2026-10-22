"""Project-local PostgreSQL for Windows; no system service or administrator rights."""

import os
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
PG = RUNTIME / "pgsql" / "bin"
DATA = RUNTIME / "pgdata"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(*args, **kwargs):
    return subprocess.run(
        [str(a) for a in args], check=True, creationflags=FLAGS, **kwargs
    )


def start():
    if os.name != "nt":
        raise SystemExit(
            "Use docker compose up on Linux/macOS, or configure DATABASE_URL."
        )
    RUNTIME.mkdir(exist_ok=True)
    if not (PG / "pg_ctl.exe").exists():
        archive = RUNTIME / "postgresql-17.6.zip"
        print("Downloading project-local PostgreSQL 17.6...", flush=True)
        urllib.request.urlretrieve(
            "https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip",
            archive,
        )
        with zipfile.ZipFile(archive) as z:
            for item in z.namelist():
                if item.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                    z.extract(item, RUNTIME)
    if not (DATA / "PG_VERSION").exists():
        password_file = RUNTIME / "init-password.txt"
        password_file.write_text("order_agent_local", encoding="utf-8")
        try:
            run(
                PG / "initdb.exe",
                "-D",
                DATA,
                "-U",
                "order_agent",
                "--pwfile",
                password_file,
                "-A",
                "scram-sha-256",
                "-E",
                "UTF8",
                "--locale=C",
                stdout=subprocess.DEVNULL,
            )
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run(
        [str(PG / "pg_ctl.exe"), "-D", str(DATA), "status"],
        capture_output=True,
        creationflags=FLAGS,
    )
    if status.returncode:
        run(
            PG / "pg_ctl.exe",
            "-D",
            DATA,
            "-l",
            RUNTIME / "postgres.log",
            "-o",
            "-h 127.0.0.1 -p 55432",
            "-w",
            "start",
        )
    import psycopg

    with psycopg.connect(
        "postgresql://order_agent:order_agent_local@127.0.0.1:55432/postgres",
        autocommit=True,
    ) as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname='order_agent'"
        ).fetchone():
            conn.execute("CREATE DATABASE order_agent")
    print("PostgreSQL ready on 127.0.0.1:55432", flush=True)


if __name__ == "__main__":
    start()
