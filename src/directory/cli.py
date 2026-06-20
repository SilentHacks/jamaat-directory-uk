from pathlib import Path

import typer

from directory.config import Settings
from directory.db import init_db, make_engine
from directory.ingest.mib import clean_mib_export, write_seed_file
from directory.ingest.seed import load_seed_file, seed_database

app = typer.Typer(help="UK Mosque Jamaat Directory CLI")


def _engine_from_env():
    settings = Settings()
    engine = make_engine(settings.database_url)
    return engine


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite database and schema."""
    engine = _engine_from_env()
    init_db(engine)
    typer.echo(f"Initialised {Settings().db_path}")


@app.command("import-mib")
def import_mib(
    input: Path = typer.Option(..., "--input", help="Raw upstream export JSON"),  # noqa: B008
    output: Path = typer.Option(  # noqa: B008
        Path("data/seed/mosques.json"), "--output", help="Cleaned seed JSON path"
    ),
) -> None:
    """Clean the upstream export into the seed schema."""
    records = clean_mib_export(input)
    write_seed_file(records, output)
    typer.echo(f"Cleaned {len(records)} mosques to {output}")


@app.command()
def seed(input: Path = typer.Option(..., "--input", help="Seed JSON file")) -> None:  # noqa: B008
    """Load the MIB mosque list into SQLite."""
    engine = _engine_from_env()
    init_db(engine)
    records = load_seed_file(input)
    n = seed_database(engine, records)
    typer.echo(f"Seeded {n} mosques")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the API + browse site."""
    import uvicorn

    uvicorn.run("directory.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
