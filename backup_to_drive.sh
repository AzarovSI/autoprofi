#!/usr/bin/env bash
# Build the complete, portable recovery archive. No secrets or DB dump.
# Google Drive upload is a separate authenticated operation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$ROOT/recovery/build_backup.py" "$@"
printf '%s\n' 'DRIVE_FOLDER_ID=1MFXb-PykO7yUOvneqAzLkWcHrOZ0xf2E'
