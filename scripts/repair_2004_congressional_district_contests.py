"""Rebuild 2004 congressional statewide contest rollups from raw precinct results."""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.aggregate_arizona import (
    build_historical_precinct_district_assignments,
    historical_party_lookup,
    iter_official_2012_rows,
    normalize_precinct_name,
)


DATA = ROOT / "Data"
SOURCE_DIRS = {
    2004: DATA / "official-az-precinct-results" / "2004" / "general",
    2008: DATA / "official-az-precinct-results" / "2008",
}
CROSSWALK = DATA / "crosswalks" / "election_precinct_to_districts.csv"
OUTPUT = DATA / "district_contests"

OFFICES = {
    "President": ("president", "John F. Kerry", "George W. Bush"),
    "U.S. Senate": ("us_senate", "Stuart Starky", "John McCain"),
}

def key(county, precinct):
    import re
    normalize = lambda value: re.sub(r"[^A-Z0-9]", "", str(value or "").upper().split(" - PREC #", 1)[0])
    return normalize(county), normalize(precinct)


def main():
    assignments = {}
    with CROSSWALK.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("year") not in {"2004", "2006", "2010"}:
                continue
            district = row.get("congressional_district", "").strip()
            if district:
                assignments.setdefault(key(row.get("county"), row.get("election_precinct")), district)

    # The 2008 files use the precinct vintage preserved in the county GIS
    # archives. Prefer those polygon-derived assignments where available,
    # then fall back to the older election crosswalk for the rest.
    historical = build_historical_precinct_district_assignments()

    for year, source_dir in SOURCE_DIRS.items():
      totals = {office: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0}) for office in OFFICES}
      for source_file in sorted(source_dir.glob("*.txt")):
        for row in iter_official_2012_rows(source_file, historical_party_lookup()):
            contest = row.get("office", "").strip()
            office = "President" if contest == "president" else "U.S. Senate" if contest == "us_senate" else ""
            if not office:
                continue
            candidate = row.get("candidate", "").strip().upper()
            if candidate in {"OVER VOTES", "UNDER VOTES", "BLANK VOTES", "TOTAL VOTES"}:
                continue
            historical_item = historical.get((year, normalize_precinct_name(row.get("county")), normalize_precinct_name(row.get("precinct"))))
            district = (historical_item or {}).get("congressional") or assignments.get(key(row.get("county"), row.get("precinct")))
            if not district:
                continue
            votes = int(row.get("votes", 0) or 0)
            if office == "President":
                if year == 2004:
                    party = "DEM" if "KERRY" in candidate else "REP" if "BUSH" in candidate else "OTHER"
                else:
                    party = "DEM" if "OBAMA" in candidate else "REP" if "MCCAIN" in candidate else "OTHER"
            else:
                party = "DEM" if "STARKY" in candidate else "REP" if "MCCAIN" in candidate else "OTHER"
            bucket = "dem" if party == "DEM" else "rep" if party == "REP" else "other"
            totals[office][district][bucket] += votes

      OUTPUT.mkdir(parents=True, exist_ok=True)
      for office, (contest_type, dem_candidate, rep_candidate) in OFFICES.items():
        if year == 2008 and contest_type == "president":
            dem_candidate, rep_candidate = "Barack Obama", "John McCain"
        results = {}
        for district in sorted(totals[office], key=lambda value: int(value)):
            values = totals[office][district]
            dem = values["dem"]
            rep = values["rep"]
            other = values["other"]
            total = dem + rep + other
            margin = rep - dem
            results[district] = {
                "dem_votes": dem,
                "rep_votes": rep,
                "other_votes": other,
                "total_votes": total,
                "dem_candidate": dem_candidate,
                "rep_candidate": rep_candidate,
                "margin": margin,
                "margin_pct": (margin / total * 100) if total else 0,
                "winner": "REP" if margin > 0 else "DEM" if margin < 0 else "TIE",
            }
        payload = {
            "year": year,
            "scope": "congressional",
            "contest_type": contest_type,
            "general": {"results": results},
        }
        (OUTPUT / f"congressional_{contest_type}_{year}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    manifest_path = OUTPUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": []}
    manifest["files"] = [entry for entry in manifest.get("files", []) if not (entry.get("year") == 2008 and entry.get("scope") == "congressional" and entry.get("contest_type") in {"president", "us_senate"})]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
