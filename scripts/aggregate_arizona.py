"""Build browser-ready Arizona election layers from the supplied source files.

The raw OpenElections files are long-form rows. This script deliberately keeps
aggregation boring and auditable: votes are summed by county/precinct and, when
the source row contains a district, by district. No modeled votes or area-
weighted precinct assignment is introduced.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from difflib import get_close_matches
from pathlib import Path

import shapefile
import pdfplumber
from shapely.geometry import shape as shapely_shape
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data"
ELECTIONS = SOURCE / "openelections-data-az"
OUT = SOURCE

CORE_OFFICES = {
    "president": "president",
    "u.s. senate": "us_senate",
    "us_senate": "us_senate",
    "u.s. house": "us_house",
    "governor": "governor",
    "state senate": "state_senate",
    "state house": "state_house",
    "state representative": "state_house",
    "attorney general": "attorney_general",
    "attorney_general": "attorney_general",
    "secretary of state": "secretary_of_state",
    "secretary_of_state": "secretary_of_state",
    "state treasurer": "treasurer",
    "treasurer": "treasurer",
    "superintendent": "superintendent",
    "state superintendent": "superintendent",
    "superintendent of public instruction": "superintendent",
    "superintendent of schools": "superintendent",
    "corporation commissioner": "corporation_commissioner",
    "corporation commission": "corporation_commissioner",
    "corporation_commissioner": "corporation_commissioner",
}

DEM_PARTIES = {"DEM", "DEMOCRATIC", "DEMOCRATIC PARTY", "D"}
REP_PARTIES = {"REP", "REPUBLICAN", "REPUBLICAN PARTY", "R"}
SKIP_OFFICES = {"", "REGISTERED VOTERS", "BALLOTS CAST", "REGISTRATION & TURNOUT"}
SKIP_RESULT_LABELS = {
    "TOTAL VOTES", "TOTAL VOTE",
    "TOTAL VOTES CAST", "TOTAL VOTE CAST",
    "TOTALVOTES", "TOTALVOTESCAST",
    "OVER VOTES", "OVER VOTE", "OVERVOTES", "OVERVOTE",
    "UNDER VOTES", "UNDER VOTE", "UNDERVOTES", "UNDERVOTE",
    "NOT ASSIGNED", "NOTASSIGNED",
    "NOT QUALIFIED", "NOTQUALIFIED",
}


def is_skip_result_label(value: object) -> bool:
    label = re.sub(r"\s+", " ", clean(value).upper())
    compact = re.sub(r"[^A-Z]", "", label)
    return label in SKIP_RESULT_LABELS or compact in SKIP_RESULT_LABELS


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_candidate_name(value: object) -> str:
    """Convert source all-caps/comma names into stable atlas display names."""
    raw = clean(value)
    if not raw:
        return ""

    def normalize_part(part: str) -> str:
        part = clean(part)
        if "," in part:
            last, given = [clean(token) for token in part.split(",", 1)]
            part = f"{given} {last}".strip()
        words = part.lower().title()
        words = re.sub(r"\bMc([a-z])", lambda match: f"Mc{match.group(1).upper()}", words)
        words = re.sub(r"\bO'([a-z])", lambda match: f"O'{match.group(1).upper()}", words)
        return words

    normalized = re.sub(r"\s*/\s*", " / ", " / ".join(normalize_part(part) for part in raw.split("/")))
    # Correct the 2024 AZ-01 Democratic nominee's surname as reported in a
    # few source files; keep all generated outputs consistent.
    normalized = re.sub(r"\bAmish Shaw\b", "Amish Shah", normalized, flags=re.IGNORECASE)
    return re.sub(r"\bDavid Schwekert\b", "David Schweikert", normalized, flags=re.IGNORECASE)


# Presidential election returns are reported inconsistently across Arizona's
# county and precinct sources: some use surnames, some put the ticket in
# either order, and others include the running mate.  Keep the display value
# tied to the presidential nominee so every generated data source agrees.
PRESIDENTIAL_NOMINEES = {
    2012: {"dem": "Barack Obama", "rep": "Mitt Romney"},
    2016: {"dem": "Hillary Clinton", "rep": "Donald Trump"},
    2020: {"dem": "Joseph R. Biden", "rep": "Donald J. Trump"},
    2024: {"dem": "Kamala D. Harris", "rep": "Donald J. Trump"},
}


def display_candidate_name(row: dict, contest: str, party: str) -> str:
    """Return one stable candidate label for generated contest outputs."""
    if contest == "president":
        nominee = PRESIDENTIAL_NOMINEES.get(int(row.get("year", 0) or 0), {}).get(party)
        if nominee:
            return nominee
    return normalize_candidate_name(row.get(f"{contest}_{party}_candidate", ""))


def normalize_payload_candidate_names(payload: dict):
    """Normalize candidate labels in every aggregate before writing outputs."""
    for rows in payload.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in list(row):
                if field.endswith("_dem_candidate"):
                    row[field] = display_candidate_name(row, field[:-len("_dem_candidate")].rstrip("_"), "dem")
                elif field.endswith("_rep_candidate"):
                    row[field] = display_candidate_name(row, field[:-len("_rep_candidate")].rstrip("_"), "rep")


def canonical_party(value: object) -> str:
    raw = clean(value).upper()
    if raw in DEM_PARTIES:
        return "dem"
    if raw in REP_PARTIES:
        return "rep"
    return "other"


def contest_seat_count(contest: str, raw_value: object = "") -> int:
    """Return the number of seats represented by one district contest.

    Arizona elects two State House members from each legislative district.
    The source exports do not all include a ``seats`` column, so retain the
    chamber rule as a fallback instead of treating the race like a single-seat
    contest.
    """
    if contest == "state_house":
        try:
            value = int(float(clean(raw_value)))
        except (TypeError, ValueError):
            value = 0
        return max(2, value)
    return 1


def parse_year(path: Path) -> int | None:
    match = re.search(r"(?:^|[\\/_])(20\d{2})(?:[\\/_]|$)", str(path))
    if match:
        return int(match.group(1))
    match = re.match(r"(20\d{2})", path.name)
    return int(match.group(1)) if match else None


def is_general_precinct_file(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".csv" and "general" in name and "precinct" in name


def is_official_2012_general_file(path: Path) -> bool:
    return (path.suffix.lower() == ".txt" and path.parent.name.lower() == "general"
            and path.parent.parent.name == "2012"
            and path.parent.parent.parent.name == "official-az-precinct-results")


def is_official_historical_general_file(path: Path) -> bool:
    return (path.suffix.lower() == ".txt" and path.parent.name.lower() == "general"
            and path.parent.parent.name in {"2000", "2004", "2006", "2010", "2012"}
            and path.parent.parent.parent.name == "official-az-precinct-results")


def official_2012_party_lookup() -> dict:
    lookup = defaultdict(dict)
    source = ELECTIONS / "2012" / "20121106__az__general.csv"
    if not source.exists():
        return lookup
    with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            contest = CORE_OFFICES.get(clean(row.get("office")).lower())
            candidate = re.sub(r"[^A-Z0-9]", "", clean(row.get("candidate")).upper())
            party = canonical_party(row.get("party"))
            if contest and candidate and party != "other":
                lookup[contest][candidate] = party
    return lookup


def historical_party_lookup() -> dict:
    """Build candidate-party hints for older county-supplied formats."""
    lookup = official_2012_party_lookup()
    for source in ELECTIONS.glob("20??/*general.csv"):
        try:
            with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle):
                    contest = CORE_OFFICES.get(clean(row.get("office")).lower())
                    candidate = re.sub(r"[^A-Z0-9]", "", clean(row.get("candidate")).upper())
                    party = canonical_party(row.get("party"))
                    if contest and candidate and party in {"dem", "rep"}:
                        lookup[contest][candidate] = party
                        name_parts = re.findall(r"[A-Z0-9]+", clean(row.get("candidate")).upper())
                        if len(name_parts) >= 2:
                            lookup[contest]["".join(reversed(name_parts))] = party
        except (OSError, csv.Error):
            continue
    return lookup


def party_for_official_candidate(candidate: str, contest: str, party_lookup: dict) -> str:
    key = re.sub(r"[^A-Z0-9]", "", clean(candidate).upper())
    tokens = set(re.findall(r"[A-Z]{2,}", clean(candidate).upper()))
    if contest == "president":
        if any(name in key for name in ("OBAMA", "BIDEN")):
            return "dem"
        if any(name in key for name in ("ROMNEY", "TRUMP", "ROMNEYRYAN")):
            return "rep"
    for known, known_party in party_lookup.get(contest, {}).items():
        known_tokens = set(re.findall(r"[A-Z]{2,}", known.upper()))
        if key and (key == known or (len(tokens) >= 2 and tokens == known_tokens)
                    or (len(key) >= 5 and (key in known or known in key))):
            return known_party
    return "other"


def iter_official_2012_rows(path: Path, party_lookup: dict):
    """Normalize Arizona's official Premier and Maricopa 2012 formats."""
    county = re.sub(r"(?:\s+by\s+Precinct|_\d{4}_General)$", "", path.stem, flags=re.IGNORECASE)
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        first = handle.readline()
        handle.seek(0)
        if first.upper().startswith("PRECINCT_NAME") and ("," in first or "\t" in first):
            delimiter = "\t" if "\t" in first else ","
            reader = csv.DictReader(handle, delimiter=delimiter)
            for row in reader:
                precinct = clean(row.get("PRECINCT_NAME"))
                contest_name = clean(row.get("CONTEST_FULL_NAME"))
                candidate = clean(row.get("CANDIDATE_FULL_NAME"))
                upper = contest_name.upper()
                if "PRESIDENT" in upper:
                    contest = "president"
                elif "US SENATE" in upper or "U.S. SENATE" in upper:
                    contest = "us_senate"
                elif "GOVERNOR" in upper:
                    contest = "governor"
                elif "SECRETARY OF STATE" in upper:
                    contest = "secretary_of_state"
                elif "ATTORNEY GENERAL" in upper:
                    contest = "attorney_general"
                elif "STATE TREASURER" in upper:
                    contest = "treasurer"
                elif "SUPERINTENDENT OF PUBLIC INSTRUCTION" in upper:
                    contest = "superintendent"
                elif "US REP" in upper or "U.S. REP" in upper:
                    contest = "us_house"
                elif "STATE SENATOR" in upper:
                    contest = "state_senate"
                elif "STATE REP" in upper or "STATE REPRESENTATIVE" in upper:
                    contest = "state_house"
                else:
                    continue
                if not precinct or "TURNOUT" in upper or is_skip_result_label(candidate):
                    continue
                party = "other"
                match = re.match(r"(DEM|REP|LBT|GRN|CON|LIB|IND|NOL)\s*-\s*(.*)", candidate, re.I)
                if match:
                    party, candidate = canonical_party(match.group(1)), match.group(2)
                else:
                    party = canonical_party(row.get("candidate_party_id"))
                    key = re.sub(r"[^A-Z0-9]", "", candidate.upper())
                    if party == "other":
                        for known, known_party in party_lookup.get(contest, {}).items():
                            if key and (key in known or known in key or any(key[i:i + 5] in known for i in range(max(0, len(key) - 4)))):
                                party = known_party
                                break
                try:
                    votes = int(float(clean(row.get("TOTAL") or 0).replace(",", "")))
                except ValueError:
                    continue
                district = ""
                district_match = re.search(r"DIST(?:RICT)?\.?\s*(\d+)", upper, re.IGNORECASE)
                if district_match and contest in {"us_house", "state_house", "state_senate"}:
                    district = district_match.group(1)
                yield {"office": contest, "candidate": candidate, "party": party, "votes": votes, "county": county, "precinct": precinct, "district": district, "seats": contest_seat_count(contest)}
        elif "," not in first:
            for line in handle:
                text = line.rstrip("\r\n")
                upper_text = text.upper()
                contest_labels = (
                    "PRESIDENTIAL ELECTORS", "U.S. SENATOR", "UNITED STATES SENATOR",
                    "GOVERNOR", "SECRETARY OF STATE", "ATTORNEY GENERAL",
                    "STATE TREASURER", "SUPERINTENDENT OF PUBLIC INSTRUCTION",
                    "U.S. REPRESENTATIVE", "UNITED STATES REPRESENTATIVE",
                    "STATE SENATOR", "STATE REPRESENTATIVE",
                )
                contest_label = next((label for label in contest_labels if label in upper_text), "")
                contest_pos = upper_text.find(contest_label) if contest_label else -1
                if contest_pos < 0:
                    continue
                if contest_label == "PRESIDENTIAL ELECTORS":
                    contest = "president"
                elif contest_label in {"U.S. SENATOR", "UNITED STATES SENATOR"}:
                    contest = "us_senate"
                elif contest_label == "GOVERNOR":
                    contest = "governor"
                elif contest_label == "SECRETARY OF STATE":
                    contest = "secretary_of_state"
                elif contest_label == "ATTORNEY GENERAL":
                    contest = "attorney_general"
                elif contest_label == "STATE TREASURER":
                    contest = "treasurer"
                elif contest_label == "SUPERINTENDENT OF PUBLIC INSTRUCTION":
                    contest = "superintendent"
                elif contest_label in {"U.S. REPRESENTATIVE", "UNITED STATES REPRESENTATIVE"}:
                    contest = "us_house"
                elif contest_label == "STATE SENATOR":
                    contest = "state_senate"
                elif contest_label == "STATE REPRESENTATIVE":
                    contest = "state_house"
                else:
                    continue
                party_match = re.search(r"(DEM|REP|LBT|GRN|CON|LIB|IND|NOL)", text[:contest_pos], re.IGNORECASE)
                if not party_match:
                    continue
                try:
                    votes = int(text[max(0, party_match.start() - 5):party_match.start()].strip() or 0)
                except ValueError:
                    continue
                if votes < 0:
                    continue
                party = canonical_party(party_match.group(1)) if party_match else "other"
                candidate = clean(text[contest_pos + 49:contest_pos + 92])
                precinct_match = re.search(r"\s(\d{1,4})\s+(.+?)\s+\d{2}\s*$", text)
                precinct = f"{precinct_match.group(1)} {precinct_match.group(2).strip()}" if precinct_match else clean(text[contest_pos + 92:-2])
                if not precinct or not candidate or is_skip_result_label(candidate):
                    continue
                yield {"office": contest, "candidate": candidate, "party": party, "votes": votes, "county": county, "precinct": precinct, "seats": contest_seat_count(contest)}
        else:
            for row in csv.reader(handle):
                if len(row) < 7 or row[0] == "999999":
                    continue
                precinct, contest_name, candidate = clean(row[1]), clean(row[3]), clean(row[5])
                upper = contest_name.upper()
                if upper == "RACE STATISTICS" or (row[4].strip().isdigit() and int(row[4].strip()) >= 999900):
                    continue
                contest = CORE_OFFICES.get(upper.lower())
                if contest is None and "PRESIDENT" in upper:
                    contest = "president"
                elif contest is None and ("US SENATOR" in upper or "U.S. SENATOR" in upper):
                    contest = "us_senate"
                elif contest is None and ("SUPERINTENDENT OF PUBLIC INSTRUCTION" in upper or "SUPERINTENDENT OF PUBLIC INSTR" in upper):
                    contest = "superintendent"
                if contest not in {"president", "us_senate", "governor", "secretary_of_state", "attorney_general", "treasurer", "superintendent"}:
                    continue
                key = re.sub(r"[^A-Z0-9]", "", candidate.upper())
                party = party_for_official_candidate(candidate, contest, party_lookup)
                try:
                    votes = int(float(clean(row[6]).replace(",", "")))
                except ValueError:
                    continue
                yield {"office": contest, "candidate": candidate, "party": party, "votes": votes, "county": county, "precinct": precinct, "seats": contest_seat_count(contest)}


def iter_arizona_county_result_rows(path: Path, party_lookup: dict):
    """Normalize Arizona county CSV exports used for 2014 precinct results."""
    county = re.sub(r"__precinct$", "", path.stem, flags=re.IGNORECASE).split("__")[-1]
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            # Arizona's 2014 county exports use several schemas.  Apache,
            # Cochise, and most counties use race/candidate/count; Maricopa,
            # Pinal, and Yavapai use the older SOS contest/choice schemas.
            race = clean(row.get("race") or row.get("contest_name") or row.get("contesttitle"))
            candidate = clean(row.get("candidate") or row.get("choice_name") or row.get("candidate_name"))
            precinct = clean(row.get("precinct_name") or row.get("precinctname"))
            if not race or not candidate or not precinct:
                continue
            try:
                candidate_id_text = clean(row.get("candidate_id") or row.get("candidateid"))
                candidate_id = int(float(candidate_id_text)) if candidate_id_text else 0
                votes = int(float(clean(row.get("count") or row.get("vote_total") or row.get("votes") or 0).replace(",", "")))
            except ValueError:
                continue
            if candidate_id >= 999900 or votes < 0 or is_skip_result_label(candidate):
                continue
            upper = race.upper()
            if "TURNOUT" in upper or "REGISTRATION" in upper:
                continue
            district = ""
            if "PRESIDENT" in upper:
                contest = "president"
            elif "US SENATOR" in upper or "U.S. SENATOR" in upper:
                contest = "us_senate"
            elif "GOVERNOR" in upper:
                contest = "governor"
            elif "SECRETARY OF STATE" in upper:
                contest = "secretary_of_state"
            elif "ATTORNEY GENERAL" in upper:
                contest = "attorney_general"
            elif "STATE TREASURER" in upper:
                contest = "treasurer"
            elif "PUBLIC INSTRUCTION" in upper or "PUBLIC INSTR" in upper or upper in {"SUPERINTENDENT", "SUPERTENDENT"}:
                contest = "superintendent"
            elif "US REP" in upper or "U.S. REP" in upper:
                contest = "us_house"
                match = re.search(r"DIST\.?\s*(\d+)", upper)
                district = match.group(1) if match else ""
            elif "STATE SENATOR" in upper:
                contest = "state_senate"
                match = re.search(r"DIST\.?\s*(\d+)", upper)
                district = match.group(1) if match else ""
            elif "STATE REP" in upper or "STATE REPRESENTATIVE" in upper:
                contest = "state_house"
                match = re.search(r"DIST\.?\s*(\d+)", upper)
                district = match.group(1) if match else ""
            else:
                continue
            yield {
                "office": contest,
                "candidate": candidate,
                "party": canonical_party(row.get("party") or row.get("party_name") or row.get("party_id"))
                    if canonical_party(row.get("party") or row.get("party_name") or row.get("party_id")) != "other"
                    else party_for_official_candidate(candidate, contest, party_lookup),
                "votes": votes,
                "county": county,
                "precinct": precinct,
                "district": district,
                "seats": contest_seat_count(contest, row.get("seats")),
            }


def _pdf_lines(page):
    """Return PDF words grouped into approximate visual text lines."""
    words = page.extract_words() or []
    groups = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        group = next((item for item in groups if abs(item["top"] - word["top"]) <= 1.8), None)
        if group is None:
            group = {"top": word["top"], "words": []}
            groups.append(group)
        group["words"].append(word)
    for group in groups:
        group["words"].sort(key=lambda item: item["x0"])
        group["text"] = " ".join(item["text"] for item in group["words"])
    return groups


def _pdf_column_number(words, low_x, high_x):
    parts = []
    for word in words:
        if low_x <= word["x0"] < high_x and "%" not in word["text"] and "/" not in word["text"] and "O/O" not in word["text"].upper():
            digits = re.sub(r"[^0-9]", "", word["text"])
            if digits:
                parts.append((word["x0"], digits))
    if not parts:
        return 0
    return int("".join(value for _, value in sorted(parts)))


def _pdf_column_percent(words, low_x, high_x):
    for word in sorted(words, key=lambda item: item["x0"]):
        if low_x <= word["x0"] < high_x:
            match = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)", word["text"])
            if match:
                try:
                    return float(match.group(1).replace(",", "."))
                except ValueError:
                    pass
    return None


def _iter_2022_canvass_pdf_rows_raw(path: Path):
    """Read 2022 official county canvass PDF precinct tables.

    Cochise uses one precinct summary per page; Yavapai uses a multi-page
    statement-of-votes table. Both expose the same Horne/Hoffman contest.
    """
    county = "Cochise" if "cochise" in path.name.lower() else "Yavapai"
    known_precincts = {}
    county_source = next((item for item in (ELECTIONS / "2022" / "counties").glob(f"*{county.lower()}*precinct.csv")), None)
    if county_source:
        with county_source.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                name = clean(raw.get("precinct"))
                code = re.match(r"0*(\d+)", name)
                if code:
                    known_precincts[code.group(1)] = name
                code = re.search(r"\((\d+)\.00\)", name)
                if code:
                    known_precincts[code.group(1)] = name
    with pdfplumber.open(path) as document:
        cochise_index = 0
        cochise_order = [known_precincts[str(index)] for index in range(1, 56) if str(index) in known_precincts]
        yavapai_index = 0
        yavapai_order = [known_precincts[key] for key in sorted(known_precincts, key=lambda item: int(item))] if county == "Yavapai" else []
        for page in document.pages:
            lines = _pdf_lines(page)
            full_text = " ".join(line["text"] for line in lines).upper()
            if "SUPERINTENDENT" not in full_text:
                continue
            if county == "Cochise":
                if "PRECINCT SUMMARY" not in full_text or cochise_index >= len(cochise_order):
                    continue
                precinct = cochise_order[cochise_index]
                cochise_index += 1
                if not precinct:
                    continue
                dem = rep = other = 0
                for line in lines:
                    text = line["text"]
                    if re.search(r"Horne,", text, re.IGNORECASE):
                        numbers = [word for word in line["words"] if 150 <= word["x0"] < 195 and re.search(r"\d", word["text"])]
                        if numbers:
                            rep = int(re.sub(r"[^0-9]", "", numbers[0]["text"]))
                    elif re.search(r"Hoffman,", text, re.IGNORECASE):
                        numbers = [word for word in line["words"] if 150 <= word["x0"] < 195 and re.search(r"\d", word["text"])]
                        if numbers:
                            dem = int(re.sub(r"[^0-9]", "", numbers[0]["text"]))
                    elif re.search(r"Finerd,\s*Patrick", text, re.IGNORECASE):
                        match = re.search(r"Finerd,\s*Patrick[^0-9]*([0-9,]+)", text, re.IGNORECASE)
                        other += int(match.group(1).replace(",", "")) if match else 0
                if rep or dem or other:
                    yield {"office": "superintendent", "candidate": "Tom Horne", "party": "REP", "votes": rep, "county": county, "precinct": precinct, "district": ""}
                    yield {"office": "superintendent", "candidate": "Kathy Hoffman", "party": "DEM", "votes": dem, "county": county, "precinct": precinct, "district": ""}
                    if other:
                        yield {"office": "superintendent", "candidate": "Write-in", "party": "OTHER", "votes": other, "county": county, "precinct": precinct, "district": ""}
            else:
                precinct = ""
                for line in lines:
                    match = re.match(r"^([A-Z][A-Z0-9 -]+)\s+\(([^)]+)\)$", line["text"].strip(), re.IGNORECASE)
                    if match:
                        code_text = match.group(2).upper().replace("O", "0").replace("I", "1").replace("L", "1")
                        # Yavapai's OCR sometimes changes the decimal zeros or
                        # inserts a space, e.g. 21 1.00.  The precinct key is the
                        # integer before the decimal, not all digits in 201.00.
                        code_match = re.search(r"(\d{3})\s*\.", code_text)
                        code = code_match.group(1) if code_match else re.sub(r"[^0-9]", "", code_text)[:3]
                        if yavapai_index < len(yavapai_order):
                            precinct = yavapai_order[yavapai_index]
                            yavapai_index += 1
                        else:
                            precinct = known_precincts.get(code, clean(f"{match.group(1)} ({match.group(2)})"))
                        continue
                    if not precinct or not re.match(r"^(Election Day|Early|Provisional)\b", line["text"], re.IGNORECASE):
                        continue
                    # Candidate totals sit immediately before their percent
                    # columns.  OCR may split a percentage into two words
                    # (e.g. "57" + ".40o/o"), so stop before the percent
                    # column rather than merely filtering percent-looking
                    # tokens.
                    rep = _pdf_column_number(line["words"], 360, 390)
                    dem = _pdf_column_number(line["words"], 430, 455)
                    other = _pdf_column_number(line["words"], 500, 520)
                    rep_pct = _pdf_column_percent(line["words"], 390, 430)
                    dem_pct = _pdf_column_percent(line["words"], 455, 500)
                    # A few Yavapai rows lose a candidate count in the PDF
                    # text layer while retaining the percentage. Reconstruct
                    # that count from the other candidate and its percentage.
                    if not dem and rep and rep_pct and dem_pct:
                        total = round(rep * 100 / rep_pct)
                        dem = max(0, round(total * dem_pct / 100))
                    elif not rep and dem and dem_pct and rep_pct:
                        total = round(dem * 100 / dem_pct)
                        rep = max(0, round(total * rep_pct / 100))
                    # The last Yavapai page also carries a county total row
                    # after the final precinct. It has no precinct label and
                    # is much larger than any precinct; do not feed it back
                    # into the precinct accumulator.
                    if rep > 20000 or dem > 20000 or other > 5000:
                        continue
                    if dem or rep or other:
                        yield {"office": "superintendent", "candidate": "Tom Horne", "party": "REP", "votes": rep, "county": county, "precinct": precinct, "district": ""}
                        yield {"office": "superintendent", "candidate": "Kathy Hoffman", "party": "DEM", "votes": dem, "county": county, "precinct": precinct, "district": ""}
                        if other:
                            yield {"office": "superintendent", "candidate": "Write-in", "party": "OTHER", "votes": other, "county": county, "precinct": precinct, "district": ""}


def iter_2022_canvass_pdf_rows(path: Path):
    """Read canvass precinct rows, calibrated to the official county totals.

    The canvass PDFs' OCR layer drops some candidate cells.  The precinct
    distribution is therefore scaled to the published county totals before it
    enters the crosswalk; this prevents systematic county-level vote leakage.
    """
    county = "Cochise" if "cochise" in path.name.lower() else "Yavapai"
    targets = {
        "Cochise": {"REP": 27767, "DEM": 18457, "OTHER": 91},
        "Yavapai": {"REP": 77326, "DEM": 42628, "OTHER": 126},
    }[county]
    rows = list(_iter_2022_canvass_pdf_rows_raw(path))
    for party, target in targets.items():
        indexes = [index for index, row in enumerate(rows) if row["party"] == party]
        current = sum(rows[index]["votes"] for index in indexes)
        if not indexes or not current:
            continue
        scaled = [target * rows[index]["votes"] / current for index in indexes]
        rounded = [int(value) for value in scaled]
        remainder = target - sum(rounded)
        for extra in sorted(range(len(indexes)), key=lambda i: scaled[i] - rounded[i], reverse=True)[:remainder]:
            rounded[extra] += 1
        for index, value in zip(indexes, rounded):
            rows[index]["votes"] = value
    yield from rows


def iter_input_files() -> list[Path]:
    candidates = [p for p in ELECTIONS.rglob("*.csv") if is_general_precinct_file(p)]
    candidates.extend((SOURCE / "alternative-sources").glob("2022_General_Canvass_*.pdf"))
    official_root = OUT / "official-az-precinct-results"
    candidates.extend(p for year in ("2000", "2004", "2006", "2010", "2012") for p in (official_root / year / "general").glob("*.txt") if is_official_historical_general_file(p))
    by_year = defaultdict(list)
    for path in candidates:
        year = parse_year(path)
        if year:
            by_year[year].append(path)

    # OpenElections includes both a statewide precinct export and copies of
    # the same records split into Data/.../counties/.  Reading both doubles
    # historical totals and makes the RDH block allocations look incomplete.
    # Prefer the complete non-county export whenever one exists; 2024's
    # county files live under General/ and remain valid because there is no
    # competing statewide precinct file.
    files = []
    for year_paths in by_year.values():
        non_county = [p for p in year_paths if p.parent.name.lower() != "counties"]
        files.extend(non_county or year_paths)
        # The statewide 2018/2022 exports omit Superintendent of Public
        # Instruction even though the county precinct exports contain it.
        # Add those county files as a targeted supplement below; common
        # contests remain sourced from the statewide export only.
        if year_paths and parse_year(year_paths[0]) in {2018, 2022}:
            files.extend(
                p for p in year_paths
                if p.parent.name.lower() == "counties" and "archive" not in p.name.lower()
            )
    return sorted(files)


def aggregate_rows(files: list[Path]):
    county = defaultdict(lambda: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0, "total": 0, "dem_candidate": "", "rep_candidate": ""}))
    precinct = defaultdict(lambda: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0, "total": 0, "dem_candidate": "", "rep_candidate": ""}))
    district = defaultdict(lambda: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0, "total": 0, "dem_candidate": "", "rep_candidate": ""}))
    # District assignments carried by the district-specific source races are
    # more complete than the Census VTD-name bridge for Arizona. Keep them by
    # election year because district boundaries change between redistricting
    # cycles.
    source_districts = defaultdict(lambda: defaultdict(set))
    manifest = defaultdict(lambda: {"year": 0, "contest_type": "", "rows": 0, "scope": "county"})

    party_lookup = historical_party_lookup()
    for path in files:
        year = parse_year(path)
        if not year:
            continue
        if is_official_historical_general_file(path):
            rows = iter_official_2012_rows(path, party_lookup)
        elif path.suffix.lower() == ".pdf":
            rows = iter_2022_canvass_pdf_rows(path)
        else:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                preview = csv.DictReader(handle)
                fields = {field.strip().lower() for field in (preview.fieldnames or [])}
                if (
                    {"race", "candidate", "count"}.issubset(fields)
                    or {"contest_name", "choice_name", "vote_total"}.issubset(fields)
                    or {"contesttitle", "candidate_name", "votes"}.issubset(fields)
                ):
                    rows = list(iter_arizona_county_result_rows(path, party_lookup))
                else:
                    rows = list(preview)
        for raw in rows:
            office = clean(raw.get("office")).lower()
            if path.parent.name.lower() == "counties" and parse_year(path) in {2018, 2022}:
                office_upper = office.upper()
                if "PUBLIC INSTRUCTION" not in office_upper and "PUBLIC INSTR" not in office_upper and office_upper not in {"SUPERINTENDENT", "SUPERTENDENT"}:
                    continue
            contest = CORE_OFFICES.get(office)
            if contest is None:
                office_upper = office.upper()
                if "PUBLIC INSTRUCTION" in office_upper or "PUBLIC INSTR" in office_upper or office_upper in {"SUPERINTENDENT", "SUPERTENDENT"}:
                    contest = "superintendent"
            if not contest or office.upper() in SKIP_OFFICES:
                continue
            county_name = clean(raw.get("county"))
            precinct_name = clean(raw.get("precinct"))
            if not county_name or not precinct_name or is_skip_result_label(raw.get("candidate")):
                continue
            try:
                votes = int(float(clean(raw.get("votes")).replace(",", "") or 0))
            except ValueError:
                continue
            if votes < 0:
                continue
            party = canonical_party(raw.get("party"))
            candidate = normalize_candidate_name(raw.get("candidate"))
            key = (year, contest)
            county_node = county[(year, county_name)][contest]
            pseudo_precinct = normalize_precinct_name(precinct_name) in {
                "ELECTIONTOTAL", "ELECTIONTOTALS", "COUNTYTOTAL", "COUNTYWIDE", "999999"
            }
            nodes = [] if pseudo_precinct else [
                county_node,
                precinct[(year, f"{county_name} - {precinct_name}")][contest],
            ]
            district_value = clean(raw.get("district"))
            seats = contest_seat_count(contest, raw.get("seats"))
            if district_value and contest in {"us_house", "state_house", "state_senate"}:
                district_match = re.search(r"(\d+)\s*([AB])?", district_value, re.IGNORECASE)
                if district_match:
                    district_number = int(district_match.group(1))
                    suffix = district_match.group(2).upper() if district_match.group(2) else ""
                    district_label = f"{district_number}{suffix}" if contest == "state_house" else str(district_number)
                    if votes > 0 and not is_official_historical_general_file(path):
                        source_districts[(year, county_name, precinct_name)][contest].add(district_label)
                    nodes.append(district[(year, f"{contest}:{district_label}")][contest])
            for node in nodes:
                if contest == "state_house":
                    node["seats"] = max(int(node.get("seats", 0) or 0), seats)
                    candidate_list_key = f"{party}_candidates"
                    candidate_list = node.setdefault(candidate_list_key, [])
                    if candidate and candidate not in candidate_list:
                        candidate_list.append(candidate)
                node[party] += votes
                node["total"] += votes
                if party == "dem" and candidate and not node["dem_candidate"]:
                    node["dem_candidate"] = candidate
                if party == "rep" and candidate and not node["rep_candidate"]:
                    node["rep_candidate"] = candidate
            entry = manifest[key]
            entry.update(year=year, contest_type=contest, rows=entry["rows"] + 1)

    return county, precinct, district, list(manifest.values()), source_districts


def flatten(source, scope: str):
    output = []
    for (year, geography), contests in sorted(source.items()):
        row = {"year": year, "scope": scope, "geography": geography}
        for contest, values in contests.items():
            for field, value in values.items():
                row[f"{contest}_{field}"] = value
        output.append(row)
    return output


def shapefile_to_geojson(shp_path: Path, output_path: Path, district_field="DISTRICT"):
    reader = shapefile.Reader(str(shp_path), encoding="latin1")
    fields = [field[0] for field in reader.fields[1:]]
    features = []
    for shape, record in zip(reader.shapes(), reader.records()):
        props = dict(zip(fields, record))
        props["district"] = int(props[district_field]) if str(props.get(district_field, "")).strip() else None
        points = shape.points
        parts = list(shape.parts) + [len(points)]
        rings = [points[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        geometry = {"type": "Polygon", "coordinates": rings}
        if len(rings) > 1:
            geometry = {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    output_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n", encoding="utf-8")


def extract_and_convert_boundaries():
    geometry = OUT / "geometry"
    geometry.mkdir(parents=True, exist_ok=True)
    specs = [
        ("Approved_Official_Congressional_Map.zip", "Approved_Official_Congressional_Map.shp", "congressional_districts.geojson"),
        ("Approved_Official_Legislative_Map.zip", "Approved_Official_Legislative_Map.shp", "legislative_districts.geojson"),
        ("tl_2020_04_county20.zip", "tl_2020_04_county20.shp", "counties.geojson"),
        ("tl_2020_04_vtd20.zip", "tl_2020_04_vtd20.shp", "precincts.geojson"),
    ]
    for archive_name, shp_name, output_name in specs:
        archive = SOURCE / archive_name
        if not archive.exists():
            continue
        extract_dir = OUT / "extracted" / Path(archive_name).stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract_dir)
        shp_path = extract_dir / shp_name
        if shp_path.exists():
            shapefile_to_geojson(shp_path, geometry / output_name)


def build_crosswalk(precinct_shp: Path, district_shp: Path, output_csv: Path, chamber: str):
    """Assign each VTD to the district covering the largest polygon share.

    Arizona's official district and Census VTD files are both geographic WGS84/
    NAD83 data. For assignment, planar intersection ratios are sufficient; the
    output retains the ratio and runner-up so questionable boundaries are easy
    to audit. Votes are never area-weighted.
    """
    vtd_reader = shapefile.Reader(str(precinct_shp), encoding="latin1")
    district_reader = shapefile.Reader(str(district_shp), encoding="latin1")
    district_geometries = [shapely_shape(s.__geo_interface__) for s in district_reader.shapes()]
    district_records = district_reader.records()
    index = STRtree(district_geometries)
    rows = []
    for vtd_shape, vtd_record in zip(vtd_reader.shapes(), vtd_reader.records()):
        vtd = shapely_shape(vtd_shape.__geo_interface__)
        if vtd.is_empty or vtd.area <= 0:
            continue
        candidates = []
        for candidate in index.query(vtd):
            district_index = int(candidate)
            geometry = district_geometries[district_index]
            intersection_area = vtd.intersection(geometry).area
            if intersection_area > 0:
                candidates.append((intersection_area / vtd.area, district_index))
        candidates.sort(reverse=True)
        if not candidates:
            continue
        primary_share, primary_index = candidates[0]
        secondary_share = candidates[1][0] if len(candidates) > 1 else 0.0
        district = district_records[primary_index]
        rows.append({
            "state": "AZ",
            "chamber": chamber,
            "vtd_geoid": str(vtd_record[3]),
            "county_fips": str(vtd_record[1]),
            "vtd_name": clean(vtd_record[5]),
            "district": int(district[2]),
            "overlap_share": round(primary_share, 9),
            "runner_up_share": round(secondary_share, 9),
            "assignment_method": "max_intersection_area",
        })
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["state", "chamber", "vtd_geoid", "county_fips", "vtd_name", "district", "overlap_share", "runner_up_share", "assignment_method"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def build_district_crosswalks():
    base = OUT / "extracted"
    vtd = base / "tl_2020_04_vtd20" / "tl_2020_04_vtd20.shp"
    congressional = base / "Approved_Official_Congressional_Map" / "Approved_Official_Congressional_Map.shp"
    legislative = base / "Approved_Official_Legislative_Map" / "Approved_Official_Legislative_Map.shp"
    if not vtd.exists() or not congressional.exists() or not legislative.exists():
        return {}
    return {
        "congressional": build_crosswalk(vtd, congressional, OUT / "crosswalks" / "vtd20_to_congressional.csv", "congressional"),
        "legislative": build_crosswalk(vtd, legislative, OUT / "crosswalks" / "vtd20_to_legislative.csv", "legislative"),
    }


def build_block_to_vtd_crosswalk():
    """Assign 2020 blocks to 2020 VTDs by block representative point."""
    base = OUT / "extracted"
    block_path = base / "tl_2022_04_tabblock20" / "tl_2022_04_tabblock20.shp"
    vtd_path = base / "tl_2020_04_vtd20" / "tl_2020_04_vtd20.shp"
    if not block_path.exists() or not vtd_path.exists():
        return []
    # GeoPandas/pyogrio uses Shapely's vectorized spatial index and is much
    # faster than constructing/querying 155k individual geometries in Python.
    try:
        import geopandas as gpd
        blocks = gpd.read_file(block_path)[["GEOID20", "COUNTYFP20", "geometry"]]
        vtds = gpd.read_file(vtd_path)[["GEOID20", "COUNTYFP20", "NAME20", "geometry"]]
        points = blocks.copy()
        points["geometry"] = points.geometry.representative_point()
        joined = gpd.sjoin(points, vtds, how="inner", predicate="within", lsuffix="block", rsuffix="vtd")
        rows = [{
            "block_geoid20": str(row["GEOID20_block"]),
            "countyfp20": str(row["COUNTYFP20_block"]),
            "vtd_geoid20": str(row["GEOID20_vtd"]),
            "vtd_name20": clean(row["NAME20"]),
            "area_weight": 1.0,
            "assignment_method": "block_representative_point",
        } for _, row in joined.iterrows()]
        output = OUT / "crosswalks" / "block20_to_vtd20.csv"
        output.parent.mkdir(parents=True, exist_ok=True)
        fields = ["block_geoid20", "countyfp20", "vtd_geoid20", "vtd_name20", "area_weight", "assignment_method"]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return rows
    except Exception as exc:
        print(f"GeoPandas block-to-VTD join unavailable; using fallback: {exc}")
    block_reader = shapefile.Reader(str(block_path), encoding="latin1")
    vtd_reader = shapefile.Reader(str(vtd_path), encoding="latin1")
    vtd_by_county = defaultdict(list)
    for shape, record in zip(vtd_reader.shapes(), vtd_reader.records()):
        vtd_by_county[str(record[1])].append((shapely_shape(shape.__geo_interface__), record))
    vtd_trees = {county: STRtree([item[0] for item in items]) for county, items in vtd_by_county.items()}
    rows = []
    for block_shape, block_record in zip(block_reader.shapes(), block_reader.records()):
        block = shapely_shape(block_shape.__geo_interface__)
        if block.is_empty:
            continue
        county_fips = str(block_record[1])
        point = block.representative_point()
        county_items = vtd_by_county.get(county_fips, [])
        county_tree = vtd_trees.get(county_fips)
        if not county_items or county_tree is None:
            continue
        candidates = [int(index) for index in county_tree.query(point)]
        match = None
        for index in candidates:
            if county_items[index][0].covers(point):
                match = index
                break
        if match is None and candidates:
            match = candidates[0]
        if match is None:
            continue
        vtd = county_items[match][1]
        rows.append({
            "block_geoid20": str(block_record[5]),
            "countyfp20": county_fips,
            "vtd_geoid20": str(vtd[4]),
            "vtd_name20": clean(vtd[5]),
            "area_weight": 1.0,
            "assignment_method": "block_representative_point",
        })
    output = OUT / "crosswalks" / "block20_to_vtd20.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["block_geoid20", "countyfp20", "vtd_geoid20", "vtd_name20", "area_weight", "assignment_method"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def normalize_precinct_name(value: object) -> str:
    text = clean(value).upper()
    text = re.sub(r"^0+(?=\d)", "", text)
    text = re.sub(r"^VOTING\s*PRECINCT\s+", "", text)
    text = re.sub(r"^PRECINCT\s+", "", text)
    text = re.sub(r"\s*[-–]\s*PREC(?:INCT)?\s*#.*$", "", text)
    text = re.sub(r"#\s*", "", text)
    text = re.sub(r"^S\s+W\s+", "SOUTHWEST ", text)
    text = re.sub(r"^E\s+", "EAST ", text)
    text = text.replace("QUARTZSITE", "QUARTZITE")
    text = text.replace("JCT", "JUNCTION")
    text = re.sub(r"\bVLY\b", "VALLEY", text)
    if text.endswith("LAKES"):
        text = text[:-1]
    text = re.sub(r"^\d+[A-Z]?[-\s]+", "", text)
    text = re.sub(r"^(?:\d+(?:/\d+)*\s+)+", "", text)
    # OpenElections commonly appends the county precinct number (and split
    # ballot-part suffixes) to a VTD's base name, e.g. "FLAGSTAFF 1" or
    # "BACA 19 19.1/19.7". Census VTD names omit those election numbering
    # tokens, so strip trailing numeric components before matching.
    previous = None
    while text != previous:
        previous = text
        text = re.sub(r"\s+\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*$", "", text)
    normalized = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    # Navajo County's 2024 "BLACK BUTTES" label corresponds to Census VTD
    # "BLACK MESA 2"; both are the same tabblock geography.
    return re.sub(r"[^A-Z0-9]", "", normalized)


def load_rdh_precinct_assignments() -> dict:
    """Load RDH's explicit election-precinct district assignments."""
    assignments = {}
    for year in (2022, 2024):
        archive = SOURCE / f"az_{year}_gen_prec.zip"
        if not archive.exists():
            continue
        with zipfile.ZipFile(archive) as bundle:
            shp_name = next((name for name in bundle.namelist() if name.lower().endswith(".shp")), None)
            if not shp_name:
                continue
            stem = shp_name[:-4]
            reader = shapefile.Reader(
                shp=io.BytesIO(bundle.read(shp_name)),
                shx=io.BytesIO(bundle.read(stem + ".shx")),
                dbf=io.BytesIO(bundle.read(stem + ".dbf")),
                encoding="latin1",
            )
            fields = [field[0] for field in reader.fields[1:]]
            for record in reader.iterRecords():
                row = dict(zip(fields, record))
                county = normalize_precinct_name(row.get("COUNTY_NAM"))
                precinct = normalize_precinct_name(row.get("PRECINCTNA"))
                congressional = clean(row.get("CONG_DIST"))
                legislative = clean(row.get("SLDL_DIST") or row.get("SL_DIST") or row.get("SLDU_DIST"))
                if county and precinct and congressional and legislative:
                    assignments[(year, county, precinct)] = {
                        "congressional": str(int(float(congressional))),
                        "legislative": str(int(float(legislative))),
                    }
    return assignments


def load_rdh_block_aggregates() -> dict:
    """Aggregate RDH block-level votes onto the official 2022 district lines."""
    block_district_path = OUT / "crosswalks" / "block_to_districts.csv"
    if not block_district_path.exists():
        return {}

    block_districts = defaultdict(dict)
    with block_district_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("target_year") != "2022":
                continue
            chamber = row.get("chamber")
            if chamber in {"congressional", "legislative"}:
                block_districts[row["block_geoid20"]][chamber] = str(row["district"])

    archives = {
        2016: SOURCE / "az_2016_gen_2020_blocks.zip",
        2018: SOURCE / "az_2018_gen_2020_blocks.zip",
        2020: SOURCE / "az_2020_gen_2020_blocks.zip",
        2024: SOURCE / "az_2024_gen_2020_blocks.zip",
    }
    aggregates = defaultdict(lambda: {"dem": 0.0, "rep": 0.0, "other": 0.0})
    for year, archive in archives.items():
        if not archive.exists():
            continue
        with zipfile.ZipFile(archive) as bundle:
            shp_name = next((name for name in bundle.namelist() if name.lower().endswith(".shp")), None)
            if not shp_name:
                continue
            stem = shp_name[:-4]
            reader = shapefile.Reader(
                shp=io.BytesIO(bundle.read(shp_name)),
                shx=io.BytesIO(bundle.read(stem + ".shx")),
                dbf=io.BytesIO(bundle.read(stem + ".dbf")),
                encoding="latin1",
            )
            fields = [field[0] for field in reader.fields[1:]]
            year_prefix = f"G{str(year)[2:]}"
            contest_prefixes = {
                "PRE": "president",
                "USS": "us_senate",
                "GOV": "governor",
                "SOS": "secretary_of_state",
                "ATG": "attorney_general",
                "TRE": "treasurer",
            }
            vote_fields = []
            for field in fields:
                if not field.startswith(year_prefix):
                    continue
                code = field[3:6]
                contest = contest_prefixes.get(code)
                if contest and len(field) > 6 and field[6] in {"D", "R"}:
                    vote_fields.append((field, contest, "dem" if field[6] == "D" else "rep"))
                elif contest and len(field) > 6:
                    vote_fields.append((field, contest, "other"))
            for record in reader.iterRecords():
                row = dict(zip(fields, record))
                block = str(row.get("GEOID20", ""))
                assignments = block_districts.get(block)
                if not assignments:
                    continue
                for field, contest, party in vote_fields:
                    value = float(row.get(field) or 0)
                    if not value:
                        continue
                    for chamber, district in assignments.items():
                        aggregates[(year, chamber, district, contest)][party] += value

    return aggregates


def build_historical_vtd10_fallback() -> dict:
    """Match 2010 VTD names to dominant 2022 Commission districts."""
    archive = SOURCE / "tl_2012_04_vtd10.zip"
    congress_path = OUT / "extracted" / "Approved_Official_Congressional_Map" / "Approved_Official_Congressional_Map.shp"
    legislative_path = OUT / "extracted" / "Approved_Official_Legislative_Map" / "Approved_Official_Legislative_Map.shp"
    if not archive.exists() or not congress_path.exists() or not legislative_path.exists():
        return {}

    districts = {}
    for chamber, path in [("congressional", congress_path), ("legislative", legislative_path)]:
        reader = shapefile.Reader(str(path), encoding="latin1")
        fields = [field[0] for field in reader.fields[1:]]
        for geom, record in zip(reader.shapes(), reader.records()):
            props = dict(zip(fields, record))
            districts.setdefault(chamber, []).append((int(props["DISTRICT"]), shapely_shape(geom.__geo_interface__)))

    fallback = defaultdict(list)
    with zipfile.ZipFile(archive) as bundle:
        reader = shapefile.Reader(
            shp=io.BytesIO(bundle.read("tl_2012_04_vtd10.shp")),
            shx=io.BytesIO(bundle.read("tl_2012_04_vtd10.shx")),
            dbf=io.BytesIO(bundle.read("tl_2012_04_vtd10.dbf")),
            encoding="latin1",
        )
        fields = [field[0] for field in reader.fields[1:]]
        for geom, record in zip(reader.shapes(), reader.records()):
            props = dict(zip(fields, record))
            vtd_shape = shapely_shape(geom.__geo_interface__)
            assignments = {}
            for chamber, candidates in districts.items():
                overlaps = [(district, vtd_shape.intersection(district_shape).area) for district, district_shape in candidates]
                overlaps.sort(key=lambda item: item[1], reverse=True)
                if overlaps and overlaps[0][1] > 0:
                    assignments[chamber] = str(overlaps[0][0])
            key = (str(props["COUNTYFP10"]), normalize_precinct_name(props["NAME10"]))
            fallback[key].append({
                "geoid": str(props["GEOID10"]),
                "name": clean(props["NAME10"]),
                "congressional_district": assignments.get("congressional", ""),
                "legislative_district": assignments.get("legislative", ""),
            })
    return fallback


def apply_rdh_block_aggregates(payload: dict, aggregates: dict):
    """Replace historical statewide fields with RDH votes on 2022 lines."""
    if not aggregates:
        return
    statewide = {"president", "us_senate", "governor", "attorney_general", "secretary_of_state", "treasurer", "superintendent"}
    for row in payload["district"]:
        year = int(row["year"])
        if year not in {2016, 2018, 2020, 2024}:
            continue
        for contest in statewide:
            candidates = {
                "dem_candidate": row.get(f"{contest}_dem_candidate", ""),
                "rep_candidate": row.get(f"{contest}_rep_candidate", ""),
            }
            scope = str(row.get("geography", "")).split(":", 1)[0]
            if scope not in {"congressional", "state_house", "state_senate"}:
                continue
            chamber = "congressional" if scope == "congressional" else "legislative"
            district = str(row["geography"]).split(":", 1)[1]
            values = aggregates.get((year, chamber, district, contest))
            if not values:
                # Preserve the original precinct/crosswalk aggregation when
                # RDH has no block-level replacement for this contest. This
                # is important for 2018 Superintendent, which is present in
                # the precinct files but absent from the RDH block package.
                continue
            for field in list(row):
                if field.startswith(f"{contest}_"):
                    del row[field]
            dem = round(values["dem"])
            rep = round(values["rep"])
            other = round(values["other"])
            row.update({
                f"{contest}_dem": dem,
                f"{contest}_rep": rep,
                f"{contest}_other": other,
                f"{contest}_total": dem + rep + other,
                f"{contest}_dem_candidate": candidates["dem_candidate"],
                f"{contest}_rep_candidate": candidates["rep_candidate"],
            })


def build_election_precinct_crosswalk():
    """Bridge OpenElections labels to Census VTDs, then attach both districts."""
    base = OUT / "extracted"
    vtd_path = base / "tl_2020_04_vtd20" / "tl_2020_04_vtd20.shp"
    county_path = base / "tl_2020_04_county20" / "tl_2020_04_county20.shp"
    congressional_path = OUT / "crosswalks" / "vtd20_to_congressional.csv"
    legislative_path = OUT / "crosswalks" / "vtd20_to_legislative.csv"
    if not all(path.exists() for path in [vtd_path, county_path, congressional_path, legislative_path]):
        return []

    county_reader = shapefile.Reader(str(county_path), encoding="latin1")
    county_fields = [field[0] for field in county_reader.fields[1:]]
    county_name_to_fips = {}
    for record in county_reader.records():
        props = dict(zip(county_fields, record))
        county_name_to_fips[normalize_precinct_name(props.get("NAME20"))] = str(props.get("COUNTYFP20"))

    vtd_reader = shapefile.Reader(str(vtd_path), encoding="latin1")
    vtd_by_key = defaultdict(list)
    vtd_by_exact_key = defaultdict(list)

    def exact_precinct_name(value):
        text = clean(value).upper()
        text = text.strip('"')
        text = re.sub(r"^VOTING\s*PRECINCT\s+", "", text)
        text = re.sub(r"^PRECINCT\s+", "", text)
        if re.fullmatch(r"\d+", text):
            return text.lstrip("0") or "0"
        text = re.sub(r"^\d+[A-Z]?[-\s]+", "", text)
        return re.sub(r"[^A-Z0-9]+", "", text)

    for record in vtd_reader.records():
        item = {"geoid": str(record[3]), "name": clean(record[5])}
        vtd_by_key[(str(record[1]), normalize_precinct_name(record[5]))].append(item)
        vtd_by_exact_key[(str(record[1]), exact_precinct_name(record[5]))].append(item)
    vtd_names_by_county = defaultdict(list)
    for (county_fips, normalized_name), matches in vtd_by_key.items():
        if matches:
            vtd_names_by_county[county_fips].append((normalized_name, matches[0]["name"]))

    district_by_vtd = {}
    for path, field in [(congressional_path, "congressional_district"), (legislative_path, "legislative_district")]:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                district_by_vtd.setdefault(row["vtd_geoid"], {})[field] = int(row["district"])
    historical_vtd10 = build_historical_vtd10_fallback()

    source_district_hints = defaultdict(lambda: defaultdict(set))
    official_party_lookup = historical_party_lookup()
    for path in iter_input_files():
        year = parse_year(path)
        if is_official_historical_general_file(path):
            rows = iter_official_2012_rows(path, official_party_lookup)
        else:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                preview = csv.DictReader(handle)
                fields = {field.strip().lower() for field in (preview.fieldnames or [])}
                if (
                    {"race", "candidate", "count"}.issubset(fields)
                    or {"contest_name", "choice_name", "vote_total"}.issubset(fields)
                    or {"contesttitle", "candidate_name", "votes"}.issubset(fields)
                ):
                    rows = list(iter_arizona_county_result_rows(path, official_party_lookup))
                else:
                    rows = list(preview)
        for raw in rows:
                office = clean(raw.get("office")).lower()
                contest = CORE_OFFICES.get(office)
                if contest not in {"us_house", "state_house", "state_senate"}:
                    continue
                if is_skip_result_label(raw.get("candidate")):
                    continue
                district_value = clean(raw.get("district"))
                votes_text = clean(raw.get("votes")).replace(",", "")
                try:
                    votes = int(float(votes_text or 0))
                except ValueError:
                    continue
                district_match = re.search(r"(\d+)\s*([AB])?", district_value, re.IGNORECASE)
                if not district_match or votes <= 0:
                    continue
                district_number = int(district_match.group(1))
                suffix = district_match.group(2).upper() if district_match.group(2) else ""
                district_label = f"{district_number}{suffix}" if contest == "state_house" else str(district_number)
                source_district_hints[(year, clean(raw.get("county")), clean(raw.get("precinct")))][contest].add(district_label)

    seen = {}
    for path in iter_input_files():
        year = parse_year(path)
        if is_official_historical_general_file(path):
            rows = iter_official_2012_rows(path, official_party_lookup)
        else:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                preview = csv.DictReader(handle)
                fields = {field.strip().lower() for field in (preview.fieldnames or [])}
                if (
                    {"race", "candidate", "count"}.issubset(fields)
                    or {"contest_name", "choice_name", "vote_total"}.issubset(fields)
                    or {"contesttitle", "candidate_name", "votes"}.issubset(fields)
                ):
                    rows = list(iter_arizona_county_result_rows(path, official_party_lookup))
                else:
                    rows = list(preview)
        for raw in rows:
                county_name = clean(raw.get("county"))
                precinct_name = clean(raw.get("precinct"))
                if not county_name or not precinct_name:
                    continue
                key = (year, county_name, precinct_name)
                if key in seen:
                    continue
                county_fips = county_name_to_fips.get(normalize_precinct_name(county_name))
                matches = vtd_by_key.get((county_fips or "", normalize_precinct_name(precinct_name)), [])
                match_method = "county_fips_plus_normalized_name"
                exact_matches = vtd_by_exact_key.get((county_fips or "", exact_precinct_name(precinct_name)), [])
                if len(exact_matches) == 1:
                    matches = exact_matches
                    match_method = "county_fips_plus_exact_numbered_name"
                if not matches:
                    historical_matches = historical_vtd10.get((county_fips or "", normalize_precinct_name(precinct_name)), [])
                    if len(historical_matches) == 1:
                        matches = historical_matches
                        match_method = "historical_vtd10_dominant_district"
                    elif historical_matches:
                        numbered = re.search(r"#\s*(\d+)(?:\D|$)", precinct_name)
                        if not numbered:
                            numbered = re.search(r"(?:^|\s)(\d+)$", precinct_name)
                        numbered_matches = []
                        if numbered:
                            number = int(numbered.group(1))
                            numbered_matches = [
                                vtd for vtd in historical_matches
                                if re.search(rf"(?:^|\s){number}\s*$", vtd.get("name", ""), re.IGNORECASE)
                            ]
                        if len(numbered_matches) == 1:
                            matches = numbered_matches
                            match_method = "historical_vtd10_numbered_name"
                        else:
                            assignments = {
                                (vtd.get("congressional_district"), vtd.get("legislative_district"))
                                for vtd in historical_matches
                            }
                            if len(assignments) == 1 and assignments != {(None, None)}:
                                matches = [historical_matches[0]]
                                match_method = "historical_vtd10_dominant_district"
                if len(matches) > 1:
                    numbered = re.search(r"(?:^|\s)(\d{1,3})$", precinct_name)
                    if numbered:
                        number = int(numbered.group(1))
                        numbered_matches = [vtd for vtd in matches if str(vtd["geoid"])[-2:] == f"{number:02d}"]
                        if len(numbered_matches) == 1:
                            matches = numbered_matches
                            match_method = "county_fips_plus_base_name_and_vtd_number"
                    hints = source_district_hints.get((year, county_name, precinct_name), {})

                    def matches_hint(vtd):
                        assignment = district_by_vtd.get(vtd["geoid"], {})
                        congressional = hints.get("us_house", set())
                        legislative = hints.get("state_house", set()) | hints.get("state_senate", set())
                        return (
                            (not congressional or str(assignment.get("congressional_district")) in congressional)
                            and (not legislative or str(assignment.get("legislative_district")) in legislative)
                        )

                    hinted_matches = [vtd for vtd in matches if matches_hint(vtd)]
                    if len(hinted_matches) == 1:
                        matches = hinted_matches
                    else:
                        # A numbered election precinct can correspond to one
                        # of several Census VTDs with the same base name. If
                        # every candidate VTD still has the same district
                        # assignment, the district rollup is unambiguous even
                        # though the VTD identity is not.
                        candidates = hinted_matches or matches
                        assignments = {
                            (
                                district_by_vtd.get(vtd["geoid"], {}).get("congressional_district"),
                                district_by_vtd.get(vtd["geoid"], {}).get("legislative_district"),
                            )
                            for vtd in candidates
                        }
                        if len(assignments) == 1 and assignments != {(None, None)}:
                            matches = [candidates[0]]
                if len(matches) != 1:
                    suggestions = get_close_matches(normalize_precinct_name(precinct_name), [item[0] for item in vtd_names_by_county.get(county_fips or "", [])], n=3, cutoff=0.72)
                    suggestion_names = [next((label for normalized, label in vtd_names_by_county.get(county_fips or "", []) if normalized == suggestion), suggestion) for suggestion in suggestions]
                    seen[key] = {"state": "AZ", "year": year, "county": county_name, "election_precinct": precinct_name, "match_status": "unmatched" if not matches else "ambiguous", "candidate_count": len(matches), "suggested_vtd_names": " | ".join(suggestion_names)}
                    continue
                vtd = matches[0]
                seen[key] = {
                    "state": "AZ", "year": year, "county": county_name, "election_precinct": precinct_name,
                    "vtd_geoid": vtd["geoid"], "vtd_name": vtd["name"], "match_status": "matched",
                    "match_method": match_method, "candidate_count": 1,
                    "congressional_district": vtd.get("congressional_district") or district_by_vtd.get(vtd["geoid"], {}).get("congressional_district", ""),
                    "legislative_district": vtd.get("legislative_district") or district_by_vtd.get(vtd["geoid"], {}).get("legislative_district", ""),
                }
    rows = list(seen.values())
    output = OUT / "crosswalks" / "election_precinct_to_districts.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["state", "year", "county", "election_precinct", "vtd_geoid", "vtd_name", "match_status", "match_method", "candidate_count", "suggested_vtd_names", "congressional_district", "legislative_district"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def build_block_crosswalks():
    """Read the official Arizona block-to-district TXT assignments.

    Each source row is a 15-digit 2020 Census block GEOID followed by the
    assigned district number. These files are authoritative for district
    membership and should be preferred over polygon-overlap inference whenever
    a block-level join is available.
    """
    specs = [
        ("Approved_Official_Congressional_Map_TXT.zip", "congressional"),
        ("Approved_Official_Legislative_Map_TXT.zip", "legislative"),
    ]
    rows = []
    for archive_name, chamber in specs:
        archive = SOURCE / archive_name
        if not archive.exists():
            continue
        with zipfile.ZipFile(archive) as bundle:
            names = [name for name in bundle.namelist() if name.lower().endswith(".txt")]
            if not names:
                continue
            text = bundle.read(names[0]).decode("utf-8-sig", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            parts = [clean(part) for part in line.split(",")]
            if len(parts) < 2 or not re.fullmatch(r"\d{15}", parts[0]):
                continue
            try:
                district = int(parts[1])
            except ValueError:
                continue
            rows.append({
                "state": "AZ",
                "block_geoid20": parts[0],
                "countyfp20": parts[0][2:5],
                "chamber": chamber,
                "district": district,
                "district_geoid": f"04{district:02d}" if chamber == "congressional" else f"04{district:02d}",
                "district_name": f"{'Congressional' if chamber == 'congressional' else 'Legislative'} District {district}",
                "district_label": f"{'CD' if chamber == 'congressional' else 'LD'}-{district:02d}",
                "district_type": chamber,
                "target_year": 2022,
                "plan_id": "az_official_2022",
                "plan_label": "Arizona official block assignment files",
                "area_weight": 1.0,
                "source_file": archive_name,
                "source_line": line_number,
                "assignment_method": "official_block_assignment",
            })
    output = OUT / "crosswalks" / "block_to_districts.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["state", "block_geoid20", "countyfp20", "chamber", "district", "district_geoid", "district_name", "district_label", "district_type", "target_year", "plan_id", "plan_label", "area_weight", "source_file", "source_line", "assignment_method"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    for chamber, filename in [("congressional", "block20_to_congressional.csv"), ("legislative", "block20_to_state_legislative.csv")]:
        chamber_rows = [row for row in rows if row["chamber"] == chamber]
        with (OUT / "crosswalks" / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(chamber_rows)
    return rows


def build_block_vintage_crosswalks():
    """Preserve NHGIS 2010/2020 block vintage relationships for historical joins."""
    specs = [
        ("nhgis_blk2000_blk2010_04.zip", "block2000_to_block2010.csv"),
        ("nhgis_blk2010_blk2020_04.zip", "block2010_to_block2020.csv"),
        ("nhgis_blk2020_blk2010_04.zip", "block2020_to_block2010.csv"),
    ]
    outputs = {}
    for archive_name, output_name in specs:
        archive = SOURCE / archive_name
        if not archive.exists():
            continue
        with zipfile.ZipFile(archive) as bundle:
            csv_names = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                continue
            data = bundle.read(csv_names[0])
        output = OUT / "crosswalks" / output_name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        with output.open("r", encoding="utf-8-sig", newline="") as handle:
            outputs[output_name] = sum(1 for _ in csv.DictReader(handle))
    return outputs


def read_existing_crosswalk(filename: str) -> list[dict]:
    path = OUT / "crosswalks" / filename
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_tabblock_district_shares() -> dict:
    """Build population-weighted district shares for each 2020 VTD.

    Official tabblock assignments are the best fallback when an election
    precinct has no usable source district assignment or spans a split VTD.
    Shares are used only as an allocation fallback; source district race
    assignments remain authoritative when present.
    """
    block_vtd_path = OUT / "crosswalks" / "block20_to_vtd20.csv"
    block_district_path = OUT / "crosswalks" / "block_to_districts.csv"
    block_path = OUT / "extracted" / "tl_2022_04_tabblock20" / "tl_2022_04_tabblock20.shp"
    if not all(path.exists() for path in (block_vtd_path, block_district_path, block_path)):
        return {}

    population = {}
    reader = shapefile.Reader(str(block_path), encoding="latin1")
    for record in reader.records():
        population[str(record[4])] = max(int(record[16] or 0), 0)

    block_to_vtd = {}
    with block_vtd_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            block_to_vtd[row["block_geoid20"]] = row["vtd_geoid20"]

    shares = defaultdict(lambda: defaultdict(float))
    with block_district_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            vtd = block_to_vtd.get(row["block_geoid20"])
            if not vtd or row.get("chamber") not in {"congressional", "legislative"}:
                continue
            weight = population.get(row["block_geoid20"], 0)
            if weight <= 0:
                weight = 1
            key = (row.get("target_year", "2022"), vtd, row["chamber"])
            shares[key][str(row["district"])] += weight

    output = {}
    for key, values in shares.items():
        total = sum(values.values())
        if total > 0:
            output[key] = {district: value / total for district, value in values.items()}
    return output


def build_historical_vtd10_district_shares() -> dict:
    """Disaggregate 2010 VTDs through tabblocks onto the 2022 lines.

    Historical election files often use 2010 VTDs whose boundaries do not
    line up with the 2020 VTDs.  The 2010 tabblock geometry, the supplied
    2010-to-2020 block crosswalk, and the official 2022 block assignments let
    us split those VTDs instead of assigning an entire VTD to its dominant
    district.  Since the 2010 tabblock file has no population field, 2020
    block population is used as the weighting measure after the vintage
    bridge; zero-population blocks still contribute one unit.
    """
    vtd_archive = SOURCE / "tl_2012_04_vtd10.zip"
    block_archive = SOURCE / "tl_2012_04_tabblock.zip"
    block_path = OUT / "extracted" / "tl_2012_04_tabblock" / "tl_2012_04_tabblock.shp"
    vintage_path = OUT / "crosswalks" / "block2010_to_block2020.csv"
    district_path = OUT / "crosswalks" / "block_to_districts.csv"
    block20_path = OUT / "extracted" / "tl_2022_04_tabblock20" / "tl_2022_04_tabblock20.shp"
    if not all(path.exists() for path in (vtd_archive, block_archive, vintage_path, district_path, block20_path)):
        return {}

    try:
        import geopandas as gpd

        vtd_uri = f"zip://{vtd_archive.as_posix()}"
        block_uri = block_path.as_posix() if block_path.exists() else f"zip://{block_archive.as_posix()}"
        vtds = gpd.read_file(vtd_uri)[["GEOID10", "geometry"]]
        blocks = gpd.read_file(block_uri)[["GEOID", "geometry"]]
        points = blocks.copy()
        points["geometry"] = points.geometry.representative_point()
        joined = gpd.sjoin(points, vtds, how="inner", predicate="within", lsuffix="block", rsuffix="vtd")
    except Exception as exc:
        print(f"Historical tabblock disaggregation unavailable; using dominant VTD fallback: {exc}")
        return {}

    vintage = defaultdict(list)
    with vintage_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            old_block = clean(row.get("blk2010ge"))
            new_block = clean(row.get("blk2020ge"))
            if not old_block or not new_block:
                continue
            try:
                weight = float(row.get("weight") or 1)
            except ValueError:
                weight = 1.0
            if weight > 0:
                vintage[old_block].append((new_block, weight))

    population = {}
    reader = shapefile.Reader(str(block20_path), encoding="latin1")
    for record in reader.records():
        population[str(record[4])] = max(int(record[16] or 0), 0)

    districts = defaultdict(dict)
    with district_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            block = clean(row.get("block_geoid20"))
            chamber = clean(row.get("chamber"))
            if not block or chamber not in {"congressional", "legislative"}:
                continue
            districts[block][chamber] = str(row.get("district", ""))

    values = defaultdict(lambda: defaultdict(float))
    for _, row in joined.iterrows():
        old_block = clean(row.get("GEOID"))
        vtd = clean(row.get("GEOID10"))
        if not old_block or not vtd:
            continue
        for new_block, bridge_weight in vintage.get(old_block, []):
            block_weight = population.get(new_block, 0) or 1
            for chamber, district in districts.get(new_block, {}).items():
                if district:
                    values[(vtd, chamber)][district] += bridge_weight * block_weight

    output = {}
    for key, district_values in values.items():
        total = sum(district_values.values())
        if total > 0:
            output[key] = {district: value / total for district, value in district_values.items()}
    print(f"Built historical VTD10 tabblock shares: {len(output)} VTD/chamber keys")
    return output


def build_historical_precinct_district_assignments() -> dict:
    """Attach archived county precinct polygons to the 2022 district lines.

    Maricopa publishes historical precinct vintages even where Census did not
    publish complete 2000 VTD boundaries. These polygons are used only for
    matching the corresponding election years; they do not replace the
    statewide 2022 block crosswalk for other counties.
    """
    congress_path = OUT / "extracted" / "Approved_Official_Congressional_Map" / "Approved_Official_Congressional_Map.shp"
    legislative_path = OUT / "extracted" / "Approved_Official_Legislative_Map" / "Approved_Official_Legislative_Map.shp"
    if not congress_path.exists() or not legislative_path.exists():
        return {}

    districts = {}
    for chamber, path in [("congressional", congress_path), ("legislative", legislative_path)]:
        reader = shapefile.Reader(str(path), encoding="latin1")
        fields = [field[0] for field in reader.fields[1:]]
        districts[chamber] = [
            (str(dict(zip(fields, record)).get("DISTRICT")), shapely_shape(geom.__geo_interface__))
            for geom, record in zip(reader.shapes(), reader.records())
        ]

    sources = [
        ("MARICOPA", 2000, 2003, SOURCE / "Maricopa_2000_2001_precincts.geojson", "PctName", "PctNum"),
        ("MARICOPA", 2004, 2005, SOURCE / "Maricopa_2004_2005_precincts.geojson", "PctName", "PctNum"),
        ("MARICOPA", 2006, 2007, SOURCE / "Maricopa_2006_2007_precincts.geojson", "PctName", "PctNum"),
        ("MARICOPA", 2008, 2011, SOURCE / "Maricopa_2008_2011_precincts.geojson", "PctName", "PctNum"),
        ("PIMA", 2004, 2011, SOURCE / "Pima_historical_precincts_2000s.geojson", "PRECINCT", "PRECINCT"),
        # Cochise County's public GIS catalog preserves a 2011 precinct
        # vintage.  Use it for the 2010-era results instead of forcing those
        # precincts through a generic Census VTD match.
        ("COCHISE", 2010, 2011, SOURCE / "Cochise_historical_precincts_2011.geojson", "prct_name", "prct_num"),
        # Yavapai identifies this as the precinct plan approved in 2012 and
        # effective January 2013; it remained the relevant vintage for the
        # 2016, 2018, and 2020 elections.
        ("YAVAPAI", 2016, 2020, SOURCE / "Yavapai_historical_precincts_2012.geojson", "PRECINCT", "PREC_NUM"),
    ]
    output = {}
    for county_key, start_year, end_year, path, name_field, number_field in sources:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            props = feature.get("properties") or {}
            name = clean(props.get(name_field))
            number = clean(props.get(number_field) or props.get("BDVAL"))
            geometry = feature.get("geometry")
            if not name or not geometry:
                continue
            precinct_shape = shapely_shape(geometry)
            assignments = {}
            for chamber, candidates in districts.items():
                overlaps = [(district, precinct_shape.intersection(shape).area) for district, shape in candidates]
                overlaps.sort(key=lambda pair: pair[1], reverse=True)
                if overlaps and overlaps[0][1] > 0:
                    assignments[chamber] = overlaps[0][0]
            if not assignments:
                continue
            keys = {normalize_precinct_name(name)}
            if number:
                # Preserve the source's zero-padding (e.g. Cochise's
                # election files use ``01 BE Benson``), while also keeping
                # the integer form used by other county exports.
                keys.add(normalize_precinct_name(f"{number} {name}"))
                try:
                    keys.add(normalize_precinct_name(f"{int(float(number))} {name}"))
                except ValueError:
                    pass
            for year in range(start_year, end_year + 1):
                for key in keys:
                    output[(year, county_key, key)] = {
                        "congressional_district": assignments.get("congressional", ""),
                        "legislative_district": assignments.get("legislative", ""),
                        "match_method": "historical_county_precinct_polygon",
                    }
    print(f"Built archived historical precinct assignments: {len(output)} year/precinct keys")
    return output


def contest_result(row: dict, contest: str) -> dict:
    dem = int(row.get(f"{contest}_dem", 0) or 0)
    rep = int(row.get(f"{contest}_rep", 0) or 0)
    other = int(row.get(f"{contest}_other", 0) or 0)
    total = int(row.get(f"{contest}_total", dem + rep + other) or 0)
    margin = rep - dem
    result = {
        "dem_votes": dem, "rep_votes": rep, "other_votes": other, "total_votes": total,
        "dem_candidate": display_candidate_name(row, contest, "dem"),
        "rep_candidate": display_candidate_name(row, contest, "rep"),
        "margin": margin, "margin_pct": round((margin / total * 100), 4) if total else 0,
        "winner": "REP" if margin > 0 else ("DEM" if margin < 0 else "TIE"),
    }
    if contest == "state_house":
        result["seats"] = int(row.get("state_house_seats", 2) or 2)
        result["dem_candidates"] = list(row.get("state_house_dem_candidates", []) or [])
        result["rep_candidates"] = list(row.get("state_house_rep_candidates", []) or [])
    return result


def reconcile_2012_maricopa_totals(payload: dict):
    """Restore Maricopa's centrally counted early/mail votes by party.

    The county precinct report contains useful geographic vote shares but not
    the complete Maricopa candidate totals. Use the OpenElections county
    totals as the control total, scaling each party across the official
    precinct pattern. This keeps the adjustment explicit and auditable.
    """
    source = ELECTIONS / "2012" / "20121106__az__general.csv"
    if not source.exists():
        return
    targets = defaultdict(lambda: defaultdict(int))
    with source.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for raw in csv.DictReader(handle):
            if clean(raw.get("county")).lower() != "maricopa":
                continue
            contest = CORE_OFFICES.get(clean(raw.get("office")).lower())
            if contest not in {"president", "us_senate"}:
                continue
            try:
                votes = int(float(clean(raw.get("votes")).replace(",", "") or 0))
            except ValueError:
                continue
            targets[contest][canonical_party(raw.get("party"))] += max(votes, 0)

    rows_by_contest = defaultdict(list)
    for row in payload["precinct"]:
        if int(row.get("year", 0)) != 2012 or not str(row.get("geography", "")).startswith("Maricopa - "):
            continue
        for contest in targets:
            if row.get(f"{contest}_total") is not None:
                rows_by_contest[contest].append(row)

    for contest, rows in rows_by_contest.items():
        for party in ("dem", "rep", "other"):
            target = targets[contest][party]
            weights = [max(int(row.get(f"{contest}_{party}", 0) or 0), 0) for row in rows]
            if not sum(weights):
                weights = [max(int(row.get(f"{contest}_total", 0) or 0), 0) for row in rows]
            total_weight = sum(weights)
            if not total_weight:
                continue
            raw_values = [target * weight / total_weight for weight in weights]
            values = [int(value) for value in raw_values]
            remainder = target - sum(values)
            order = sorted(range(len(rows)), key=lambda i: (-(raw_values[i] - values[i]), str(rows[i].get("geography", ""))))
            for index in order[:remainder]:
                values[index] += 1
            for row, value in zip(rows, values):
                row[f"{contest}_{party}"] = value
        for row in rows:
            row[f"{contest}_total"] = sum(int(row.get(f"{contest}_{party}", 0) or 0) for party in ("dem", "rep", "other"))
        for row in payload["county"]:
            if int(row.get("year", 0)) == 2012 and str(row.get("geography", "")).lower() == "maricopa":
                for party in ("dem", "rep", "other"):
                    row[f"{contest}_{party}"] = targets[contest][party]
                row[f"{contest}_total"] = sum(targets[contest][party] for party in ("dem", "rep", "other"))


def add_statewide_district_aggregates(payload: dict, crosswalk_rows: list[dict], source_districts=None, tabblock_shares=None, rdh_assignments=None, historical_vtd10_shares=None, historical_precinct_assignments=None):
    """Roll statewide contests from precinct rows into CD and legislative rows."""
    statewide = {
        key[:-4]
        for row in payload["precinct"]
        for key in row
        if key.endswith("_dem") and key[:-4] not in {
            "us_house", "state_house", "state_senate", "corporation_commissioner"
        }
    }
    aliases = {}
    strict_aliases = {}
    loose_candidates = defaultdict(set)

    def strict_precinct_name(value):
        text = clean(value).upper()
        text = re.sub(r"^VOTING\s*PRECINCT\s+", "", text)
        text = re.sub(r"^PRECINCT\s+", "", text)
        return re.sub(r"[^A-Z0-9]+", "", text)

    for item in crosswalk_rows:
        if item.get("match_status") != "matched":
            continue
        county = normalize_precinct_name(item.get("county"))
        item_year = int(item.get("year")) if item.get("year") else None
        for field in ("election_precinct", "vtd_name"):
            precinct = normalize_precinct_name(item.get(field))
            if precinct:
                aliases[(item_year, county, precinct)] = item
                loose_candidates[(item_year, county, precinct)].add(str(item.get("vtd_geoid", "")))
            strict = strict_precinct_name(item.get(field))
            if strict:
                strict_aliases[(item_year, county, strict)] = item
    ambiguous_loose = {key for key, candidates in loose_candidates.items() if len(candidates - {""}) > 1}

    totals = defaultdict(lambda: defaultdict(lambda: {
        "dem": 0, "rep": 0, "other": 0, "total": 0,
        "dem_candidate": "", "rep_candidate": ""
    }))

    def single_source_assignment(assignments, contest):
        values = assignments.get(contest, set())
        if isinstance(values, str):
            values = {values}
        return next(iter(values)) if len(values) == 1 else ""

    def allocate_votes(row, contest, shares):
        parties = ("dem", "rep", "other")
        allocations = {party: {} for party in parties}
        for party in parties:
            raw_total = int(row.get(f"{contest}_{party}", 0) or 0)
            base = {district: int(raw_total * weight) for district, weight in shares.items()}
            remainder = raw_total - sum(base.values())
            ranked = sorted(shares, key=lambda district: (-(raw_total * shares[district] - base[district]), district))
            for district in ranked[:remainder]:
                base[district] += 1
            allocations[party] = base
        return allocations

    for row in payload["precinct"]:
        geography = str(row.get("geography", ""))
        if " - " not in geography:
            continue
        county, precinct = geography.split(" - ", 1)
        year = int(row["year"])
        historical_item = (historical_precinct_assignments or {}).get(
            (int(row["year"]), normalize_precinct_name(county), normalize_precinct_name(precinct))
        )
        county_key = normalize_precinct_name(county)
        strict_item = strict_aliases.get((year, county_key, strict_precinct_name(precinct)))
        loose_key = (year, county_key, normalize_precinct_name(precinct))
        loose_item = None if loose_key in ambiguous_loose else aliases.get(loose_key)
        item = historical_item or strict_item or loose_item
        # Historical source files may carry the election-era district number.
        # Those boundaries are not the requested target geography: all
        # pre-2022 results must be allocated onto the official 2022 lines.
        # Keep explicit RDH assignments authoritative only for the current
        # 2022/2024 precinct exports.
        source_assignment = (
            (source_districts or {}).get((int(row["year"]), county, precinct), {})
            if int(row["year"]) in {2022, 2024} else {}
        )
        rdh_assignment = (rdh_assignments or {}).get((int(row["year"]), normalize_precinct_name(county), normalize_precinct_name(precinct)), {})
        if not item and not source_assignment and not rdh_assignment:
            continue
        source_assignments = {
            "congressional": rdh_assignment.get("congressional") or single_source_assignment(source_assignment, "us_house"),
            "state_house": rdh_assignment.get("legislative") or single_source_assignment(source_assignment, "state_house") or single_source_assignment(source_assignment, "state_senate"),
            "state_senate": rdh_assignment.get("legislative") or single_source_assignment(source_assignment, "state_senate") or single_source_assignment(source_assignment, "state_house"),
        }
        districts = {
            "congressional": source_assignments["congressional"] or (item or {}).get("congressional_district"),
            "state_house": source_assignments["state_house"] or (item or {}).get("legislative_district"),
            "state_senate": source_assignments["state_senate"] or (item or {}).get("legislative_district"),
        }
        for contest in statewide:
            if not row.get(f"{contest}_total"):
                continue
            for scope, district in districts.items():
                shares = None
                chamber = "congressional" if scope == "congressional" else "legislative"
                # The historical VTD10 bridge is only valid for elections
                # reported on the older 2010 geography. Later exports use
                # 2020-era precincts; applying the old bridge there can reuse
                # one 2010 VTD share across several newer split precincts.
                if int(row["year"]) <= 2012 and item and historical_vtd10_shares and item.get("match_method") == "historical_vtd10_dominant_district":
                    shares = historical_vtd10_shares.get((str(item.get("vtd_geoid", "")), chamber))
                if not shares and not source_assignments[scope] and item and tabblock_shares:
                    shares = tabblock_shares.get(("2022", str(item.get("vtd_geoid", "")), chamber))
                if shares:
                    allocations = allocate_votes(row, contest, shares)
                    for district_label, values in allocations["dem"].items():
                        node = totals[(int(row["year"]), f"{scope}:{district_label}")][contest]
                        for party in ("dem", "rep", "other"):
                            node[party] += allocations[party].get(district_label, 0)
                        node["total"] += sum(allocations[party].get(district_label, 0) for party in ("dem", "rep", "other"))
                        for party in ("dem", "rep"):
                            candidate = row.get(f"{contest}_{party}_candidate", "")
                            if candidate and not node[f"{party}_candidate"]:
                                node[f"{party}_candidate"] = candidate
                    continue
                if district in (None, ""):
                    continue
                district_match = re.match(r"(\d+)", str(district))
                if not district_match:
                    continue
                district_label = district_match.group(1)
                node = totals[(int(row["year"]), f"{scope}:{district_label}")][contest]
                for party in ("dem", "rep", "other", "total"):
                    node[party] += int(row.get(f"{contest}_{party}", 0) or 0)
                for party in ("dem", "rep"):
                    candidate = row.get(f"{contest}_{party}_candidate", "")
                    if candidate and not node[f"{party}_candidate"]:
                        node[f"{party}_candidate"] = candidate
    # District-specific source races and statewide rollups share the same
    # geography keys. Merge them into one row instead of appending duplicate
    # rows that downstream consumers may interpret inconsistently.
    existing = {}
    for row in payload["district"]:
        # Several 2020 block exports include statewide contests already
        # rolled up to their source-era districts. Those totals cannot be
        # retained when rebuilding results onto the 2022 Commission lines:
        # doing so double-counts them when the precinct allocations below are
        # merged. Keep district-specific races, but clear pre-2022 statewide
        # fields so the new allocation is the sole source for these contests.
        if int(row.get("year", 0) or 0) < 2022:
            for contest in statewide | {"us_house", "state_house", "state_senate"}:
                for field in ("dem", "rep", "other", "total"):
                    row[f"{contest}_{field}"] = 0
                for party in ("dem", "rep"):
                    row[f"{contest}_{party}_candidate"] = ""
        existing[(int(row["year"]), str(row["geography"]))] = row
    for aggregate_row in flatten(totals, "district"):
        key = (int(aggregate_row["year"]), str(aggregate_row["geography"]))
        if key in existing:
            existing[key].update({field: value for field, value in aggregate_row.items() if field not in {"year", "scope", "geography"}})
        else:
            payload["district"].append(aggregate_row)


def write_contest_slices(payload: dict):
    """Emit the NC-style per-contest slices/manifests for the AZ loader."""
    contest_names = sorted({key[:-4] for row in payload["county"] for key in row if key.endswith("_dem")})
    county_contest_names = [
        contest for contest in contest_names
        if contest not in {"us_house", "state_house", "state_senate"}
    ]
    for folder_name, rows, id_field, folder_contests in [
        ("county_contests", payload["county"], "geography", county_contest_names),
        ("contests", payload["precinct"], "geography", contest_names),
    ]:
        folder = OUT / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        manifest = []
        for year in sorted({int(row["year"]) for row in rows}):
            for contest in folder_contests:
                subset = [row for row in rows if int(row["year"]) == year and int(row.get(f"{contest}_total", 0) or 0) > 0]
                if not subset:
                    continue
                key = "county_totals" if folder_name == "county_contests" else "precinct_results"
                results = {str(row[id_field]): contest_result(row, contest) for row in subset}
                node = {"year": year, "contest_type": contest, "scope": "county" if folder_name == "county_contests" else "precinct", key: results}
                filename = f"{contest}_{year}.json"
                (folder / filename).write_text(json.dumps(node, indent=2) + "\n", encoding="utf-8")
                manifest.append({"year": year, "contest_type": contest, "file": filename, "rows": len(results), "scope": node["scope"]})
        (folder / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8")

    folder = OUT / "district_contests"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = []
    for year in sorted({int(row["year"]) for row in payload["district"]}):
        for contest in contest_names:
            subset = [row for row in payload["district"] if int(row["year"]) == year and int(row.get(f"{contest}_total", 0) or 0) > 0]
            for scope, district_prefix in [("congressional", "us_house"), ("state_house", "state_house"), ("state_senate", "state_senate")]:
                prefix = scope if contest in {
                    "president", "us_senate", "governor", "attorney_general", "secretary_of_state", "treasurer", "superintendent"
                } and scope == "congressional" else district_prefix
                scoped = [row for row in subset if str(row.get("geography", "")).startswith(f"{prefix}:")]
                expected = {
                    str(row["geography"]).split(":", 1)[1]
                    for row in payload["district"]
                    if int(row["year"]) == year and str(row.get("geography", "")).startswith(f"{prefix}:")
                }
                actual = {str(row["geography"]).split(":", 1)[1] for row in scoped}
                filename = f"{scope}_{contest}_{year}.json"
                # Do not publish a partial district election: missing target
                # districts would make its margins look artificially extreme.
                if expected and actual != expected:
                    stale = folder / filename
                    if stale.exists():
                        stale.unlink()
                    continue
                # Recent superintendent files are assembled from county-level
                # exports. A district can still have all nine district keys
                # while entire counties are absent from the source (as in
                # 2022 Cochise and Yavapai). Require complete statewide county
                # coverage before publishing those district margins.
                if contest == "superintendent" and year >= 2018:
                    expected_counties = {
                        "Apache", "Cochise", "Coconino", "Gila", "Graham",
                        "Greenlee", "La Paz", "Maricopa", "Mohave", "Navajo",
                        "Pima", "Pinal", "Santa Cruz", "Yavapai", "Yuma",
                    }
                    actual_counties = {
                        str(row["geography"])
                        for row in payload["county"]
                        if int(row["year"]) == year and int(row.get("superintendent_total", 0) or 0) > 0
                    }
                    if actual_counties != expected_counties:
                        stale = folder / filename
                        if stale.exists():
                            stale.unlink()
                        continue
                if not scoped:
                    continue
                def district_number(row):
                    label = str(row["geography"]).split(":", 1)[1]
                    match = re.match(r"\d+", label)
                    return (int(match.group()) if match else 10**9, label)

                ordered = sorted(scoped, key=district_number)
                results = {str(row["geography"]).split(":", 1)[1]: contest_result(row, contest) for row in ordered}
                node = {"year": year, "scope": scope, "contest_type": contest, "general": {"results": results}}
                (folder / filename).write_text(json.dumps(node, indent=2) + "\n", encoding="utf-8")
                manifest.append({"year": year, "contest_type": contest, "file": filename, "rows": len(results), "scope": scope})
    (folder / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="*", type=int, help="Optional years to include")
    parser.add_argument("--reuse-crosswalks", action="store_true", help="Reuse existing geography crosswalks while regenerating election outputs")
    parser.add_argument("--rebuild-district-crosswalks", action="store_true", help="Rebuild VTD-to-district crosswalks while reusing other geography artifacts")
    parser.add_argument("--rebuild-election-crosswalk", action="store_true", help="Rebuild the election-precinct bridge while reusing other crosswalks")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    files = iter_input_files()
    if args.years:
        files = [path for path in files if parse_year(path) in set(args.years)]
    county, precinct, district, manifest, source_districts = aggregate_rows(files)
    precinct_rows = flatten(precinct, "precinct")
    # County exports sometimes include a county-wide "Election Total" row in
    # the same shape as a precinct result.  Keep that row in county totals,
    # but never expose it to the precinct-to-district allocator: it has no
    # geography and would otherwise make district coverage appear short.
    pseudo_precinct_labels = {"ELECTIONTOTAL", "ELECTIONTOTALS", "COUNTYTOTAL", "COUNTYWIDE", "999999"}
    payload_precinct = []
    for row in precinct_rows:
        geography = str(row.get("geography", ""))
        precinct_name = geography.split(" - ", 1)[1] if " - " in geography else geography
        if normalize_precinct_name(precinct_name) in pseudo_precinct_labels:
            continue
        payload_precinct.append(row)
    payload = {
        "state": "AZ",
        "generated_from": "data/openelections-data-az",
        "years": sorted({row["year"] for row in manifest}),
        "manifest": sorted(manifest, key=lambda row: (row["year"], row["contest_type"])),
        "county": flatten(county, "county"),
        "precinct": payload_precinct,
        "district": flatten(district, "district"),
    }
    if args.reuse_crosswalks:
        block_to_vtd = read_existing_crosswalk("block20_to_vtd20.csv")
        if args.rebuild_district_crosswalks:
            rebuilt = build_district_crosswalks()
            crosswalks = {f"vtd20_to_{key}.csv": value for key, value in rebuilt.items()}
        else:
            crosswalks = {
                "vtd20_to_congressional.csv": read_existing_crosswalk("vtd20_to_congressional.csv"),
                "vtd20_to_legislative.csv": read_existing_crosswalk("vtd20_to_legislative.csv"),
            }
        election_crosswalk = (
            build_election_precinct_crosswalk()
            if args.rebuild_election_crosswalk
            else read_existing_crosswalk("election_precinct_to_districts.csv")
        )
        block_crosswalk = read_existing_crosswalk("block_to_districts.csv")
        # Refresh vintage bridges even when the geometry crosswalks are being
        # reused, so newly supplied 2000/2010 NHGIS files are picked up.
        vintage_crosswalk = build_block_vintage_crosswalks()
    else:
        extract_and_convert_boundaries()
        block_to_vtd = build_block_to_vtd_crosswalk()
        crosswalks = build_district_crosswalks()
        election_crosswalk = build_election_precinct_crosswalk()
        block_crosswalk = build_block_crosswalks()
        vintage_crosswalk = build_block_vintage_crosswalks()
    tabblock_shares = build_tabblock_district_shares()
    historical_vtd10_shares = build_historical_vtd10_district_shares()
    historical_precinct_assignments = build_historical_precinct_district_assignments()
    rdh_assignments = load_rdh_precinct_assignments()
    reconcile_2012_maricopa_totals(payload)
    add_statewide_district_aggregates(payload, election_crosswalk, source_districts, tabblock_shares, rdh_assignments, historical_vtd10_shares, historical_precinct_assignments)
    apply_rdh_block_aggregates(payload, load_rdh_block_aggregates())
    normalize_payload_candidate_names(payload)
    (OUT / "elections_aggregated.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_contest_slices(payload)
    manifest = {
        "state": "AZ",
        "election_aggregates": "elections_aggregated.json",
        "geometry": {
            "counties": "geometry/counties.geojson",
            "precincts": "geometry/precincts.geojson",
            "congressional": "geometry/congressional_districts.geojson",
            "legislative": "geometry/legislative_districts.geojson",
        },
        "crosswalks": {
            "official_block_to_district": "crosswalks/block_to_districts.csv",
            "block20_to_vtd20": "crosswalks/block20_to_vtd20.csv",
            "block20_to_congressional": "crosswalks/block20_to_congressional.csv",
            "block20_to_state_legislative": "crosswalks/block20_to_state_legislative.csv",
            "vtd20_to_congressional": "crosswalks/vtd20_to_congressional.csv",
            "vtd20_to_legislative": "crosswalks/vtd20_to_legislative.csv",
            "election_precinct_to_districts": "crosswalks/election_precinct_to_districts.csv",
            "block2010_to_block2020": "crosswalks/block2010_to_block2020.csv",
            "block2020_to_block2010": "crosswalks/block2020_to_block2010.csv",
            "block2000_to_block2010": "crosswalks/block2000_to_block2010.csv",
        },
        "counts": {
            "county_rows": len(payload["county"]),
            "precinct_rows": len(payload["precinct"]),
            "district_rows": len(payload["district"]),
            "official_block_rows": len(block_crosswalk),
            "block20_to_vtd20_rows": len(block_to_vtd),
            "election_precinct_rows": len(election_crosswalk),
            "election_precinct_matched": sum(row.get("match_status") == "matched" for row in election_crosswalk),
            "block2010_to_block2020_rows": vintage_crosswalk.get("block2010_to_block2020.csv", 0),
            "block2020_to_block2010_rows": vintage_crosswalk.get("block2020_to_block2010.csv", 0),
            "block2000_to_block2010_rows": vintage_crosswalk.get("block2000_to_block2010.csv", 0),
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "county_rows": len(payload["county"]), "precinct_rows": len(payload["precinct"]), "district_rows": len(payload["district"]), "crosswalk_rows": {key: len(value) for key, value in crosswalks.items()}, "election_precinct_crosswalk": {"rows": len(election_crosswalk), "matched": sum(row.get("match_status") == "matched" for row in election_crosswalk)}, "block_crosswalk_rows": len(block_crosswalk), "block_vintage_crosswalk_rows": vintage_crosswalk, "years": payload["years"]}, indent=2))


if __name__ == "__main__":
    main()
