from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent


def migration_files() -> list:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name[:3].isdigit())
