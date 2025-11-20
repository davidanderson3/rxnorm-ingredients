# RxNorm Ingredients Extractor

Builds an enriched ingredient dataset from RxNorm RRF files and serves a lightweight browser UI.

## Quick start
- Auto-download latest: `python3 extract_rxnorm_ingredients.py`
- Use local RRFs: `python3 extract_rxnorm_ingredients.py --rrf-dir /path/to/rrf`

Outputs: `rxnorm_ingredients.json` plus `web/` assets. The script starts a local server and opens the UI automatically.

## Data highlights
- Ingredients (IN/PIN/MIN) with names and optional UNIIs.
- Linked SCDCs → SCDs, packs (GPCK/BPCK), brands (SBD/BN), and RXNORM NDCs.
- Non-suppressed RXNORM concepts; NDCs pulled from RXNSAT (SAB=RXNORM, ATN=NDC).
