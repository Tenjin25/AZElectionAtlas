"""Rebuild 2004 congressional statewide contest rollups from raw precinct results."""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.aggregate_arizona import historical_party_lookup, iter_official_2012_rows


DATA = ROOT / "Data"
SOURCE_DIR = DATA / "official-az-precinct-results" / "2004" / "general"
CROSSWALK = DATA / "crosswalks" / "election_precinct_to_districts.csv"
OUTPUT = DATA / "district_contests"

OFFICES = {
    "President": ("president", "John F. Kerry", "George W. Bush"),
    "U.S. Senate": ("us_senate", "Stuart Starky", "John McCain"),
}


def key(county, precinct):
    return (county or "").strip().upper(), (precinct or "").strip().upper()


def main():
    assignments = {}
    with CROSSWALK.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            district = row.get("congressional_district", "").strip()
            if district:
                assignments[key(row.get("county"), row.get("election_precinct"))] = district

    totals = {
        office: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
        for office in OFFICES
    }
    for source_file in sorted(SOURCE_DIR.glob("*.txt")):
        for row in iter_official_2012_rows(source_file, historical_party_lookup()):
            contest = row.get("office", "").strip()
            office = "President" if contest == "president" else "U.S. Senate" if contest == "us_senate" else ""
            if not office:
                continue
            candidate = row.get("candidate", "").strip().upper()
            if candidate in {"OVER VOTES", "UNDER VOTES", "BLANK VOTES", "TOTAL VOTES"}:
                continue
            district = assignments.get(key(row.get("county"), row.get("precinct")))
            if not district:
                continue
            votes = int(row.get("votes", 0) or 0)
            if office == "President":
                party = "DEM" if "KERRY" in candidate else "REP" if "BUSH" in candidate else "OTHER"
            else:
                party = "DEM" if "STARKY" in candidate else "REP" if "MCCAIN" in candidate else "OTHER"
            bucket = "dem" if party == "DEM" else "rep" if party == "REP" else "other"
            totals[office][district][bucket] += votes

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for office, (contest_type, dem_candidate, rep_candidate) in OFFICES.items():
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
            "year": 2004,
            "scope": "congressional",
            "contest_type": contest_type,
            "general": {"results": results},
        }
        (OUTPUT / f"congressional_{contest_type}_2004.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
