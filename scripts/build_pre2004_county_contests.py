"""Build county-level statewide contest slices for Arizona 2000 and 2002."""

import csv
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "Data" / "openelections-data-az"
OUTPUT_DIR = ROOT / "Data" / "county_contests"

OFFICE_TO_CONTEST = {
    "President": "president",
    "U.S. Senate": "us_senate",
    "Governor": "governor",
    "Secretary of State": "secretary_of_state",
    "Attorney General": "attorney_general",
    "State Treasurer": "treasurer",
    "Superintendent of Public Instruction": "superintendent",
}
GENERAL_FILES = {
    2000: "20001107__az__general.csv",
    2002: "20021105__az__general.csv",
    2004: "20041102__az__general.csv",
    2006: "20061107__az__general.csv",
    2008: "20081104__az__general.csv",
    2010: "20101102__az__general.csv",
    2012: "20121106__az__general.csv",
    2014: "20141104__az__general.csv",
    2016: "20161108__az__general__precinct.csv",
}


def build_contest(year, source_file, office, contest_type):
    totals = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0, "dem_candidate": "", "rep_candidate": ""})
    candidate_votes = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    with source_file.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("office", "").strip() != office:
                continue
            county = row.get("county", "").strip()
            if not county:
                continue
            candidate = row.get("candidate", "").strip()
            candidate_key = "".join(character for character in candidate.upper() if character.isalpha())
            if (
                candidate_key in {"OVERVOTES", "UNDERVOTES", "BLANKVOTES", "TOTALVOTES"}
                or candidate_key.startswith("TIMES")
                or candidate_key in {"REGISTEREDVOTERS", "BALLOTSCAST", "TOTALVOTES"}
            ):
                continue
            votes = int(float(row.get("votes", "0") or 0))
            party = row.get("party", "").strip().upper()
            bucket = "dem" if party == "DEM" else "rep" if party == "REP" else "other"
            totals[county][bucket] += votes
            if candidate:
                candidate_votes[county][bucket][candidate] += votes

    county_totals = {}
    for county, values in sorted(totals.items()):
        dem = values["dem"]
        rep = values["rep"]
        other = values["other"]
        total = dem + rep + other
        margin = rep - dem
        winner = "REP" if margin > 0 else "DEM" if margin < 0 else "TIE"
        county_totals[county] = {
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "total_votes": total,
            "dem_candidate": max(candidate_votes[county]["dem"], key=candidate_votes[county]["dem"].get, default=""),
            "rep_candidate": max(candidate_votes[county]["rep"], key=candidate_votes[county]["rep"].get, default=""),
            "margin": margin,
            "margin_pct": (margin / total * 100) if total else 0,
            "winner": winner,
        }

    return {
        "year": year,
        "contest_type": contest_type,
        "scope": "county",
        "county_totals": county_totals,
    }


def main():
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [
        entry for entry in manifest.get("files", [])
        if entry.get("contest_type") != "corporation_commissioner"
        and not (entry.get("contest_type") == "treasurer" and entry.get("year") == 2012)
        and not (entry.get("year") in GENERAL_FILES and entry.get("scope") == "county")
    ]

    for year, filename in GENERAL_FILES.items():
        source_file = SOURCE_DIR / str(year) / filename
        with source_file.open(newline="", encoding="utf-8-sig") as handle:
            offices = sorted({row.get("office", "").strip() for row in csv.DictReader(handle)})
        for office in offices:
            contest_type = OFFICE_TO_CONTEST.get(office)
            if not contest_type:
                continue
            payload = build_contest(year, source_file, office, contest_type)
            output_file = OUTPUT_DIR / f"{contest_type}_{year}.json"
            output_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            entries.append({
                "year": year,
                "contest_type": contest_type,
                "file": output_file.name,
                "rows": len(payload["county_totals"]),
                "scope": "county",
            })

    manifest["files"] = sorted(entries, key=lambda entry: (int(entry["year"]), entry["contest_type"]))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
