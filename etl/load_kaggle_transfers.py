"""
Load and clean NCAA transfer portal CSV(s) downloaded from Kaggle.

Expected input: one or more CSV files in data/raw/ with transfer portal records.
Common Kaggle datasets have columns like:
  Player, From, To, Position, Stars, Rating, Season  (varies by dataset)

Outputs: data/transfers_clean.csv — normalized, deduplicated, ready for PostgreSQL.

Usage:
    1. Download a transfer portal CSV from Kaggle (search "NCAA basketball transfer portal")
    2. Drop the CSV(s) into data/raw/
    3. python etl/load_kaggle_transfers.py
"""

import re
import pandas as pd
from pathlib import Path
from normalize import canonical_team, normalize_season

RAW_DIR    = Path(__file__).parent.parent / "data" / "raw"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "transfers_clean.csv"

# Column name aliases — maps whatever Kaggle uses → our standard names
COLUMN_ALIASES = {
    # player name
    "player":        "player_name",
    "name":          "player_name",
    "player_name":   "player_name",
    "athlete":       "player_name",
    # from school
    "from":          "from_school",
    "from_school":   "from_school",
    "previous_school": "from_school",
    "origin":        "from_school",
    "transferring_from": "from_school",
    # to school
    "to":            "to_school",
    "to_school":     "to_school",
    "destination":   "to_school",
    "new_school":    "to_school",
    "transferring_to": "to_school",
    # position
    "position":      "position",
    "pos":           "position",
    # season / year
    "season":        "season",
    "year":          "season",
    "transfer_year": "season",
    # recruiting composite / stars
    "stars":         "stars",
    "rating":        "recruiting_composite",
    "composite":     "recruiting_composite",
    "on3_composite": "recruiting_composite",
    "247_composite": "recruiting_composite",
    # class year
    "class":         "class_year",
    "eligibility":   "class_year",
    "year_in_school": "class_year",
}

REQUIRED_COLS = {"player_name", "from_school", "to_school"}

POSITION_MAP = {
    "guard":         "G",
    "point guard":   "G",
    "shooting guard":"G",
    "pg":            "G",
    "sg":            "G",
    "g":             "G",
    "forward":       "F",
    "small forward": "F",
    "power forward": "F",
    "sf":            "F",
    "pf":            "F",
    "f":             "F",
    "center":        "C",
    "c":             "C",
    "g/f":           "G/F",
    "f/c":           "F/C",
    "wing":          "G/F",
}

CLASS_YEAR_MAP = {
    "freshman":  "FR",
    "fr":        "FR",
    "sophomore": "SO",
    "so":        "SO",
    "junior":    "JR",
    "jr":        "JR",
    "senior":    "SR",
    "sr":        "SR",
    "graduate":  "Grad",
    "grad":      "Grad",
    "5th year":  "Grad",
    "fifth year":"Grad",
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={k: v for k, v in COLUMN_ALIASES.items() if k in df.columns})
    return df


def normalize_position(val) -> str:
    if pd.isna(val):
        return None
    return POSITION_MAP.get(str(val).strip().lower(), str(val).strip().upper()[:5])


def normalize_class_year(val) -> str:
    if pd.isna(val):
        return None
    return CLASS_YEAR_MAP.get(str(val).strip().lower(), str(val).strip())


def infer_season(df: pd.DataFrame, filepath: Path) -> pd.DataFrame:
    """If no season column, try to infer from filename (e.g. transfers_2023.csv)."""
    if "season" not in df.columns:
        match = re.search(r"(20\d{2})", filepath.stem)
        if match:
            year = int(match.group(1))
            df["season"] = f"{year-1}-{str(year)[2:]}"
        else:
            df["season"] = "unknown"
    return df


def load_one_file(filepath: Path) -> pd.DataFrame | None:
    print(f"  Loading {filepath.name}...")
    try:
        df = pd.read_csv(filepath, encoding="utf-8", low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(filepath, encoding="latin-1", low_memory=False)

    df = normalize_columns(df)

    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        print(f"    SKIP — missing required columns: {missing}")
        return None

    df = infer_season(df, filepath)

    # Normalize values
    df["player_name"]  = df["player_name"].str.strip().str.title()
    df["from_school"]  = df["from_school"].apply(lambda x: canonical_team(str(x).strip()) if pd.notna(x) else None)
    df["to_school"]    = df["to_school"].apply(lambda x: canonical_team(str(x).strip()) if pd.notna(x) else None)
    df["season"]       = df["season"].apply(normalize_season)

    if "position" in df.columns:
        df["position"] = df["position"].apply(normalize_position)
    else:
        df["position"] = None

    if "class_year" in df.columns:
        df["class_year"] = df["class_year"].apply(normalize_class_year)
    else:
        df["class_year"] = None

    if "stars" in df.columns and "recruiting_composite" not in df.columns:
        # Convert 1-5 star rating to approximate 0-100 composite
        star_to_composite = {5: 95.0, 4: 88.0, 3: 83.0, 2: 78.0, 1: 70.0}
        df["recruiting_composite"] = df["stars"].map(star_to_composite)

    if "recruiting_composite" not in df.columns:
        df["recruiting_composite"] = None

    keep_cols = [
        "player_name", "from_school", "to_school",
        "position", "class_year", "season", "recruiting_composite",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]

    # Drop rows missing either school
    df = df.dropna(subset=["from_school", "to_school"])

    # Drop self-transfers (same school listed as from and to — data artifact)
    df = df[df["from_school"] != df["to_school"]]

    print(f"    -> {len(df)} transfers after cleaning")
    return df


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    csv_files = list(RAW_DIR.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {RAW_DIR}")
        print("Download a transfer portal dataset from Kaggle and place it in data/raw/")
        return

    frames = []
    for f in sorted(csv_files):
        df = load_one_file(f)
        if df is not None:
            frames.append(df)

    if not frames:
        print("No usable files found.")
        return

    combined = pd.concat(frames, ignore_index=True)

    # Deduplicate: same player, same from/to schools, same season
    before = len(combined)
    combined = combined.drop_duplicates(subset=["player_name", "from_school", "to_school", "season"])
    after = len(combined)
    print(f"\nDeduplication: {before} -> {after} rows ({before - after} duplicates removed)")

    combined = combined.sort_values(["season", "player_name"]).reset_index(drop=True)
    combined.to_csv(OUTPUT_PATH, index=False)
    print(f"Saved {len(combined)} transfers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
