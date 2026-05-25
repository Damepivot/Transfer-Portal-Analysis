"""
Team name normalization across CBB Reference, BartTorvik, and On3/247Sports.
Usage:
    from normalize import canonical_team, canonical_conference
"""

# Maps all known variants → canonical name used in our DB
TEAM_NAME_MAP = {
    # ACC
    "North Carolina": "North Carolina",
    "UNC": "North Carolina",
    "NC": "North Carolina",
    "Duke": "Duke",
    "Virginia": "Virginia",
    "UVA": "Virginia",
    "Virginia Tech": "Virginia Tech",
    "VT": "Virginia Tech",
    "Clemson": "Clemson",
    "Wake Forest": "Wake Forest",
    "NC State": "NC State",
    "North Carolina State": "NC State",
    "Florida State": "Florida State",
    "FSU": "Florida State",
    "Miami (FL)": "Miami FL",
    "Miami": "Miami FL",
    "Miami (Florida)": "Miami FL",
    "Georgia Tech": "Georgia Tech",
    "Notre Dame": "Notre Dame",
    "Pittsburgh": "Pittsburgh",
    "Pitt": "Pittsburgh",
    "Louisville": "Louisville",
    "Syracuse": "Syracuse",
    "Boston College": "Boston College",

    # Big Ten
    "Michigan": "Michigan",
    "Michigan State": "Michigan State",
    "Ohio State": "Ohio State",
    "Penn State": "Penn State",
    "Indiana": "Indiana",
    "Purdue": "Purdue",
    "Iowa": "Iowa",
    "Illinois": "Illinois",
    "Wisconsin": "Wisconsin",
    "Minnesota": "Minnesota",
    "Northwestern": "Northwestern",
    "Nebraska": "Nebraska",
    "Maryland": "Maryland",
    "Rutgers": "Rutgers",
    "UCLA": "UCLA",
    "USC": "USC",
    "Washington": "Washington",
    "Oregon": "Oregon",
    "USC Trojans": "USC",

    # Big 12
    "Kansas": "Kansas",
    "Baylor": "Baylor",
    "Texas": "Texas",
    "Texas Tech": "Texas Tech",
    "Oklahoma": "Oklahoma",
    "Oklahoma State": "Oklahoma State",
    "West Virginia": "West Virginia",
    "WVU": "West Virginia",
    "TCU": "TCU",
    "Texas Christian": "TCU",
    "Iowa State": "Iowa State",
    "Kansas State": "Kansas State",
    "K-State": "Kansas State",
    "Cincinnati": "Cincinnati",
    "Houston": "Houston",
    "BYU": "BYU",
    "Brigham Young": "BYU",
    "UCF": "UCF",
    "Central Florida": "UCF",

    # SEC
    "Kentucky": "Kentucky",
    "Tennessee": "Tennessee",
    "Auburn": "Auburn",
    "Alabama": "Alabama",
    "Arkansas": "Arkansas",
    "Mississippi State": "Mississippi State",
    "Ole Miss": "Ole Miss",
    "Mississippi": "Ole Miss",
    "LSU": "LSU",
    "Florida": "Florida",
    "Georgia": "Georgia",
    "South Carolina": "South Carolina",
    "Missouri": "Missouri",
    "Mizzou": "Missouri",
    "Vanderbilt": "Vanderbilt",
    "Texas A&M": "Texas A&M",
    "Texas A&amp;M": "Texas A&M",

    # Big East
    "Connecticut": "Connecticut",
    "UConn": "Connecticut",
    "Villanova": "Villanova",
    "Marquette": "Marquette",
    "Seton Hall": "Seton Hall",
    "Xavier": "Xavier",
    "Providence": "Providence",
    "Creighton": "Creighton",
    "Butler": "Butler",
    "DePaul": "DePaul",
    "Georgetown": "Georgetown",
    "St. John's": "St. John's",
    "Saint John's": "St. John's",

    # AAC
    "Memphis": "Memphis",
    "Wichita State": "Wichita State",
    "Temple": "Temple",
    "Tulsa": "Tulsa",
    "South Florida": "South Florida",
    "USF": "South Florida",
    "SMU": "SMU",
    "Southern Methodist": "SMU",
    "Tulane": "Tulane",
    "East Carolina": "East Carolina",
    "ECU": "East Carolina",
    "Charlotte": "Charlotte",
    "UTSA": "UTSA",
    "North Texas": "North Texas",
    "Florida Atlantic": "Florida Atlantic",
    "FAU": "Florida Atlantic",
    "Rice": "Rice",

    # Mountain West
    "San Diego State": "San Diego State",
    "SDSU": "San Diego State",
    "Nevada": "Nevada",
    "New Mexico": "New Mexico",
    "Utah State": "Utah State",
    "Boise State": "Boise State",
    "Colorado State": "Colorado State",
    "Fresno State": "Fresno State",
    "UNLV": "UNLV",
    "Wyoming": "Wyoming",
    "Air Force": "Air Force",
    "Hawaii": "Hawaii",

    # WCC
    "Gonzaga": "Gonzaga",
    "Saint Mary's": "Saint Mary's",
    "Saint Mary's (CA)": "Saint Mary's",
    "San Francisco": "San Francisco",
    "USF (CA)": "San Francisco",
    "Santa Clara": "Santa Clara",
    "Pacific": "Pacific",
    "Loyola Marymount": "Loyola Marymount",
    "LMU": "Loyola Marymount",
    "Pepperdine": "Pepperdine",
    "Portland": "Portland",

    # Atlantic 10
    "Dayton": "Dayton",
    "VCU": "VCU",
    "Virginia Commonwealth": "VCU",
    "Saint Louis": "Saint Louis",
    "Richmond": "Richmond",
    "Davidson": "Davidson",
    "Rhode Island": "Rhode Island",
    "URI": "Rhode Island",
    "George Mason": "George Mason",
    "Fordham": "Fordham",
    "La Salle": "La Salle",
    "Duquesne": "Duquesne",
    "Massachusetts": "Massachusetts",
    "UMass": "Massachusetts",

    # Pac-12 (pre-dissolution)
    "Arizona": "Arizona",
    "Arizona State": "Arizona State",
    "Colorado": "Colorado",
    "Stanford": "Stanford",
    "California": "California",
    "Cal": "California",
    "Utah": "Utah",
    "Washington State": "Washington State",
    "WSU": "Washington State",
    "Oregon State": "Oregon State",
}

# Conference name variants → canonical
CONFERENCE_NAME_MAP = {
    "ACC": "ACC",
    "Atlantic Coast": "ACC",
    "Atlantic Coast Conference": "ACC",
    "Big Ten": "Big Ten",
    "Big 10": "Big Ten",
    "B1G": "Big Ten",
    "Big 12": "Big 12",
    "Big Twelve": "Big 12",
    "SEC": "SEC",
    "Southeastern": "SEC",
    "Southeastern Conference": "SEC",
    "Big East": "Big East",
    "American": "AAC",
    "AAC": "AAC",
    "American Athletic": "AAC",
    "American Athletic Conference": "AAC",
    "Mountain West": "MWC",
    "MWC": "MWC",
    "WCC": "WCC",
    "West Coast": "WCC",
    "West Coast Conference": "WCC",
    "A-10": "A-10",
    "Atlantic 10": "A-10",
    "Atlantic Ten": "A-10",
    "MVC": "MVC",
    "Missouri Valley": "MVC",
    "Missouri Valley Conference": "MVC",
    "MAC": "MAC",
    "Mid-American": "MAC",
    "Mid American": "MAC",
    "C-USA": "CUSA",
    "CUSA": "CUSA",
    "Conference USA": "CUSA",
    "Sun Belt": "Sun Belt",
    "CAA": "CAA",
    "Colonial": "CAA",
    "Colonial Athletic": "CAA",
    "Horizon": "Horizon",
    "Horizon League": "Horizon",
    "Big West": "Big West",
    "Southern": "SoCon",
    "SoCon": "SoCon",
    "Southern Conference": "SoCon",
    "Big South": "Big South",
    "NEC": "NEC",
    "Northeast": "NEC",
    "Northeast Conference": "NEC",
    "OVC": "OVC",
    "Ohio Valley": "OVC",
    "Ohio Valley Conference": "OVC",
    "SWAC": "SWAC",
    "Southwestern Athletic": "SWAC",
    "MEAC": "MEAC",
    "Mid-Eastern Athletic": "MEAC",
    "Patriot": "Patriot",
    "Patriot League": "Patriot",
    "America East": "America East",
    "WAC": "WAC",
    "Western Athletic": "WAC",
    "Ivy": "Ivy",
    "Ivy League": "Ivy",
    "ASUN": "ASUN",
    "A-Sun": "ASUN",
    "Atlantic Sun": "ASUN",
    "Pac-12": "Pac-12",
    "Pac 12": "Pac-12",
    "Pacific-12": "Pac-12",
    "Pacific 12": "Pac-12",
}


def canonical_team(raw_name: str) -> str:
    """Return the canonical team name for a raw string, or the raw string if unknown."""
    if not raw_name:
        return raw_name
    cleaned = raw_name.strip()
    return TEAM_NAME_MAP.get(cleaned, cleaned)


def canonical_conference(raw_name: str) -> str:
    """Return the canonical conference abbreviation for a raw string."""
    if not raw_name:
        return raw_name
    cleaned = raw_name.strip()
    return CONFERENCE_NAME_MAP.get(cleaned, cleaned)


def normalize_season(raw: str) -> str:
    """
    Normalize season strings to '2022-23' format.
    Handles: '2022-23', '2022-2023', '2023', '22-23'
    """
    raw = str(raw).strip()
    if len(raw) == 4 and raw.isdigit():
        yr = int(raw)
        return f"{yr-1}-{str(yr)[2:]}"
    if "-" in raw:
        parts = raw.split("-")
        start = parts[0].strip()
        end = parts[1].strip()
        if len(start) == 2:
            start = "20" + start
        if len(end) == 4:
            end = end[2:]
        return f"{start}-{end}"
    return raw
