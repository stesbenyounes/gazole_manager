#!/usr/bin/env bash
set -e

PROJ="gazole_manager"
SRC_DIR="$(pwd)"
BACK_DIR="$HOME/Backups/$PROJ"
TS="$(date +'%Y%m%d_%H%M%S')"
ARCHIVE="$BACK_DIR/${PROJ}_$TS.tar.gz"

mkdir -p "$BACK_DIR"

# Figer les dépendances
if [ -d ".venv" ]; then
  . .venv/bin/activate
  pip freeze > requirements.txt || true
fi

# Inclure code + templates + static + base SQLite dans instance/
tar -czf "$ARCHIVE" \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  app.py models.py extensions.py utils.py import_csv.py README.md requirements.txt \
  templates static instance \
  consommation.csv sauvegarde.sh

echo "✅ Sauvegarde créée: $ARCHIVE"

