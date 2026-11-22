"""Configuration settings, paths, and environment definitions."""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(PROJECT_ROOT / ".env")

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
REFERENCE_DIR = DATA_DIR / "reference"

CONSTITUENTS_FILE = REFERENCE_DIR / "nifty500_constituents.csv"
SNAPSHOT_DATE = "2026-11-22"


def get_database_url() -> str:
    """Return PostgreSQL connection URL constructed from environment variables."""
    user = os.getenv("POSTGRES_USER", "screener")
    password = os.getenv("POSTGRES_PASSWORD", "screener_dev")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "nifty500")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure clean timestamped console logging."""
    format_str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(
        level=level,
        format=format_str,
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
