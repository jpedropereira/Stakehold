from pathlib import Path

from dotenv import load_dotenv

# Explicit path, not a bare load_dotenv(): the default search only walks upward from the
# current working directory, so it wouldn't find services/data-store/.env when pytest is
# run from the repo root.
load_dotenv(Path(__file__).parents[2] / ".env")
