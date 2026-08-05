"""Audit generated Arizona atlas outputs for structural and vote-total consistency."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATEWIDE = {"president", "us_senate", "governor", "attorney_general", "secretary_of_state", "treasurer"}
DISTRICT_ONLY = {"us_house", "state_house", "state_senate"}


def rdh_controls() -> dict[tuple[int, str], int]:
    """Return RDH block totals for years where precinct files omit central/mail votes."""
    try:
        from aggregate_arizona import load_rdh_block_aggregates
        aggregates = load_rdh_block_aggregates()
    except Exception:
        return {}
    controls = defaultdict(int)
    for (year, chamber, _district, contest), values in aggregates.items():
        # Congressional and legislative sums are separate views of the same
        # statewide vote; use one chamber only to avoid doubling the control.
        if chamber == "congressional":
            controls[(int(year), contest)] += round(sum(values.values()))
    return dict(controls)


def read_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def totals(node: dict) -> tuple[int, int, int, int]:
    return tuple(int(node.get(key, 0) or 0) for key in ("dem_votes", "rep_votes", "other_votes", "total_votes"))


def add_totals(values):
    return tuple(sum(row[index] for row in values) for index in range(4))


def audit() -> dict:
    errors = []
    warnings = []
    report = {"source": {}, "outputs": {}, "crosswalk": {}, "checks": [], "errors": errors, "warnings": warnings}

    source_files = sorted((DATA / "openelections-data-az").rglob("*.csv"))
    source_years = sorted({int(match.group(1)) for path in source_files if (match := re.search(r"/(20\d{2})/", path.as_posix()))})
    report["source"] = {"csv_files": len(source_files), "years": source_years}
    if not source_files:
        errors.append("No Arizona source CSV files were found.")

    manifest = read_json(DATA / "manifest.json")
    aggregate = read_json(DATA / "elections_aggregated.json")
    report["outputs"]["manifest_counts"] = manifest.get("counts", {})
    report["outputs"]["aggregate_years"] = aggregate.get("years", [])
    if not aggregate.get("county") or not aggregate.get("precinct"):
        errors.append("The aggregate output has no county or precinct rows.")

    county_files = {path.stem: read_json(path) for path in (DATA / "county_contests").glob("*.json") if path.stem != "manifest"}
    contest_files = {path.stem: read_json(path) for path in (DATA / "contests").glob("*.json") if path.stem != "manifest"}
    district_files = {path.stem: read_json(path) for path in (DATA / "district_contests").glob("*.json") if path.stem != "manifest"}
    rdh_total_controls = rdh_controls()
    report["outputs"]["rdh_total_controls"] = {f"{year}_{contest}": total for (year, contest), total in sorted(rdh_total_controls.items())}
    report["outputs"]["file_counts"] = {"county_contests": len(county_files), "contests": len(contest_files), "district_contests": len(district_files)}

    forbidden = [name for name, node in county_files.items() if node.get("contest_type") in DISTRICT_ONLY]
    if forbidden:
        errors.append(f"District-only contests appeared in county_contests: {forbidden}")

    statewide_checks = []
    for name, node in county_files.items():
        contest = node.get("contest_type")
        if contest not in STATEWIDE:
            continue
        county_total = add_totals([totals(value) for value in node.get("county_totals", {}).values()])
        for scope in ("congressional", "state_house", "state_senate"):
            district_name = f"{scope}_{contest}_{node.get('year')}"
            district_node = district_files.get(district_name)
            if not district_node:
                warnings.append(f"Missing district rollup for {district_name}.")
                continue
            district_total = add_totals([totals(value) for value in district_node.get("general", {}).get("results", {}).values()])
            control_total = rdh_total_controls.get((int(node.get("year")), contest), county_total[3])
            control_type = "rdh_blocks" if (int(node.get("year")), contest) in rdh_total_controls else "county_precincts"
            coverage = round((district_total[3] / control_total), 6) if control_total else 1
            statewide_checks.append({"file": district_name, "county_total": county_total[3], "control_total": control_total, "control_type": control_type, "district_total": district_total[3], "coverage": coverage})
            if control_type == "county_precincts" and district_total[3] > control_total:
                errors.append(f"District rollup exceeds county total in {district_name}.")
            if coverage < 0.999:
                warnings.append(f"Incomplete precinct-to-district coverage in {district_name}: {coverage:.2%}.")
    report["checks"].append({"name": "statewide_district_rollups", "results": statewide_checks})

    crosswalk_path = DATA / "crosswalks" / "election_precinct_to_districts.csv"
    crosswalk_rows = list(csv.DictReader(crosswalk_path.open("r", encoding="utf-8-sig", newline=""))) if crosswalk_path.exists() else []
    assignments = defaultdict(set)
    for row in crosswalk_rows:
        key = (row.get("county", "").strip().upper(), row.get("election_precinct", "").strip().upper())
        assignments[key].add((row.get("congressional_district", ""), row.get("legislative_district", "")))
    conflicting = {key: sorted(values) for key, values in assignments.items() if len(values) > 1}
    matched = sum(row.get("match_status") == "matched" for row in crosswalk_rows)
    report["crosswalk"] = {"rows": len(crosswalk_rows), "matched": matched, "unmatched": len(crosswalk_rows) - matched, "conflicting_assignments": conflicting}
    if conflicting:
        errors.append(f"Found {len(conflicting)} precincts with conflicting district assignments.")
    if crosswalk_rows and matched < len(crosswalk_rows):
        warnings.append(f"{len(crosswalk_rows) - matched} precincts are not matched to Census VTDs.")

    state_house_districts = set()
    for path in source_files:
        if path.parent.name.lower() not in {"general", "results"}:
            continue
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("office", "")).strip().lower() == "state house" and row.get("district"):
                    state_house_districts.add(str(row["district"]).strip())
    if state_house_districts and not any(re.search(r"[ab]$", value, re.I) for value in state_house_districts):
        warnings.append("Source State House records contain district numbers but no A/B seat designation; House seats cannot be separated reliably.")
    report["checks"].append({"name": "state_house_seat_designation", "district_values": sorted(state_house_districts)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DATA / "reports" / "arizona_output_audit.json")
    args = parser.parse_args()
    result = audit()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"errors": len(result["errors"]), "warnings": len(result["warnings"]), "report": str(args.report)}, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
