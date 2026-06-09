#!/usr/bin/env bash
# Build the CLEAN client repo for The Outdoor Squad handoff.
#
# Allowlist on purpose: it copies ONLY the product files into handoff/client-repo/,
# so none of AI Sprints' internal sales/pricing/close/payment/referral notes (or
# the messy git history) can leak to the client. Run from anywhere:
#
#     bash handoff/build-clean-repo.sh
#
# Then:  cd handoff/client-repo && git init && git add -A && git commit -m "Robo-Nick"
#        …and push to Nicholas's new GitHub repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
OUT="$SCRIPT_DIR/client-repo"

# --- The product allowlist (everything the running bot needs, nothing else) ---
FILES=(
  app.py
  widget.js
  widget_preview.html
  demo.html
  knowledge_base.md
  render.yaml
  requirements.txt
  Dockerfile
  supabase_schema.sql
  run_review_build_smoke.py
  migrate_local_data_to_supabase.py
  .gitignore
)
# Directories included as their GIT-TRACKED contents only (so untracked local
# junk — original PDFs, stray emails, draft FAQs the app would otherwise load —
# never makes it into the client repo; it must match what's actually deployed).
DIRS=(
  source-docs           # the client's own business content (KB, FAQ, reviews, injury note)
)

echo "Building clean client repo at: $OUT"
rm -rf "$OUT"
mkdir -p "$OUT"

for f in "${FILES[@]}"; do
  if [ -f "$REPO_ROOT/$f" ]; then
    cp "$REPO_ROOT/$f" "$OUT/$f"
    echo "  + $f"
  else
    echo "  ! MISSING (skipped): $f"
  fi
done

for d in "${DIRS[@]}"; do
  # -z = NUL-separated, unquoted output, so filenames with spaces or the "·"
  # middle-dot (the OCR source files) copy correctly instead of aborting.
  while IFS= read -r -d '' tracked; do
    mkdir -p "$OUT/$(dirname "$tracked")"
    cp "$REPO_ROOT/$tracked" "$OUT/$tracked"
  done < <(cd "$REPO_ROOT" && git ls-files -z "$d")
  echo "  + $d/ (git-tracked files only)"
done

# Client-facing README + env template replace the internal versions.
cp "$SCRIPT_DIR/CLIENT-README.md" "$OUT/README.md";  echo "  + README.md (client version)"
cp "$SCRIPT_DIR/.env.example"     "$OUT/.env.example"; echo "  + .env.example"

# Production default for the handoff service.
if [ -f "$OUT/render.yaml" ]; then
  sed -i.bak 's/value: review/value: handoff/' "$OUT/render.yaml" && rm -f "$OUT/render.yaml.bak"
  echo "  ~ render.yaml DEPLOYMENT_MODE -> handoff"
fi

echo ""
echo "Done. Verify there are NO internal docs:"
echo "  ls $OUT   # should show product files only — no *CLOSE*, *PAYMENT*, *PROPOSAL*, *CALL*, HANDOFF-PLAN"
echo ""
echo "Then push to Nicholas's GitHub:"
echo "  cd $OUT && git init -b main && git add -A && git commit -m 'Robo-Nick — The Outdoor Squad bot'"
echo "  git remote add origin git@github.com:<nicholas-account>/robo-nick.git && git push -u origin main"
