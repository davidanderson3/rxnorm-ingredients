#!/usr/bin/env python3
"""
Extract RXNORM ingredients (TTY in IN, PIN, MIN) from RXNCONSO.RRF and output JSON.

Outputs objects with keys: RXCUI, TTY, Name, UNII, SCDCs[].
Each SCDC item includes its SCDs as SCDs[].

Usage:
  python extract_rxnorm_ingredients.py \
      --rrf-dir /optional/path/to/rrf  # if omitted, downloads the current release automatically

Notes:
  - Downloads https://download.nlm.nih.gov/rxnorm/RxNorm_full_prescribe_current.zip,
    extracts it into the current working directory, and reads the RRF files from the extracted tree.
  - Generates web assets into ./web by default.
  - Expects the standard RXNCONSO field order used by RxNorm RRF files:
    [0] RXCUI, [1] LAT, [2] TS, [3] LUI, [4] STT, [5] SUI, [6] ISPREF,
    [7] RXAUI, [8] SAUI, [9] SCUI, [10] SDUI, [11] SAB, [12] TTY,
    [13] CODE, [14] STR, [15] SRL, [16] SUPPRESS, [17] CVF
  - RXNREL field order used by RxNorm RRF files:
    [0] RXCUI1, [1] RXAUI1, [2] STYPE1, [3] REL, [4] RXCUI2, [5] RXAUI2,
    [6] STYPE2, [7] RELA, [8] RUI, [9] SRUI, [10] SAB, [11] SL, [12] DIR,
    [13] RG, [14] SUPPRESS, [15] CVF
  - Lines end with a trailing '|', which results in an extra empty field after CVF.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import socketserver
import sys
import urllib.error
import urllib.request
import urllib.parse
import webbrowser
import zipfile
from functools import partial
from threading import Thread
from time import sleep
from typing import Iterable, Dict, Any, Tuple, Set, List


TARGET_SAB = "RXNORM"
TARGET_TTYS = {"IN", "PIN", "MIN"}
RXN_ZIP_URL = "https://download.nlm.nih.gov/rxnorm/RxNorm_full_prescribe_current.zip"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract RxNorm ingredient concepts from RXNCONSO.RRF")
    p.add_argument(
        "--rrf-dir",
        help="Directory containing RXNCONSO.RRF, RXNREL.RRF, RXNSAT.RRF. If omitted, downloads the current RxNorm prescribe ZIP.",
    )
    p.add_argument(
        "--no-serve",
        action="store_true",
        help="Do not start the local HTTP server or open a browser window.",
    )
    return p.parse_args()


def locate_rrf_dir(root: str) -> str | None:
    """Find the directory containing the required RRF files inside extracted contents."""
    required = {"RXNCONSO.RRF", "RXNREL.RRF", "RXNSAT.RRF"}
    for dirpath, _, filenames in os.walk(root):
        if required.issubset(set(filenames)):
            return dirpath
    return None


def download_and_extract(url: str, extract_dir: str) -> str:
    """Download the RxNorm ZIP, extract into extract_dir, and return the RRF directory path."""
    os.makedirs(extract_dir, exist_ok=True)
    zip_name = os.path.basename(urllib.parse.urlparse(url).path) or "rxnorm_prescribe.zip"
    base_name, _ = os.path.splitext(zip_name)
    target_dir = os.path.join(extract_dir, base_name)
    os.makedirs(target_dir, exist_ok=True)
    zip_path = os.path.join(extract_dir, zip_name)
    print(f"Downloading RxNorm prescribe archive from {url} ...", file=sys.stderr)
    urllib.request.urlretrieve(url, zip_path)
    print(f"Extracting to {target_dir} ...", file=sys.stderr)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(target_dir)
    try:
        os.remove(zip_path)
    except OSError:
        pass
    rrf_dir = locate_rrf_dir(target_dir)
    if not rrf_dir:
        raise FileNotFoundError("RXNCONSO.RRF")
    return rrf_dir


def start_http_server(directory: str, preferred_port: int = 8000) -> tuple[socketserver.TCPServer, int]:
    """Start a simple HTTP server rooted at directory; returns (server, port)."""
    class SilentHTTPServer(socketserver.TCPServer):
        allow_reuse_address = True

    Handler = partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    for port in [preferred_port] + list(range(preferred_port + 1, preferred_port + 6)):
        try:
            httpd = SilentHTTPServer(("127.0.0.1", port), Handler)
            return httpd, port
        except OSError:
            continue
    raise OSError("Could not bind an HTTP port")


def _choose_name(existing: Tuple[str, str] | None, candidate: Tuple[str, str]) -> Tuple[str, str]:
    """Pick a better (TS, STR) pair, preferring TS='P'."""
    if existing is None:
        return candidate
    ts1, _ = existing
    ts2, _ = candidate
    if ts1 == 'P':
        return existing
    if ts2 == 'P':
        return candidate
    return existing


def scan_rxnconso(path: str, only_eng: bool = False) -> Tuple[
    Dict[str, Dict[str, str]],  # ingredients
    Dict[str, str],             # scdc_names
    Dict[str, str],             # unii_map
    Set[str],                   # scdc_set
    Dict[str, str],             # scd_names
    Set[str],                   # scd_set
    Set[str],                   # in_set
    Set[str],                   # pin_set
    Set[str],                   # min_set
    Dict[str, str],             # gpck_names
    Set[str],                   # gpck_set
    Dict[str, str],             # bpck_names
    Set[str],                   # bpck_set
    Dict[str, str],             # sbd_names
    Set[str],                   # sbd_set
    Dict[str, str],             # bn_names
    Set[str],                   # bn_set
]:
    """
    Scan RXNCONSO once and return:
      - ingredients: rxcui -> { 'name': str, 'tty': str }
      - scdc_names: rxcui -> name (for SAB=RXNORM, TTY=SCDC)
      - unii_map: rxcui(IN/PIN/MIN) -> UNII code (via SAB=MTHSPL, TTY=SU, CODE)
      - scdc_set: set of RXCUI that are SCDC (for robust joining even if name missing)
      - scd_names: rxcui -> name (for SAB=RXNORM, TTY=SCD)
      - scd_set: set of RXCUI that are SCD
    """
    ingredients: Dict[str, Dict[str, str]] = {}
    ing_best_name: Dict[str, Tuple[str, str]] = {}
    scdc_names_best: Dict[str, Tuple[str, str]] = {}
    unii_map: Dict[str, str] = {}
    scdc_set: Set[str] = set()
    scd_names_best: Dict[str, Tuple[str, str]] = {}
    scd_set: Set[str] = set()
    in_set: Set[str] = set()
    pin_set: Set[str] = set()
    min_set: Set[str] = set()
    gpck_names_best: Dict[str, Tuple[str, str]] = {}
    gpck_set: Set[str] = set()
    bpck_names_best: Dict[str, Tuple[str, str]] = {}
    bpck_set: Set[str] = set()
    sbd_names_best: Dict[str, Tuple[str, str]] = {}
    sbd_set: Set[str] = set()
    bn_names_best: Dict[str, Tuple[str, str]] = {}
    bn_set: Set[str] = set()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 18:
                continue
            rxcui = parts[0]
            lat = parts[1]
            ts = parts[2]
            sab = parts[11]
            tty = parts[12]
            code = parts[13]
            name = parts[14]
            suppress = parts[16] if len(parts) > 16 else ""

            if not rxcui:
                continue

            if sab == TARGET_SAB:
                if tty in TARGET_TTYS:
                    # Exclude suppressed concepts (SUPPRESS must be 'N')
                    if suppress != 'N':
                        continue
                    if only_eng and lat != "ENG":
                        continue
                    ing_best_name[rxcui] = _choose_name(ing_best_name.get(rxcui), (ts, name))
                    ingredients.setdefault(rxcui, {"tty": tty, "name": name})
                    _, name_best = ing_best_name[rxcui]
                    ingredients[rxcui]["name"] = name_best
                    ingredients[rxcui]["tty"] = tty
                    if tty == "IN":
                        in_set.add(rxcui)
                    elif tty == "PIN":
                        pin_set.add(rxcui)
                    elif tty == "MIN":
                        min_set.add(rxcui)

                elif tty == "SCDC":
                    if suppress != 'N':
                        continue
                    scdc_set.add(rxcui)
                    # Always capture the name if available; if only_eng, prefer ENG but still keep others if ENG absent
                    if not only_eng or lat == "ENG":
                        scdc_names_best[rxcui] = _choose_name(scdc_names_best.get(rxcui), (ts, name))
                elif tty == "SCD":
                    if suppress != 'N':
                        continue
                    scd_set.add(rxcui)
                    if not only_eng or lat == "ENG":
                        scd_names_best[rxcui] = _choose_name(scd_names_best.get(rxcui), (ts, name))
                elif tty == "GPCK":
                    if suppress != 'N':
                        continue
                    gpck_set.add(rxcui)
                    if not only_eng or lat == "ENG":
                        gpck_names_best[rxcui] = _choose_name(gpck_names_best.get(rxcui), (ts, name))
                elif tty == "BPCK":
                    if suppress != 'N':
                        continue
                    bpck_set.add(rxcui)
                    if not only_eng or lat == "ENG":
                        bpck_names_best[rxcui] = _choose_name(bpck_names_best.get(rxcui), (ts, name))
                elif tty == "SBD":
                    if suppress != 'N':
                        continue
                    sbd_set.add(rxcui)
                    if not only_eng or lat == "ENG":
                        sbd_names_best[rxcui] = _choose_name(sbd_names_best.get(rxcui), (ts, name))
                elif tty == "BN":
                    if suppress != 'N':
                        continue
                    bn_set.add(rxcui)
                    if not only_eng or lat == "ENG":
                        bn_names_best[rxcui] = _choose_name(bn_names_best.get(rxcui), (ts, name))

            if sab == "MTHSPL" and tty == "SU" and code:
                unii_map.setdefault(rxcui, code)

    scdc_names: Dict[str, str] = {cui: pair[1] for cui, pair in scdc_names_best.items()}
    scd_names: Dict[str, str] = {cui: pair[1] for cui, pair in scd_names_best.items()}
    gpck_names: Dict[str, str] = {cui: pair[1] for cui, pair in gpck_names_best.items()}
    bpck_names: Dict[str, str] = {cui: pair[1] for cui, pair in bpck_names_best.items()}
    sbd_names: Dict[str, str] = {cui: pair[1] for cui, pair in sbd_names_best.items()}
    bn_names: Dict[str, str] = {cui: pair[1] for cui, pair in bn_names_best.items()}
    return (
        ingredients,
        scdc_names,
        unii_map,
        scdc_set,
        scd_names,
        scd_set,
        in_set,
        pin_set,
        min_set,
        gpck_names,
        gpck_set,
        bpck_names,
        bpck_set,
        sbd_names,
        sbd_set,
        bn_names,
        bn_set,
    )


def scan_rxnrel_for_scdc(path: str, ingredient_set: Set[str], scdc_cui_set: Set[str], pin_set: Set[str]) -> Dict[str, Set[str]]:
    """Map ingredient RXCUI -> set of SCDC RXCUIs via RXNREL.

    Handles:
      - IN/MIN: RELA in {has_ingredient, ingredient_of}
      - PIN: RELA in {has_precise_ingredient, precise_ingredient_of}
    """
    ing_to_scdc: Dict[str, Set[str]] = {cui: set() for cui in ingredient_set}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 16:
                continue
            c1 = parts[0]
            st1 = parts[2]
            rel = parts[3]
            c2 = parts[4]
            st2 = parts[6]
            rela = parts[7]
            sab = parts[10]

            if sab != TARGET_SAB:
                continue
            if st1 != "CUI" or st2 != "CUI":
                continue

            if rela in {"has_ingredient", "ingredient_of"}:
                if c1 in ingredient_set and c2 in scdc_cui_set:
                    ing_to_scdc[c1].add(c2)
                elif c2 in ingredient_set and c1 in scdc_cui_set:
                    ing_to_scdc[c2].add(c1)
            elif rela in {"has_precise_ingredient", "precise_ingredient_of"}:
                # direct PIN <-> SCDC
                if c1 in pin_set and c2 in scdc_cui_set:
                    ing_to_scdc[c1].add(c2)
                elif c2 in pin_set and c1 in scdc_cui_set:
                    ing_to_scdc[c2].add(c1)

    return ing_to_scdc


def scan_rxnrel_for_scds(path: str, scdc_cui_set: Set[str], scd_cui_set: Set[str]) -> Dict[str, Set[str]]:
    """Map SCDC RXCUI -> set of SCD RXCUIs via RXNREL (RELA constitutes)."""
    scdc_to_scds: Dict[str, Set[str]] = {cui: set() for cui in scdc_cui_set}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 16:
                continue
            c1 = parts[0]
            st1 = parts[2]
            rel = parts[3]
            c2 = parts[4]
            st2 = parts[6]
            rela = parts[7]
            sab = parts[10]

            if sab != TARGET_SAB:
                continue
            if st1 != "CUI" or st2 != "CUI":
                continue
            if rela != "constitutes":
                continue

            # Either side can be SCDC; the other should be SCD
            if c1 in scd_cui_set and c2 in scdc_cui_set:
                scdc_to_scds[c2].add(c1)
            elif c2 in scd_cui_set and c1 in scdc_cui_set:
                scdc_to_scds[c1].add(c2)

    return scdc_to_scds


def scan_rxnrel_for_packs_sbd(
    path: str,
    scd_cui_set: Set[str],
    gpck_set: Set[str],
    bpck_set: Set[str],
    sbd_set: Set[str],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Map SCD RXCUI -> sets of GPCK, BPCK, and SBD RXCUIs via RXNREL.

    - Packs: RELA in {contains, contained_in}
    - Brands (SBD): RELA in {has_tradename, tradename_of}
    """
    scd_to_gpck: Dict[str, Set[str]] = {cui: set() for cui in scd_cui_set}
    scd_to_bpck: Dict[str, Set[str]] = {cui: set() for cui in scd_cui_set}
    scd_to_sbd: Dict[str, Set[str]] = {cui: set() for cui in scd_cui_set}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 16:
                continue
            c1 = parts[0]
            st1 = parts[2]
            rel = parts[3]
            c2 = parts[4]
            st2 = parts[6]
            rela = parts[7]
            sab = parts[10]

            if sab != TARGET_SAB or st1 != "CUI" or st2 != "CUI":
                continue

            # Packs
            if rela in {"contains", "contained_in"}:
                # normalize so scd is on left variable scd, pack is pk
                if c1 in scd_cui_set and c2 in gpck_set:
                    scd_to_gpck[c1].add(c2)
                elif c2 in scd_cui_set and c1 in gpck_set:
                    scd_to_gpck[c2].add(c1)
                if c1 in scd_cui_set and c2 in bpck_set:
                    scd_to_bpck[c1].add(c2)
                elif c2 in scd_cui_set and c1 in bpck_set:
                    scd_to_bpck[c2].add(c1)

            # Brands
            if rela in {"has_tradename", "tradename_of"}:
                if c1 in scd_cui_set and c2 in sbd_set:
                    scd_to_sbd[c1].add(c2)
                elif c2 in scd_cui_set and c1 in sbd_set:
                    scd_to_sbd[c2].add(c1)

    return scd_to_gpck, scd_to_bpck, scd_to_sbd


def scan_rxnrel_for_sbd_bn(
    path: str,
    sbd_set: Set[str],
    bn_set: Set[str],
) -> Dict[str, Set[str]]:
    """Map SBD RXCUI -> set of BN RXCUIs via has_ingredient/ingredient_of (SAB=RXNORM)."""
    sbd_to_bn: Dict[str, Set[str]] = {cui: set() for cui in sbd_set}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 16:
                continue
            c1 = parts[0]
            st1 = parts[2]
            c2 = parts[4]
            st2 = parts[6]
            rela = parts[7]
            sab = parts[10]
            if sab != TARGET_SAB or st1 != "CUI" or st2 != "CUI":
                continue
            if rela not in {"has_ingredient", "ingredient_of"}:
                continue
            # BN has_ingredient SBD, or SBD ingredient_of BN
            if c1 in bn_set and c2 in sbd_set:
                sbd_to_bn[c2].add(c1)
            elif c2 in bn_set and c1 in sbd_set:
                sbd_to_bn[c1].add(c2)
    return sbd_to_bn


def scan_rxnsat_ndc_rxnorm(path: str) -> Dict[str, Set[str]]:
    """Return mapping CUI -> set of NDC strings where RXNSAT has SAB=RXNORM and ATN='NDC'.

    RXNSAT fields:
      [0] CUI, [1] LUI, [2] SUI, [3] METAUI, [4] STYPE, [5] CODE,
      [6] ATUI, [7] SATUI, [8] ATN, [9] SAB, [10] ATV, [11] SUPPRESS, [12] CVF
    """
    ndc_map: Dict[str, Set[str]] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                parts = line.rstrip("\n").split("|")
                if len(parts) < 13:
                    continue
                cui = parts[0]
                atn = parts[8]
                sab = parts[9]
                atv = parts[10]
                suppress = parts[11]
                if sab == TARGET_SAB and atn == 'NDC' and suppress == 'N' and cui and atv:
                    ndc_map.setdefault(cui, set()).add(atv)
    except FileNotFoundError:
        # handled by caller; return empty
        pass
    return ndc_map


def scan_rxnsat_boss_from(path: str) -> Dict[str, Set[Tuple[str, str]]]:
    """Return product CUI -> {(SCDC CUI, basis)} from RXN_BOSS_FROM.

    RXN_BOSS_FROM values look like "{330403} AM" or "{477368} AI".
    AM means basis-of-strength is active moiety; AI means active ingredient.
    """
    boss_from: Dict[str, Set[Tuple[str, str]]] = {}
    value_re = re.compile(r"\{([^}]+)\}\s+([A-Z]+)")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                parts = line.rstrip("\n").split("|")
                if len(parts) < 13:
                    continue
                cui = parts[0]
                atn = parts[8]
                sab = parts[9]
                atv = parts[10]
                suppress = parts[11]
                if sab != TARGET_SAB or atn != "RXN_BOSS_FROM" or suppress != "N" or not cui:
                    continue
                match = value_re.fullmatch(atv.strip())
                if not match:
                    continue
                boss_from.setdefault(cui, set()).add((match.group(1), match.group(2)))
    except FileNotFoundError:
        pass
    return boss_from


def scan_rxnsat_boss_substances(path: str) -> Dict[str, Dict[str, Dict[str, Set[str]]]]:
    """Return product CUI -> basis kind -> source SCDC CUI -> substance CUIs.

    RXN_AI/RXN_AM values look like "{330403} 12345", where the braced CUI is
    the source SCDC component and the trailing CUI is the substance concept.
    """
    refs: Dict[str, Dict[str, Dict[str, Set[str]]]] = {}
    value_re = re.compile(r"\{([^}]+)\}\s+(\S+)")
    atn_to_basis = {"RXN_AI": "AI", "RXN_AM": "AM"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                parts = line.rstrip("\n").split("|")
                if len(parts) < 13:
                    continue
                cui = parts[0]
                atn = parts[8]
                sab = parts[9]
                atv = parts[10]
                suppress = parts[11]
                basis = atn_to_basis.get(atn)
                if sab != TARGET_SAB or not basis or suppress != "N" or not cui:
                    continue
                match = value_re.fullmatch(atv.strip())
                if not match:
                    continue
                scdc_cui, substance_cui = match.groups()
                refs.setdefault(cui, {}).setdefault(basis, {}).setdefault(scdc_cui, set()).add(substance_cui)
    except FileNotFoundError:
        pass
    return refs


def scan_rxnconso_terms_for_cuis(path: str, cuis: Set[str]) -> Dict[str, Set[str]]:
    """Collect display/search terms for selected product CUIs."""
    terms: Dict[str, Set[str]] = {cui: set() for cui in cuis}
    if not cuis:
        return terms
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 18:
                continue
            cui = parts[0]
            if cui not in cuis:
                continue
            sab = parts[11]
            name = parts[14]
            suppress = parts[16]
            if suppress != "N" or sab not in {TARGET_SAB, "MTHSPL"} or not name:
                continue
            terms.setdefault(cui, set()).add(name)
    return terms


def scan_rxnrel_in_pin_forms(
    path: str,
    in_set: Set[str],
    pin_set: Set[str],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Map IN <-> PIN form relationships via form_of/has_form."""
    in_to_pins: Dict[str, Set[str]] = {cui: set() for cui in in_set}
    pin_to_ins: Dict[str, Set[str]] = {cui: set() for cui in pin_set}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 16:
                continue
            c1 = parts[0]
            st1 = parts[2]
            c2 = parts[4]
            st2 = parts[6]
            rela = parts[7]
            sab = parts[10]
            if sab != TARGET_SAB or st1 != "CUI" or st2 != "CUI":
                continue
            if rela not in {"form_of", "has_form"}:
                continue
            if c1 in in_set and c2 in pin_set:
                in_to_pins.setdefault(c1, set()).add(c2)
                pin_to_ins.setdefault(c2, set()).add(c1)
            elif c2 in in_set and c1 in pin_set:
                in_to_pins.setdefault(c2, set()).add(c1)
                pin_to_ins.setdefault(c1, set()).add(c2)
    return in_to_pins, pin_to_ins


def _match_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def derive_boss_form_scdc_links(
    boss_from: Dict[str, Set[Tuple[str, str]]],
    product_terms: Dict[str, Set[str]],
    scdc_to_ins: Dict[str, Set[str]],
    scdc_to_pins: Dict[str, Set[str]],
    in_to_pins: Dict[str, Set[str]],
    pin_to_ins: Dict[str, Set[str]],
    ingredients: Dict[str, Dict[str, str]],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Add missing IN/PIN links when BOSS says AI and AM differ.

    AM-based products often have SCDC names based on the active moiety, while
    the product terms still name the precise salt/form. Link those products'
    SCDCs to the matching PIN so the product appears under both ingredient
    concepts.
    """
    extra_pin_to_scdc: Dict[str, Set[str]] = {}
    extra_in_to_scdc: Dict[str, Set[str]] = {}
    pin_match_names = {pin: _match_key(meta.get("name", "")) for pin, meta in ingredients.items() if pin in pin_to_ins}

    for product_cui, refs in boss_from.items():
        normalized_terms = [_match_key(term) for term in product_terms.get(product_cui, set())]
        for scdc_cui, basis in refs:
            if basis == "AM":
                for in_cui in scdc_to_ins.get(scdc_cui, set()):
                    for pin_cui in in_to_pins.get(in_cui, set()):
                        pin_name = pin_match_names.get(pin_cui, "")
                        if pin_name and any(pin_name in term for term in normalized_terms):
                            extra_pin_to_scdc.setdefault(pin_cui, set()).add(scdc_cui)
            elif basis == "AI":
                for pin_cui in scdc_to_pins.get(scdc_cui, set()):
                    for in_cui in pin_to_ins.get(pin_cui, set()):
                        extra_in_to_scdc.setdefault(in_cui, set()).add(scdc_cui)

    return extra_pin_to_scdc, extra_in_to_scdc


def _ingredient_concept(
    cui: str,
    ingredients: Dict[str, Dict[str, str]],
    unii_map: Dict[str, str],
    include_unii: bool = True,
    allow_unresolved: bool = False,
) -> Dict[str, str] | None:
    meta = ingredients.get(cui)
    if not meta:
        return {"RXCUI": cui} if allow_unresolved else None
    concept = {"Name": meta.get("name", ""), "RXCUI": cui, "TTY": meta.get("tty", "")}
    unii = unii_map.get(cui) if include_unii else None
    if unii:
        concept["UNII"] = unii
    return concept


def derive_product_active_info(
    boss_from: Dict[str, Set[Tuple[str, str]]],
    boss_substances: Dict[str, Dict[str, Dict[str, Set[str]]]],
    product_terms: Dict[str, Set[str]],
    scdc_names: Dict[str, str],
    scdc_to_ins: Dict[str, Set[str]],
    scdc_to_pins: Dict[str, Set[str]],
    in_to_pins: Dict[str, Set[str]],
    pin_to_ins: Dict[str, Set[str]],
    ingredients: Dict[str, Dict[str, str]],
    unii_map: Dict[str, str],
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Build product-level BoSS and active substance metadata."""
    product_info: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    pin_match_names = {pin: _match_key(meta.get("name", "")) for pin, meta in ingredients.items() if pin in pin_to_ins}

    def concepts(cuis: Set[str]) -> List[Dict[str, str]]:
        items = []
        for cui in sorted(cuis, key=lambda x: (ingredients.get(x, {}).get("name", ""), x)):
            concept = _ingredient_concept(cui, ingredients, unii_map, include_unii=False, allow_unresolved=True)
            if concept:
                items.append(concept)
        return items

    def basis_label(basis: str) -> str:
        return "Active Moiety" if basis == "AM" else "Active Ingredient" if basis == "AI" else basis

    def union_refs(refs: Dict[str, Set[str]]) -> Set[str]:
        cuis: Set[str] = set()
        for values in refs.values():
            cuis.update(values)
        return cuis

    def basis_concepts(cuis: Set[str], basis: str, scdc_cui: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        source_scdc = {
            "Name": scdc_names.get(scdc_cui, ""),
            "RXCUI": scdc_cui,
            "TTY": "SCDC",
        }
        for cui in sorted(cuis, key=lambda x: (ingredients.get(x, {}).get("name", ""), x)):
            concept = _ingredient_concept(cui, ingredients, unii_map, include_unii=False, allow_unresolved=True)
            if not concept:
                continue
            concept["Basis"] = basis
            concept["BasisLabel"] = basis_label(basis)
            concept["SourceSCDC"] = source_scdc
            items.append(concept)
        return items

    for product_cui, refs in boss_from.items():
        substance_refs = boss_substances.get(product_cui, {})
        ai_refs = substance_refs.get("AI", {})
        am_refs = substance_refs.get("AM", {})
        active_ingredient_cuis: Set[str] = union_refs(ai_refs)
        active_moiety_cuis: Set[str] = union_refs(am_refs)
        basis_items: List[Dict[str, Any]] = []
        normalized_terms = [_match_key(term) for term in product_terms.get(product_cui, set())]

        for scdc_cui, basis in sorted(refs, key=lambda x: (scdc_names.get(x[0], ""), x[1], x[0])):
            source_ai_cuis = set(ai_refs.get(scdc_cui, set()))
            source_am_cuis = set(am_refs.get(scdc_cui, set()))
            derived_ai_cuis: Set[str] = set()
            derived_am_cuis: Set[str] = set()

            if basis == "AM":
                ins = set(scdc_to_ins.get(scdc_cui, set()))
                derived_am_cuis.update(ins)
                derived_ai_cuis.update(scdc_to_pins.get(scdc_cui, set()))

                candidate_pins: Set[str] = set()
                for in_cui in ins:
                    candidate_pins.update(in_to_pins.get(in_cui, set()))
                matched_pins = {
                    pin
                    for pin in candidate_pins
                    if pin_match_names.get(pin) and any(pin_match_names[pin] in term for term in normalized_terms)
                }
                if matched_pins:
                    derived_ai_cuis.update(matched_pins)
                elif not candidate_pins:
                    derived_ai_cuis.update(ins)

            elif basis == "AI":
                pins = set(scdc_to_pins.get(scdc_cui, set()))
                ins = set(scdc_to_ins.get(scdc_cui, set()))
                if pins:
                    derived_ai_cuis.update(pins)
                    moieties_for_ref: Set[str] = set()
                    for pin_cui in pins:
                        moieties_for_ref.update(pin_to_ins.get(pin_cui, set()))
                    derived_am_cuis.update(moieties_for_ref or ins)
                else:
                    derived_ai_cuis.update(ins)
                    derived_am_cuis.update(ins)

            active_ingredient_cuis.update(source_ai_cuis or derived_ai_cuis)
            active_moiety_cuis.update(source_am_cuis or derived_am_cuis)
            if basis == "AM":
                basis_items.extend(basis_concepts(source_am_cuis or derived_am_cuis, basis, scdc_cui))
            elif basis == "AI":
                basis_items.extend(basis_concepts(source_ai_cuis or derived_ai_cuis, basis, scdc_cui))

        info: Dict[str, List[Dict[str, Any]]] = {}
        if basis_items:
            info["BasisOfStrength"] = basis_items
        active_ingredients = concepts(active_ingredient_cuis)
        active_moieties = concepts(active_moiety_cuis)
        if active_ingredients:
            info["ActiveIngredients"] = active_ingredients
        if active_moieties:
            info["ActiveMoieties"] = active_moieties
        if info:
            product_info[product_cui] = info

    return product_info


def derive_pin_min_scdc(
    rel_path: str,
    in_set: Set[str],
    pin_set: Set[str],
    min_set: Set[str],
    ing_to_scdc: Dict[str, Set[str]],
    scd_set: Set[str],
    scd_to_scdc: Dict[str, Set[str]],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """
    Build SCDC sets for PIN and MIN by propagating from related INs:
      - PIN: via RELA has_precise_ingredient between IN and PIN
      - MIN: via RELA has_ingredient/ingredients_of between MIN and IN
    """
    pin_to_scdc: Dict[str, Set[str]] = {cui: set() for cui in pin_set}
    min_to_scdc: Dict[str, Set[str]] = {cui: set() for cui in min_set}

    with open(rel_path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            parts = line.rstrip("\n").split("|")
            if len(parts) < 16:
                continue
            c1 = parts[0]
            st1 = parts[2]
            rel = parts[3]
            c2 = parts[4]
            st2 = parts[6]
            rela = parts[7]
            sab = parts[10]
            if sab != TARGET_SAB or st1 != "CUI" or st2 != "CUI":
                continue

            # PIN via IN<->PIN relations (exclude form_of/has_form to avoid over-propagation)
            if rela in {"has_precise_ingredient", "precise_ingredient_of"}:
                # Identify which side is IN and which is PIN, then inherit SCDCs from IN
                if c1 in in_set and c2 in pin_set:
                    pin_to_scdc[c2].update(ing_to_scdc.get(c1, set()))
                elif c2 in in_set and c1 in pin_set:
                    pin_to_scdc[c1].update(ing_to_scdc.get(c2, set()))

            # MIN via has_ingredient(s) or ingredients_of
            if rela in {"has_ingredient", "has_ingredients", "ingredients_of"}:
                # MIN may link directly to IN or to SCD; support both
                if c1 in min_set and c2 in in_set:
                    min_to_scdc[c1].update(ing_to_scdc.get(c2, set()))
                elif c2 in min_set and c1 in in_set:
                    min_to_scdc[c2].update(ing_to_scdc.get(c1, set()))
                elif c1 in min_set and c2 in scd_set:
                    min_to_scdc[c1].update(scd_to_scdc.get(c2, set()))
                elif c2 in min_set and c1 in scd_set:
                    min_to_scdc[c2].update(scd_to_scdc.get(c1, set()))

    return pin_to_scdc, min_to_scdc


def write_json(records: Iterable[Dict[str, Any]], output_path: str, ndjson: bool = False) -> None:
    if ndjson:
        with open(output_path, "w", encoding="utf-8") as out:
            for rec in records:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    else:
        data = list(records)
        with open(output_path, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)


def find_rxnorm_readme(rrf_dir: str) -> str | None:
    """Find the RxNorm prescribe readme near an RRF directory."""
    search_dirs: List[str] = []
    current = os.path.abspath(rrf_dir)
    for _ in range(4):
        search_dirs.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    seen: Set[str] = set()
    for directory in search_dirs:
        if directory in seen or not os.path.isdir(directory):
            continue
        seen.add(directory)
        for name in os.listdir(directory):
            if re.fullmatch(r"Readme_Full_Prescribe_\d{8}\.txt", name, flags=re.IGNORECASE):
                return os.path.join(directory, name)
    return None


def rxnorm_metadata_from_readme(rrf_dir: str) -> Dict[str, str]:
    """Extract the RxNorm release version shown in the web UI."""
    readme_path = find_rxnorm_readme(rrf_dir)
    metadata: Dict[str, str] = {}
    if not readme_path:
        return metadata

    metadata["source_readme"] = os.path.basename(readme_path)
    try:
        with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f.readlines()]
    except OSError:
        return metadata

    release_date = next((line for line in lines if line), "")
    if release_date:
        metadata["rxnorm_release_date"] = release_date

    release_label = ""
    for line in lines:
        if line.startswith("README:"):
            release_label = line.replace("README:", "", 1).strip()
            break
    if release_label:
        metadata["rxnorm_release_label"] = release_label
        release_text = f"{release_label} {os.path.basename(readme_path)}".lower()
        if "prescribable content" in release_text or "prescribe" in release_text:
            metadata["rxnorm_release_type"] = "Prescribable Content"
        elif "full release" in release_text or "full" in release_text:
            metadata["rxnorm_release_type"] = "Full Release"
        match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", release_label)
        if match:
            metadata["rxnorm_version"] = match.group(1)

    if "rxnorm_version" not in metadata:
        match = re.search(r"Readme_Full_Prescribe_(\d{2})(\d{2})(\d{4})\.txt", os.path.basename(readme_path), re.IGNORECASE)
        if match:
            metadata["rxnorm_version"] = f"{match.group(1)}/{match.group(2)}/{match.group(3)}"

    return metadata


def write_web_split(data: List[Dict[str, Any]], out_dir: str, metadata: Dict[str, str] | None = None) -> None:
    """Write lightweight, serverless web assets split by first letter of Name.

    Produces:
      - <out_dir>/manifest.json: [{ key, label, count, file }]
      - <out_dir>/metadata.json: release metadata for the UI
      - <out_dir>/data/<KEY>.json: array of enriched records for that key
    Keys: 'A'..'Z' plus '0-9' bucket for non-letters.
    """
    import os
    os.makedirs(os.path.join(out_dir, 'data'), exist_ok=True)

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    def key_for(name: str) -> str:
        if not name:
            return '0-9'
        ch = name[0].upper()
        return ch if 'A' <= ch <= 'Z' else '0-9'

    for rec in data:
        k = key_for(rec.get('Name') or '')
        buckets.setdefault(k, []).append(rec)

    manifest = []
    for k in sorted(buckets.keys(), key=lambda x: ('Z{' if x=='0-9' else x)):
        arr = buckets[k]
        # keep per-bucket sorted by Name
        arr.sort(key=lambda r: (r.get('Name') or '').lower())
        fname = f"data/{k}.json"
        with open(os.path.join(out_dir, fname), 'w', encoding='utf-8') as f:
            json.dump(arr, f, ensure_ascii=False)
        manifest.append({
            'key': k,
            'label': k if k != '0-9' else '0–9',
            'count': len(arr),
            'file': fname,
        })

    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata or {}, f, ensure_ascii=False, indent=2)


def main() -> int:
    args = parse_args()
    only_eng = True  # enforce LAT=ENG filter
    output_path = "rxnorm_ingredients.json"
    web_split_dir = "web"
    input_path = rel_path = sat_path = ""
    try:
        if args.rrf_dir:
            rrf_dir = args.rrf_dir
            print(f"Using local RRF directory: {rrf_dir}", file=sys.stderr)
        else:
            download_root = os.getcwd()
            rrf_dir = download_and_extract(RXN_ZIP_URL, download_root)
            print(f"Using downloaded RRFs from: {rrf_dir}", file=sys.stderr)
        metadata = rxnorm_metadata_from_readme(rrf_dir)
        input_path = os.path.join(rrf_dir, "RXNCONSO.RRF")
        rel_path = os.path.join(rrf_dir, "RXNREL.RRF")
        sat_path = os.path.join(rrf_dir, "RXNSAT.RRF")

        (
            ingredients,
            scdc_names,
            unii_map,
            scdc_set,
            scd_names,
            scd_set,
            in_set,
            pin_set,
            min_set,
            gpck_names,
            gpck_set,
            bpck_names,
            bpck_set,
            sbd_names,
            sbd_set,
            bn_names,
            bn_set,
        ) = scan_rxnconso(input_path, only_eng=only_eng)

        ing_set = set(ingredients.keys())
        ing_to_scdc = scan_rxnrel_for_scdc(rel_path, ing_set, scdc_set, pin_set)
        scdc_to_scds = scan_rxnrel_for_scds(rel_path, scdc_set, scd_set)
        in_to_pins, pin_to_ins = scan_rxnrel_in_pin_forms(rel_path, in_set, pin_set)
        scdc_to_ins: Dict[str, Set[str]] = {}
        scdc_to_pins: Dict[str, Set[str]] = {}
        for in_cui in in_set:
            for scdc in ing_to_scdc.get(in_cui, set()):
                scdc_to_ins.setdefault(scdc, set()).add(in_cui)
        for pin_cui in pin_set:
            for scdc in ing_to_scdc.get(pin_cui, set()):
                scdc_to_pins.setdefault(scdc, set()).add(pin_cui)
        # invert SCDC->SCDs to SCD->SCDC(s)
        scd_to_scdc: Dict[str, Set[str]] = {}
        for scdc, scds in scdc_to_scds.items():
            for scd in scds:
                scd_to_scdc.setdefault(scd, set()).add(scdc)

        pin_to_scdc, min_to_scdc = derive_pin_min_scdc(
            rel_path, in_set, pin_set, min_set, ing_to_scdc, scd_set, scd_to_scdc
        )

        # Packs and brands for SCDs
        scd_to_gpck, scd_to_bpck, scd_to_sbd = scan_rxnrel_for_packs_sbd(
            rel_path, scd_set, gpck_set, bpck_set, sbd_set
        )

        # RXNORM NDCs from RXNSAT
        cui_to_ndcs = scan_rxnsat_ndc_rxnorm(sat_path)
        boss_from = scan_rxnsat_boss_from(sat_path)
        boss_substances = scan_rxnsat_boss_substances(sat_path)
        product_terms = scan_rxnconso_terms_for_cuis(input_path, set(boss_from.keys()))
        boss_pin_to_scdc, boss_in_to_scdc = derive_boss_form_scdc_links(
            boss_from,
            product_terms,
            scdc_to_ins,
            scdc_to_pins,
            in_to_pins,
            pin_to_ins,
            ingredients,
        )
        product_active_info = derive_product_active_info(
            boss_from,
            boss_substances,
            product_terms,
            scdc_names,
            scdc_to_ins,
            scdc_to_pins,
            in_to_pins,
            pin_to_ins,
            ingredients,
            unii_map,
        )

        def attach_active_info(obj: Dict[str, Any]) -> None:
            info = product_active_info.get(obj["RXCUI"])
            if not info:
                return
            for key, value in info.items():
                obj[key] = value

        # SBD -> BN mapping
        sbd_to_bn = scan_rxnrel_for_sbd_bn(rel_path, sbd_set, bn_set)

        # unify cui -> scdc set
        cui_to_scdc: Dict[str, Set[str]] = {}
        for cui in ing_set:
            if cui in in_set:
                s = set()
                s.update(ing_to_scdc.get(cui, set()))
                s.update(boss_in_to_scdc.get(cui, set()))
                cui_to_scdc[cui] = s
            elif cui in pin_set:
                s = set()
                s.update(ing_to_scdc.get(cui, set()))  # direct PIN->SCDC via precise_ingredient
                s.update(pin_to_scdc.get(cui, set()))  # inherited via IN
                s.update(boss_pin_to_scdc.get(cui, set()))  # AM BOSS products matched to their precise salt/form
                cui_to_scdc[cui] = s
            elif cui in min_set:
                cui_to_scdc[cui] = min_to_scdc.get(cui, set())
            else:
                cui_to_scdc[cui] = set()

        # Assemble final records with at least one SCDC, sorted by ingredient name
        output: List[Dict[str, Any]] = []
        for cui, meta in ingredients.items():
            name = meta["name"]
            tty = meta["tty"]
            scdc_ids = sorted(cui_to_scdc.get(cui, set()), key=lambda x: scdc_names.get(x, ""))
            if not scdc_ids:
                continue  # skip ingredients with no SCDCs
            scdcs = []
            for sc in scdc_ids:
                # collect SCDs for this SCDC
                scd_ids = sorted(scdc_to_scds.get(sc, set()), key=lambda x: scd_names.get(x, ""))
                scds = []
                for s in scd_ids:
                    gpcks = [{"Name": gpck_names.get(g, ""), "RXCUI": g, "TTY": "GPCK"} for g in sorted(scd_to_gpck.get(s, set()), key=lambda x: gpck_names.get(x, ""))]
                    bpcks = [{"Name": bpck_names.get(b, ""), "RXCUI": b, "TTY": "BPCK"} for b in sorted(scd_to_bpck.get(s, set()), key=lambda x: bpck_names.get(x, ""))]
                    # Build SBD objects including optional BNs and NDCs
                    sbds = []
                    for b in sorted(scd_to_sbd.get(s, set()), key=lambda x: sbd_names.get(x, "")):
                        sbd_obj = {"Name": sbd_names.get(b, ""), "RXCUI": b, "TTY": "SBD"}
                        ndcs_s = sorted(cui_to_ndcs.get(b, set()))
                        if ndcs_s:
                            sbd_obj["NDCs"] = ndcs_s
                        bn_ids = sorted(sbd_to_bn.get(b, set()), key=lambda x: bn_names.get(x, ""))
                        if bn_ids:
                            sbd_obj["BNs"] = [{"Name": bn_names.get(bn, ""), "RXCUI": bn, "TTY": "BN"} for bn in bn_ids]
                        attach_active_info(sbd_obj)
                        sbds.append(sbd_obj)
                    scd_obj = {"Name": scd_names.get(s, ""), "RXCUI": s, "TTY": "SCD"}
                    # attach RXNORM NDCs if present for SCD
                    ndcs = sorted(cui_to_ndcs.get(s, set()))
                    if ndcs:
                        scd_obj["NDCs"] = ndcs
                    if gpcks:
                        # add NDCs for GPCKs if present
                        for obj in gpcks:
                            ndcs_g = sorted(cui_to_ndcs.get(obj["RXCUI"], set()))
                            if ndcs_g:
                                obj["NDCs"] = ndcs_g
                            attach_active_info(obj)
                        scd_obj["GPCKs"] = gpcks
                    if bpcks:
                        for obj in bpcks:
                            ndcs_b = sorted(cui_to_ndcs.get(obj["RXCUI"], set()))
                            if ndcs_b:
                                obj["NDCs"] = ndcs_b
                            attach_active_info(obj)
                        scd_obj["BPCKs"] = bpcks
                    if sbds:
                        scd_obj["SBDs"] = sbds
                    attach_active_info(scd_obj)
                    scds.append(scd_obj)
                scdcs.append({"Name": scdc_names.get(sc, ""), "RXCUI": sc, "TTY": "SCDC", "SCDs": scds})
            unii = unii_map.get(cui)
            top = {
                "Name": name,
                "RXCUI": cui,
                "TTY": tty,
                "SCDCs": scdcs,
            }
            if unii:
                top["UNII"] = unii
            output.append(top)

        output.sort(key=lambda r: (r.get("Name") or "").lower())
        write_json(output, output_path, ndjson=False)
        write_web_split(output, web_split_dir, metadata)
        print(f"Wrote {output_path} and web assets in {web_split_dir}/", file=sys.stderr)

        if not args.no_serve:
            # Serve the web UI and open in browser
            try:
                httpd, port = start_http_server(directory=".")
                url = f"http://127.0.0.1:{port}/web/"
                print(f"Serving ./web via http://127.0.0.1:{port}/web/ (Ctrl+C to stop)", file=sys.stderr)
                webbrowser.open(url)
                # Run the server until interrupted
                thread = Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                while True:
                    sleep(1)
            except KeyboardInterrupt:
                print("Stopping server...", file=sys.stderr)
                try:
                    httpd.shutdown()
                except Exception:
                    pass
            except Exception as e:
                print(f"Could not start HTTP server: {e}", file=sys.stderr)
    except FileNotFoundError as e:
        missing = e.filename or input_path
        sys.stderr.write(f"File not found after download/extract: {missing}\n")
        sys.stderr.write("Ensure the RxNorm archive contains RXNCONSO.RRF, RXNREL.RRF, and RXNSAT.RRF.\n")
        return 1
    except urllib.error.URLError as e:
        sys.stderr.write(f"Download failed: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
