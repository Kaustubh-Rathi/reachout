"""Source Data Backup & Integrity Snapshot Utility.

Creates immutable, versioned backups and SHA-256 checksum manifests for all
critical legacy source artifacts and logs prior to any migration or execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
BACKUP_BASE_DIR = DATA_DIR / "backups"


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()


def create_source_backup(
    snapshot_name: Optional[str] = None,
) -> Path:
    """Create a timestamped backup of all source artifacts and logs with a manifest."""
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag = snapshot_name or f"snapshot_{now_str}"
    snapshot_dir = BACKUP_BASE_DIR / tag
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    artifacts_to_backup = [
        DATA_DIR / "MNC_Final.xlsx",
        DATA_DIR / "Reachout.xlsx",
        DATA_DIR / "mnc_cleaned_contacts.csv",
        DATA_DIR / "crm_data.json",
        DATA_DIR / "crm_data.json.bak",
        LOGS_DIR / "mnc_whatsapp_send_log.csv",
        LOGS_DIR / "people_email_send_log.csv",
    ]

    manifest: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_name": tag,
        "files": {},
    }

    copied_count = 0
    for file_path in artifacts_to_backup:
        if file_path.exists():
            rel_name = file_path.relative_to(ROOT_DIR).as_posix()
            dest_path = snapshot_dir / file_path.name
            shutil.copy2(file_path, dest_path)
            checksum = compute_file_sha256(file_path)
            size = file_path.stat().st_size
            manifest["files"][rel_name] = {
                "backup_filename": file_path.name,
                "sha256": checksum,
                "size_bytes": size,
            }
            copied_count += 1

    manifest_path = snapshot_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[Backup] Successfully preserved {copied_count} artifacts in {snapshot_dir}")
    print(f"[Backup] Manifest generated at {manifest_path}")
    return snapshot_dir


if __name__ == "__main__":
    snapshot_dir = create_source_backup("initial_snapshot")
    print(f"Done. Snapshot saved to {snapshot_dir}")
