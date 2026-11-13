#!/usr/bin/env bash
# Downloads the challenge data files with the API key from .env into data/.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
BASE="${VALIO_API_BASE:-https://junction26.valioaimo.fi}"
mkdir -p data/raw
curl -fsSL -H "X-API-Key: $VALIO_API_KEY" "$BASE/api/products/download" -o data/raw/products.json
curl -fsSL -H "X-API-Key: $VALIO_API_KEY" "$BASE/api/delivery-reliability/download" -o data/delivery_reliability.csv
curl -fsSL -H "X-API-Key: $VALIO_API_KEY" "$BASE/api/carbon-footprint/download" -o data/carbon_footprint.csv
python3 -c "import json;print(len(json.load(open('data/raw/products.json'))),'products')"
