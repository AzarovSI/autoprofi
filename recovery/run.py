#!/usr/bin/env python3
"""Portable offline bootstrap. Does not need Perplexity, uv, or a build tool."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent
RECOVERY = ROOT / "recovery"
RUNTIME = RECOVERY / "runtime"


def verify_bundle():
    manifest = ROOT / "BACKUP_MANIFEST.json"
    if not manifest.exists():
        raise SystemExit("Нет BACKUP_MANIFEST.json: запускайте распакованный полный бэкап.")
    for relative, expected in json.loads(manifest.read_text())["files"].items():
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Нарушена целостность бэкапа: {relative}")


def main():
    # Resolve launcher symlinks before venv inspects the interpreter home.
    # This supports relocatable CPython installations as well as system Python.
    executable = Path(sys.executable).resolve()
    if str(executable) != sys.executable:
        os.execv(str(executable), [str(executable), str(Path(__file__).resolve()), *sys.argv[1:]])
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(RECOVERY / "config.json"))
    parser.add_argument("--check", action="store_true", help="Install and verify DB, do not start server")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or sys.platform != "linux":
        raise SystemExit("Проверенный комплект: Linux x86_64, Python 3.12. См. RESTORE.md.")
    import platform
    if platform.machine() not in ("x86_64", "AMD64"):
        raise SystemExit("Архив зависимостей собран для Linux x86_64.")
    verify_bundle()
    config_path = Path(args.config)
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    dsn = os.environ.get("DB_DSN") or config.get("DB_DSN")
    if not dsn:
        raise SystemExit("Заполните DB_DSN в recovery/config.json по config.example.json.")
    if config_path.exists():
        config_path.chmod(0o600)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    env_dir = RUNTIME / "venv"
    python = env_dir / "bin/python"
    lock = RECOVERY / "requirements.lock"
    marker = env_dir / ".installed-lock"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if not python.exists():
        venv.EnvBuilder(with_pip=True, symlinks=True).create(env_dir)
    if not marker.exists() or marker.read_text() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "--no-index",
                        "--find-links", str(RECOVERY / "wheels"), "--require-hashes",
                        "-r", str(lock)], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)
        marker.write_text(digest)
    key_file = RUNTIME / "secret.key"
    key = os.environ.get("SECRET_KEY")
    if not key:
        if not key_file.exists():
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(secrets.token_hex(32))
        key = key_file.read_text().strip()
    if len(key) < 32:
        raise SystemExit("Ключ авторизации должен содержать не менее 32 символов.")
    env = {**os.environ, "DB_DSN": dsn, "SECRET_KEY": key,
           "TOKEN_TTL_HOURS": str(config.get("TOKEN_TTL_HOURS", 24)),
           "PYTHONUNBUFFERED": "1"}
    subprocess.run([str(python), str(RECOVERY / "check_database.py")],
                   cwd=ROOT, env=env, check=True)
    if args.check:
        print("RESTORE CHECK OK: зависимости и структура БД проверены.")
        return
    host = str(os.environ.get("HOST") or config.get("HOST", "127.0.0.1"))
    port = int(os.environ.get("PORT") or config.get("PORT", 8000))
    os.chdir(ROOT)
    os.execve(str(python), [str(python), "-m", "uvicorn", "app.main:app",
                           "--host", host, "--port", str(port)], env)


if __name__ == "__main__":
    main()
