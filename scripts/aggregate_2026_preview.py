"""Aggregate the Arizona 2026 general-election reporting export by precinct.

The source is an unofficial, mutable election-night export. This script keeps
its timestamp and checks every candidate's precinct sum against the county
total. It writes a preview instead of changing the atlas manifests until the
2026 precinct boundaries can be matched to map geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://app.enhancedvoting.com/cdn/results/Arizona/export-110326GeneralElection.json"
OUTPUT = ROOT / "Data" / "2026_general_aggregation_preview.json"

STATEWIDE_CONTESTS = {
    "Governor and Lieutenant Governor": "governor",
    "Secretary of State": "secretary_of_state",
    "Attorney General": "attorney_general",
    "State Treasurer": "treasurer",
    "Superintendent of Public Instruction": "superintendent",
    "Corporation Commissioner": "corporation_commissioner",
}
DISTRICT_CONTESTS = {
    "U.S. Representative in Congress": "us_house",
    "State Senator": "state_senate",
    "State Representative": "state_house",
}


def contest_key(name: str) -> str | None:
    if name in STATEWIDE_CONTESTS:
        return STATEWIDE_CONTESTS[name]
    for label, key in DISTRICT_CONTESTS.items():
        if name.startswith(label):
            match = re.search(r"District No\.\s*(\d+)", name)
            if match:
                return f"{key}_{int(match.group(1))}"
    return None


def party_key(option: dict) -> str:
    # The export assigns party labels to some declared write-ins. Their IDs
    # are nonnumeric; keep those votes separate from the ballot nominees.
    if not str(option.get("id", "")).isdigit():
        return "other"
    party = (option.get("politicalParty") or "").strip().lower()
    if party in {"dem", "democratic"}:
        return "dem"
    if party in {"rep", "republican"}:
        return "rep"
    return "other"


def display_option(contest: str, option: dict) -> dict:
    if contest != "governor":
        return option
    # The source lists governor/lieutenant governor tickets with party tags.
    # Existing atlas governor slices display only the governor's name.
    name = option.get("name", "").split(" / ", 1)[0].strip()
    return {**option, "name": re.sub(r"\s+\([^()]+\)$", "", name)}


def empty_result() -> dict:
    return {"dem_votes": 0, "rep_votes": 0, "other_votes": 0,
            "total_votes": 0, "dem_candidate": "", "rep_candidate": "", "methods": {}}


def add_vote(result: dict, option: dict, votes: int, groups: list[dict] | None = None) -> None:
    party = party_key(option)
    result[f"{party}_votes"] += votes
    result["total_votes"] += votes
    if groups is not None:
        if sum(int(group.get("voteCount") or 0) for group in groups) != votes:
            raise ValueError("Voting-method counts differ from candidate total")
        for group in groups:
            name = group["groupName"]
            method = result["methods"].setdefault(name, {"dem_votes": 0, "rep_votes": 0,
                                                           "other_votes": 0, "total_votes": 0})
            count = int(group.get("voteCount") or 0)
            method[f"{party}_votes"] += count
            method["total_votes"] += count
    # Write-ins sometimes inherit a major-party label. Use the highest-vote
    # candidate as the display name, while retaining every reported vote.
    if party in {"dem", "rep"} and votes > result.get(f"_{party}_display_votes", -1):
        result[f"{party}_candidate"] = option.get("name", "").strip()
        result[f"_{party}_display_votes"] = votes


def finish(result: dict) -> dict:
    result.pop("_dem_display_votes", None)
    result.pop("_rep_display_votes", None)
    margin = result["rep_votes"] - result["dem_votes"]
    result["margin"] = margin
    result["margin_pct"] = round(100 * margin / result["total_votes"], 4) if result["total_votes"] else 0
    result["winner"] = "REP" if margin > 0 else "DEM" if margin < 0 else "TIE"
    return result


def combine_result(target: dict, row: dict) -> None:
    for party in ("dem", "rep", "other"):
        target[f"{party}_votes"] += row[f"{party}_votes"]
    target["total_votes"] += row["total_votes"]
    for name, values in row["methods"].items():
        method = target["methods"].setdefault(name, {"dem_votes": 0, "rep_votes": 0,
                                                       "other_votes": 0, "total_votes": 0})
        for field in ("dem_votes", "rep_votes", "other_votes", "total_votes"):
            method[field] += values[field]
    for party in ("dem", "rep"):
        if not target[f"{party}_candidate"] and row[f"{party}_candidate"]:
            target[f"{party}_candidate"] = row[f"{party}_candidate"]


def aggregate(source: bytes) -> dict:
    payload = json.loads(source)
    if payload.get("electionDate") != "2026-11-03" or len(payload.get("localResults", [])) != 15:
        raise ValueError("Expected the November 3, 2026 general election and all 15 counties")

    state = defaultdict(empty_result)
    counties = defaultdict(lambda: defaultdict(empty_result))
    precincts = defaultdict(lambda: defaultdict(empty_result))
    checks = []
    reporting = defaultdict(lambda: {"participating": 0, "reporting": 0})
    candidate_totals = defaultdict(int)
    for county_data in payload["localResults"]:
        county = re.sub(r"\s+County$", "", county_data["name"]).strip()
        for ballot in county_data.get("ballotItems", []):
            contest = contest_key(ballot["name"])
            if not contest:
                continue
            reporting[contest]["participating"] += ballot.get("precinctsParticipating") or 0
            reporting[contest]["reporting"] += ballot.get("precinctsReporting") or 0
            for option in ballot.get("ballotOptions", []):
                county_votes = int(option.get("voteCount") or 0)
                candidate_totals[(contest, str(option.get("id", "")))] += county_votes
                precinct_rows = option.get("precinctResults") or []
                precinct_votes = sum(int(row.get("voteCount") or 0) for row in precinct_rows)
                if precinct_votes != county_votes:
                    checks.append({"county": county, "contest": contest,
                                   "candidate": option.get("name"), "county_votes": county_votes,
                                   "precinct_votes": precinct_votes})
                shown_option = display_option(contest, option)
                add_vote(state[contest], shown_option, county_votes, option.get("groupResults"))
                add_vote(counties[contest][county], shown_option, county_votes, option.get("groupResults"))
                for row in precinct_rows:
                    name = row.get("name", "").strip()
                    if not name:
                        raise ValueError(f"Missing precinct name in {county} {contest}")
                    add_vote(precincts[contest][f"{county} - {name}"], shown_option,
                             int(row.get("voteCount") or 0), row.get("groupResults"))
    if checks:
        raise ValueError(f"{len(checks)} candidate totals differ from precinct sums: {checks[:5]}")

    statewide_checks = []
    for ballot in payload["results"].get("ballotItems", []):
        contest = contest_key(ballot["name"])
        if contest not in state:
            continue
        reported = sum(int(option.get("voteCount") or 0) for option in ballot.get("ballotOptions", []))
        if reported != state[contest]["total_votes"]:
            statewide_checks.append({"contest": contest, "state_votes": reported,
                                     "county_sum": state[contest]["total_votes"]})
        for option in ballot.get("ballotOptions", []):
            candidate = str(option.get("id", ""))
            county_sum = candidate_totals[(contest, candidate)]
            if county_sum != int(option.get("voteCount") or 0):
                statewide_checks.append({"contest": contest, "candidate": candidate,
                                         "state_votes": option.get("voteCount"), "county_sum": county_sum})
    if statewide_checks:
        raise ValueError(f"Statewide totals differ from county sums: {statewide_checks[:5]}")

    assignments = {"congressional": defaultdict(set), "legislative": defaultdict(set)}
    for county_data in payload["localResults"]:
        county = re.sub(r"\s+County$", "", county_data["name"]).strip()
        for ballot in county_data.get("ballotItems", []):
            contest = contest_key(ballot["name"])
            if not contest or not contest.startswith(("us_house_", "state_senate_")):
                continue
            scope = "congressional" if contest.startswith("us_house_") else "legislative"
            district = contest.rsplit("_", 1)[1]
            for row in (ballot.get("ballotOptions") or [{}])[0].get("precinctResults") or []:
                assignments[scope][f"{county} - {row['name'].strip()}"].add(district)

    expected = set(precincts["governor"])
    for scope, mapping in assignments.items():
        if set(mapping) != expected or any(len(value) != 1 for value in mapping.values()):
            raise ValueError(f"Incomplete or ambiguous 2026 {scope} precinct assignments")
    district_totals = {}
    for contest in STATEWIDE_CONTESTS.values():
        district_totals[contest] = {}
        for scope, mapping in assignments.items():
            grouped = defaultdict(empty_result)
            for precinct_name, row in precincts[contest].items():
                district = next(iter(mapping[precinct_name]))
                combine_result(grouped[district], row)
            if sum(row["total_votes"] for row in grouped.values()) != state[contest]["total_votes"]:
                raise ValueError(f"District allocation failed for {contest} {scope}")
            district_totals[contest][scope] = {
                key: finish(value) for key, value in sorted(grouped.items(), key=lambda item: int(item[0]))
            }

    return {
        "election_date": payload["electionDate"],
        "source_url": SOURCE_URL,
        "source_created_at": payload.get("createdAt"),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "status": "unofficial",
        "validation": {"county_candidate_precinct_mismatches": 0,
                       "statewide_contest_mismatches": 0,
                       "district_assignment_mismatches": 0,
                       "counties": len(payload["localResults"])},
        "precinct_districts": {
            key: {scope: next(iter(mapping[key])) for scope, mapping in assignments.items()}
            for key in sorted(expected)
        },
        "contests": {
            contest: {
                "statewide": finish(state[contest]),
                "counties": {key: finish(value) for key, value in sorted(counties[contest].items())},
                "precincts": {key: finish(value) for key, value in sorted(precincts[contest].items())},
                "reporting": reporting[contest],
                **({"districts": district_totals[contest]} if contest in district_totals else {}),
            }
            for contest in sorted(state)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Downloaded official JSON export")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    source = args.source.read_bytes() if args.source else urllib.request.urlopen(SOURCE_URL, timeout=60).read()
    result = aggregate(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "created_at": result["source_created_at"],
        "contest_count": len(result["contests"]),
        "governor_precincts": len(result["contests"]["governor"]["precincts"]),
        "governor_reporting": result["contests"]["governor"]["reporting"],
        "validation": result["validation"],
    }, indent=2))


if __name__ == "__main__":
    main()
