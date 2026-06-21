from pathlib import Path

import typer

from directory.config import Settings
from directory.db import init_db, make_engine
from directory.ingest.author import author_mosque, run_authoring
from directory.ingest.discover import discover_mosque, run_discovery
from directory.ingest.harness import get_harness
from directory.ingest.mib import clean_mib_export, write_seed_file
from directory.ingest.runner import extract_source, run_extract
from directory.ingest.seed import load_seed_file, seed_database
from directory.ingest.website import validate_websites

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


@app.command()
def extract(
    source_id: str | None = typer.Option(None, "--source-id", help="Extract one source"),  # noqa: B008
    horizon_days: int = typer.Option(60, "--horizon-days", help="Days of occurrences"),  # noqa: B008
) -> None:
    """Run the deterministic daily extract over authored sources."""
    engine = _engine_from_env()
    if source_id is not None:
        outcomes = [extract_source(engine, source_id, horizon_days=horizon_days)]
    else:
        outcomes = run_extract(engine, horizon_days=horizon_days)
    for o in outcomes:
        typer.echo(f"{o.source_id}: lane={o.lane} status={o.triage_status} rows={o.rows_written}")
    typer.echo(f"Processed {len(outcomes)} source(s)")


@app.command("validate-websites")
def validate_websites_cmd() -> None:
    """Verify-or-empty the known mosque websites (resolve redirects, drop dead)."""
    engine = _engine_from_env()
    summary = validate_websites(engine)
    typer.echo(
        f"checked={summary.checked} repaired={summary.repaired} "
        f"dropped={summary.dropped} unchanged={summary.unchanged}"
    )


@app.command()
def discover(
    mosque_id: str | None = typer.Option(None, "--mosque-id", help="Discover one mosque"),  # noqa: B008
    horizon_days: int = typer.Option(60, "--horizon-days", help="Verification horizon"),  # noqa: B008
) -> None:
    """Run the deterministic discovery funnel (liveness → platform → gather)."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    root = settings.candidate_dir
    if mosque_id is not None:
        outcomes = [
            discover_mosque(engine, mosque_id, candidate_root=root, horizon_days=horizon_days)
        ]
    else:
        outcomes = run_discovery(engine, candidate_root=root, horizon_days=horizon_days)
    for o in outcomes:
        typer.echo(f"{o.mosque_id}: outcome={o.outcome} platform={o.platform}")
    typer.echo(f"Discovered {len(outcomes)} mosque(s)")


@app.command()
def author(
    mosque_id: str | None = typer.Option(None, "--mosque-id", help="Author one mosque"),  # noqa: B008
    max_calls: int | None = typer.Option(None, "--max-calls", help="Per-run harness call budget"),  # noqa: B008
    horizon_days: int = typer.Option(60, "--horizon-days", help="Verification horizon"),  # noqa: B008
) -> None:
    """Single-shot authoring of candidate sources via the agent harness."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    harness = get_harness(settings.author_harness)
    models = (settings.author_model_cheap, settings.author_model_strong)
    root = settings.candidate_dir
    if mosque_id is not None:
        outcomes = [
            author_mosque(
                engine, mosque_id, harness=harness, candidate_root=root,
                models=models, horizon_days=horizon_days,
            )
        ]
    else:
        outcomes = run_authoring(
            engine, harness=harness, candidate_root=root, models=models,
            max_calls=max_calls or settings.author_max_calls, horizon_days=horizon_days,
        )
    for o in outcomes:
        typer.echo(f"{o.mosque_id}: outcome={o.outcome} model={o.model}")
    typer.echo(f"Authored {len(outcomes)} mosque(s)")


if __name__ == "__main__":
    app()
