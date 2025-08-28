# RxNorm Ingredients Extractor

This repository includes a Python script that builds an enriched, searchable list of RxNorm ingredients and related drug concepts from the RxNorm RRF files. It can also generate a small serverless web viewer for fast browsing.

## What it produces

- A single JSON file (`rxnorm_ingredients.json`) containing enriched ingredient records.
- Optional per-letter split JSON and a tiny web app under `web/` for fast, client‑side browsing.

Each ingredient record includes:
- Name, RXCUI, TTY (IN, PIN, or MIN)
- UNII (from MTHSPL/SU when available)
- SCDCs (Semantic Clinical Drug Components)
  - For each SCDC: SCDs (Semantic Clinical Drugs)
    - For each SCD: optional GPCKs, BPCKs, SBDs, and RXNORM NDCs
      - For each SBD: optional BNs (Brand Names) and RXNORM NDCs

## Data sources and relationships

Required RxNorm files (RRF format) in the working directory:
- `RXNCONSO.RRF` — concepts, names, and types (TTY)
- `RXNREL.RRF` — inter‑concept relationships
- `RXNSAT.RRF` — attributes (used here for SAB=RXNORM NDCs)

Selections and joins:
- Ingredients: `SAB=RXNORM`, `TTY in {IN, PIN, MIN}`, `SUPPRESS = 'N'`
- UNII: `SAB=MTHSPL`, `TTY=SU` rows in `RXNCONSO` where RXCUI matches the ingredient
- SCDCs: `SAB=RXNORM`, `TTY=SCDC`, `SUPPRESS='N'`
- SCDC links:
  - IN/MIN <-> SCDC via `RELA in {has_ingredient, ingredient_of}`
  - PIN <-> SCDC via `RELA in {has_precise_ingredient, precise_ingredient_of}`
- SCDs: `SAB=RXNORM`, `TTY=SCD`, `SUPPRESS='N'`; linked to SCDC via `RELA=constitutes`
- Packs (GPCK/BPCK): linked to SCD via `RELA in {contains, contained_in}`; names from `RXNCONSO` with `TTY in {GPCK, BPCK}`, `SUPPRESS='N'`
- SBDs: linked to SCD via `RELA in {has_tradename, tradename_of}`; names from `RXNCONSO` with `TTY=SBD`, `SUPPRESS='N'`
- BNs (brand names): linked to SBD via `RELA in {has_ingredient, ingredient_of}`; names from `RXNCONSO` with `TTY=BN`, `SUPPRESS='N'`
- NDCs: from `RXNSAT` where `SAB=RXNORM`, `ATN='NDC'`, `SUPPRESS='N'`; attached to SCD/SBD/GPCK/BPCK concepts

## Output schema (abridged)

Top-level ingredient object:
```
{
  "Name": string,
  "RXCUI": string,
  "TTY": "IN" | "PIN" | "MIN",
  "UNII": string?,
  "SCDCs": [
    {
      "Name": string,
      "RXCUI": string,
      "TTY": "SCDC",
      "SCDs": [
        {
          "Name": string,
          "RXCUI": string,
          "TTY": "SCD",
          "NDCs": [string]?,
          "GPCKs": [{ "Name": string, "RXCUI": string, "TTY": "GPCK", "NDCs"?: [string] }]?,
          "BPCKs": [{ "Name": string, "RXCUI": string, "TTY": "BPCK", "NDCs"?: [string] }]?,
          "SBDs": [
            {
              "Name": string,
              "RXCUI": string,
              "TTY": "SBD",
              "NDCs": [string]?,
              "BNs": [{ "Name": string, "RXCUI": string, "TTY": "BN" }]?
            }
          ]?
        }
      ]
    }
  ]
}
```

All ingredient records are sorted by `Name` (case‑insensitive). Records without any SCDCs are excluded.

## Usage

Generate the enriched JSON and optional web assets (English only):
```
python3 extract_rxnorm_ingredients.py \
  --only-eng \
  --input RXNCONSO.RRF \
  --rel RXNREL.RRF \
  --sat RXNSAT.RRF \
  --output rxnorm_ingredients.json \
  --web-split web
```

Other options:
- `--ndjson` — write newline‑delimited JSON instead of a single array
- `--only-eng` — restrict to English (`LAT=ENG`) names

## Serverless web viewer

The `--web-split web` option generates:
- `web/index.html` — a small, static single‑page viewer
- `web/manifest.json` — a manifest of per‑letter data files
- `web/data/*.json` — per‑letter data chunks (A–Z and 0–9) for fast loading

To view locally:
```
cd web
python3 -m http.server 8000
# then open http://localhost:8000
```

Features:
- Letter tabs load only that letter’s chunk on demand
- In‑letter filtering box
- Master/detail view with nested expansions for SCDCs → SCDs → packs/brands
- Field labels standardized to Name, RXCUI, TTY order; bold labels in the UI

## Notes

- All selections obey `SUPPRESS='N'` for RXNORM concepts (IN/PIN/MIN/SCDC/SCD/GPCK/BPCK/SBD/BN).
- UNIIs come from `MTHSPL|SU` rows where the RXCUI matches the ingredient concept.
