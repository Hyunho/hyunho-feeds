#!/usr/bin/env bash
# Mirror tracked functions/_secret_/ -> live functions/<secret.txt>/ for Pages deploy routes.
# Never prints the secret path. Safe to run repeatedly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -f secret.txt ]]; then
  echo "sync-secret-functions: secret.txt missing" >&2
  exit 1
fi
if [[ ! -d functions/_secret_ ]]; then
  echo "sync-secret-functions: functions/_secret_ missing" >&2
  exit 1
fi
python3 - <<'PY'
import os, shutil, re
from pathlib import Path
root = Path('.')
sec = (root / 'secret.txt').read_text().strip()
if not re.fullmatch(r'[0-9a-f]{32}', sec):
    raise SystemExit('sync-secret-functions: secret.txt format unexpected')
src = root / 'functions' / '_secret_'
dst = root / 'functions' / sec
dst.mkdir(parents=True, exist_ok=True)

def should_skip(name: str) -> bool:
    return '.bak' in name or name.endswith('~')

# Remove dest files (non-backup) that are no longer in src
for dirpath, dirnames, filenames in os.walk(dst, topdown=False):
    rel = Path(dirpath).relative_to(dst)
    for fn in filenames:
        if should_skip(fn):
            continue
        dp = Path(dirpath) / fn
        sp = src / rel / fn
        if not sp.exists():
            dp.unlink()
    for dn in dirnames:
        ddir = Path(dirpath) / dn
        sdir = src / rel / dn
        if not sdir.exists():
            try:
                ddir.rmdir()
            except OSError:
                pass

# Copy src -> dst
for dirpath, dirnames, filenames in os.walk(src):
    rel = Path(dirpath).relative_to(src)
    target_dir = dst / rel
    target_dir.mkdir(parents=True, exist_ok=True)
    for fn in filenames:
        if should_skip(fn):
            continue
        shutil.copy2(Path(dirpath) / fn, target_dir / fn)
print('sync-secret-functions: ok (live functions path refreshed from _secret_)')
PY
