#!/usr/bin/env bash
set -euo pipefail

# Weekly OpenMemory data backup (raspi5): sqlite DB + Qdrant collection snapshot.
# Run from cron; keeps the last 6 weekly backups under BACKUP_ROOT.
#
# Restore:
#   sqlite : docker compose stop openmemory-mcp
#            cp <backup>/openmemory.db /mnt/data/workspace/mem0/openmemory/api/openmemory.db
#   qdrant : curl -X POST 'http://localhost:6333/collections/openmemory/snapshots/upload?priority=snapshot' \
#              -F snapshot=@<backup>/qdrant-openmemory.snapshot
#            docker compose up -d

BACKUP_ROOT=/mnt/data/workspace/backups/openmemory-weekly
SQLITE_DB=/mnt/data/workspace/mem0/openmemory/api/openmemory.db
QDRANT=http://localhost:6333
COLLECTION=openmemory
KEEP=6

DEST="$BACKUP_ROOT/$(date +%F)"
mkdir -p "$DEST"

echo "[$(date -Is)] backup start -> $DEST"

# sqlite: online backup via the sqlite backup API (safe while the service runs)
python3 - "$SQLITE_DB" "$DEST/openmemory.db" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
EOF

# qdrant: create snapshot, download it, verify checksum, delete server-side copy
SNAP_JSON=$(curl -sf -X POST "$QDRANT/collections/$COLLECTION/snapshots")
SNAP_NAME=$(printf '%s' "$SNAP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['name'])")
SNAP_SUM=$(printf '%s' "$SNAP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['checksum'])")
curl -sf -o "$DEST/qdrant-$COLLECTION.snapshot" "$QDRANT/collections/$COLLECTION/snapshots/$SNAP_NAME"
LOCAL_SUM=$(sha256sum "$DEST/qdrant-$COLLECTION.snapshot" | cut -d' ' -f1)
if [ "$LOCAL_SUM" != "$SNAP_SUM" ]; then
    echo "[$(date -Is)] ERROR: snapshot checksum mismatch ($LOCAL_SUM != $SNAP_SUM)" >&2
    exit 1
fi
curl -sf -X DELETE "$QDRANT/collections/$COLLECTION/snapshots/$SNAP_NAME" > /dev/null

# retention: keep the newest $KEEP dated directories
ls -1d "$BACKUP_ROOT"/????-??-??/ 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf

echo "[$(date -Is)] backup done: $(du -sh "$DEST" | cut -f1)"
