#!/usr/bin/env bash
# Compile Tailwind into static/css/tailwind.css (committed; no Node at runtime).
# The version is pinned so a rebuild changes the file only when the page does.
set -euo pipefail
cd "$(dirname "$0")/.."
TAILWIND_VERSION="3.4.17"
npx -y "tailwindcss@${TAILWIND_VERSION}" \
  -c tailwind.config.js \
  -i scripts/tailwind.input.css \
  -o static/css/tailwind.css \
  --minify
echo "built static/css/tailwind.css with tailwindcss ${TAILWIND_VERSION}"
