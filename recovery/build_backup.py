#!/usr/bin/env python3
"""Build a complete code recovery archive using an explicit secret-safe allowlist."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DIRS = ("app", "static", "templates", "dist", "migrations", "tests", "recovery",
        "docs", ".claude")
FILES = ("requirements.txt", "rebuild_index.py", "migrate_incidents.py",
         "backup_to_drive.sh", "RESTORE.md", "HANDOFF_RECOVERY.md",
         "Dockerfile.restore", "compose.restore.yml", ".dockerignore",
         "CLAUDE.md", ".gitignore")
# Локальная выгрузка Perplexity содержит пароль открытым текстом — никогда не в архив.
SKIP_PREFIXES = ("docs/archive/perplexity_export_",)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT.parent))
    args = parser.parse_args()
    recovery = ROOT / "recovery"
    actual = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    if actual != (recovery / "requirements-source.sha256").read_text().strip():
        raise SystemExit("requirements.txt изменён. Обновите lock/wheels и повторите тест восстановления.")
    selected = []
    for folder in DIRS:
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if any(p in ("__pycache__", "runtime", ".venv", ".git") for p in rel.parts):
                continue
            if rel.as_posix().startswith(SKIP_PREFIXES) or path.name == ".DS_Store":
                continue
            if path.name.startswith(".env") or path.suffix == ".pyc" or path.name == "config.json":
                continue
            if path.is_symlink():
                raise SystemExit(f"Ссылка не допускается в бэкапе: {rel}")
            selected.append(path)
    selected.extend(ROOT / f for f in FILES)
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(set(selected))}
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    stamp = dt.datetime.now(dt.timezone.utc)
    manifest = {"format": 1, "created_at_utc": stamp.isoformat(), "git_commit": commit,
                "platform": "Linux x86_64 / CPython 3.12",
                "database_included": False, "secrets_included": False, "files": hashes}
    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("avtoprofi_FULL_RECOVERY_" + stamp.strftime("%Y%m%d_%H%M%S") + ".zip")
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED) as archive:
        for rel in hashes:
            archive.write(ROOT / rel, "avtoprofi_app/" + rel)
        archive.writestr("avtoprofi_app/BACKUP_MANIFEST.json",
                         json.dumps(manifest, indent=2, ensure_ascii=False))
    print(out)
    print("Files:", len(hashes), "SHA256:", hashlib.sha256(out.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
