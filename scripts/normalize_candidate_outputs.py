"""Normalize candidate labels in already-generated Arizona JSON outputs."""

import json
import re
from pathlib import Path


DATA = Path(__file__).resolve().parents[1] / "Data"
PRESIDENTIAL_NOMINEES = {
    2012: {"dem": "Barack Obama", "rep": "Mitt Romney"},
    2016: {"dem": "Hillary Clinton", "rep": "Donald Trump"},
    2020: {"dem": "Joseph R. Biden", "rep": "Donald J. Trump"},
    2024: {"dem": "Kamala D. Harris", "rep": "Donald J. Trump"},
}


def normalize_name(value):
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return ""
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if "," in part:
            last, given = [token.strip() for token in part.split(",", 1)]
            part = f"{given} {last}"
        part = part.lower().title()
        part = re.sub(r"\bMc([a-z])", lambda match: f"Mc{match.group(1).upper()}", part)
        part = re.sub(r"\bO'([a-z])", lambda match: f"O'{match.group(1).upper()}", part)
        parts.append(part)
    return " / ".join(parts)


def candidate_for(year, contest, party, value):
    if contest == "president" and year in PRESIDENTIAL_NOMINEES:
        return PRESIDENTIAL_NOMINEES[year][party]
    return normalize_name(value)


def normalize_result(result, year, contest):
    for party in ("dem", "rep"):
        field = f"{party}_candidate"
        if field in result:
            result[field] = candidate_for(year, contest, party, result[field])


def normalize_file(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    match = re.search(r"(?:^|_)(president|us_senate|governor|attorney_general|secretary_of_state|treasurer|corporation_commissioner|state_house|state_senate)_(\d{4})\.json$", path.name)
    if path.name == "elections_aggregated.json":
        for rows in (payload.get("county", []), payload.get("precinct", []), payload.get("district", [])):
            for row in rows:
                year = int(row.get("year", 0) or 0)
                for field in list(row):
                    match_field = re.match(r"(.+)_(dem|rep)_candidate$", field)
                    if match_field:
                        contest, party = match_field.groups()
                        row[field] = candidate_for(year, contest, party, row[field])
    elif match:
        contest, year = match.groups()
        year = int(year)
        containers = [payload.get("county_totals", {}), payload.get("precinct_results", {})]
        containers.append(payload.get("general", {}).get("results", {}))
        for results in containers:
            for result in results.values():
                normalize_result(result, year, contest)
    else:
        return False
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main():
    paths = [DATA / "elections_aggregated.json"]
    paths.extend(path for folder in ("county_contests", "contests", "district_contests") for path in (DATA / folder).glob("*.json") if path.name != "manifest.json")
    changed = sum(normalize_file(path) for path in paths)
    print(f"Normalized {changed} JSON data sources")


if __name__ == "__main__":
    main()
