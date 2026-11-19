"""Publish 2026 county and district contest slices from the validated export.

This deliberately leaves 2026 precinct slices unpublished until statewide
election-precinct polygons have been verified. Run aggregate_2026_preview.py
first, then this script.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Data"
PREVIEW = DATA / "2026_general_aggregation_preview.json"
RAW = DATA / "export-110326GeneralElection.json"
YEAR = 2026
STATEWIDE = (
    "governor", "secretary_of_state", "attorney_general", "treasurer",
    "superintendent",
)


def write_slice(folder: Path, filename: str, payload: dict, manifest: list[dict], scope: str, contest: str, rows: int) -> None:
    (folder / filename).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest.append({"year": YEAR, "contest_type": contest, "file": filename,
                     "rows": rows, "scope": scope})


def standard_result(row: dict, house: bool = False) -> dict:
    fields = ("dem_votes", "rep_votes", "other_votes", "total_votes",
              "dem_candidate", "rep_candidate", "margin", "margin_pct", "winner")
    result = {field: row[field] for field in fields}
    if house:
        result["seats"] = row["seats"]
        result["dem_candidates"] = row["dem_candidates"]
        result["rep_candidates"] = row["rep_candidates"]
    return result


def existing_manifest(folder: Path) -> list[dict]:
    path = folder / "manifest.json"
    return [entry for entry in json.loads(path.read_text(encoding="utf-8"))["files"]
            if int(entry.get("year", 0)) != YEAR]


def main() -> None:
    preview = json.loads(PREVIEW.read_text(encoding="utf-8"))
    if preview.get("source_sha256") != __import__("hashlib").sha256(RAW.read_bytes()).hexdigest():
        raise ValueError("Validated preview is out of sync with the local 2026 export")
    contests = preview["contests"]
    county_dir = DATA / "county_contests"
    district_dir = DATA / "district_contests"
    county_manifest = existing_manifest(county_dir)
    district_manifest = existing_manifest(district_dir)
    for contest in STATEWIDE:
        counties = contests[contest]["counties"]
        if len(counties) != 15:
            raise ValueError(f"Missing counties for {contest}")
        write_slice(county_dir, f"{contest}_{YEAR}.json",
                    {"year": YEAR, "contest_type": contest, "scope": "county",
                     "county_totals": {key: standard_result(row) for key, row in counties.items()}},
                    county_manifest, "county", contest, len(counties))
        for scope, results in contests[contest]["districts"].items():
            scopes = ("state_house", "state_senate") if scope == "legislative" else ("congressional",)
            for target_scope in scopes:
                write_slice(district_dir, f"{target_scope}_{contest}_{YEAR}.json",
                            {"year": YEAR, "scope": target_scope, "contest_type": contest,
                             "general": {"results": {key: standard_result(row) for key, row in results.items()}}},
                            district_manifest, target_scope, contest, len(results))

    for contest, scope in (("us_house", "congressional"),
                           ("state_senate", "state_senate"),
                           ("state_house", "state_house")):
        numbered = {key.rsplit("_", 1)[1]: value["statewide"].copy()
                    for key, value in contests.items() if key.startswith(f"{contest}_")}
        expected = 9 if contest == "us_house" else 30
        if len(numbered) != expected:
            raise ValueError(f"Expected {expected} {contest} districts; found {len(numbered)}")
        if contest == "state_house":
            raw = json.loads(RAW.read_text(encoding="utf-8"))
            for ballot in raw["results"]["ballotItems"]:
                if not ballot["name"].startswith("State Representative - District No."):
                    continue
                district = str(int(ballot["name"].split("No.", 1)[1].strip()))
                row = numbered[district]
                row["seats"] = int(ballot.get("voteFor") or 2)
                row["dem_candidates"] = [option["name"].strip() for option in ballot["ballotOptions"]
                                         if str(option.get("id", "")).isdigit()
                                         and str(option.get("politicalParty", "")).lower() in {"dem", "democratic"}]
                row["rep_candidates"] = [option["name"].strip() for option in ballot["ballotOptions"]
                                         if str(option.get("id", "")).isdigit()
                                         and str(option.get("politicalParty", "")).lower() in {"rep", "republican"}]
        write_slice(district_dir, f"{scope}_{contest}_{YEAR}.json",
                    {"year": YEAR, "scope": scope, "contest_type": contest,
                     "general": {"results": {key: standard_result(row, contest == "state_house")
                                             for key, row in numbered.items()}}},
                    district_manifest, scope, contest, len(numbered))

    for folder, entries in ((county_dir, county_manifest), (district_dir, district_manifest)):
        (folder / "manifest.json").write_text(json.dumps({"files": entries}, indent=2) + "\n", encoding="utf-8")
    # Remove files from the earlier trial that introduced offices absent from
    # these atlas layers. These are exact generated 2026 filenames.
    for contest in ("corporation_commissioner", "mine_inspector"):
        (county_dir / f"{contest}_{YEAR}.json").unlink(missing_ok=True)
        for scope in ("congressional", "state_house", "state_senate"):
            (district_dir / f"{scope}_{contest}_{YEAR}.json").unlink(missing_ok=True)
    print(f"Published {len(county_manifest)} county and {len(district_manifest)} district manifest entries (including historical entries)")


if __name__ == "__main__":
    main()
