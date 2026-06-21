from pathlib import Path

import typer

from directory.config import Settings
from directory.db import init_db, make_engine
from directory.ingest.author import author_mosque, run_authoring
from directory.ingest.blocklist import load_blocklist
from directory.ingest.discover import discover_mosque, run_discovery
from directory.ingest.extractors.bespoke import load_bespoke
from directory.ingest.harness import OpenCodeAgenticHarness, OpenCodeHarness
from directory.ingest.runner import extract_source, run_extract
from directory.ingest.seed import (
    clean_mib_export,
    load_seed_file,
    seed_database,
    write_seed_file,
)
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
    concurrency: int | None = typer.Option(  # noqa: B008
        None, "--concurrency", help="Parallel source extracts (default from settings)"
    ),
) -> None:
    """Run the deterministic daily extract over authored sources."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    load_bespoke(settings.bespoke_dir)
    if source_id is not None:
        outcomes = [extract_source(engine, source_id, horizon_days=horizon_days)]
    else:
        outcomes = run_extract(
            engine, horizon_days=horizon_days,
            concurrency=concurrency or settings.discover_concurrency,
        )
    for o in outcomes:
        typer.echo(f"{o.source_id}: lane={o.lane} status={o.triage_status} rows={o.rows_written}")
    typer.echo(f"Processed {len(outcomes)} source(s)")


@app.command("validate-websites")
def validate_websites_cmd(
    concurrency: int | None = typer.Option(  # noqa: B008
        None, "--concurrency", help="Parallel liveness checks (default from settings)"
    ),
) -> None:
    """Verify-or-empty the known mosque websites (resolve redirects, drop dead)."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    summary = validate_websites(
        engine, concurrency=concurrency or settings.discover_concurrency
    )
    typer.echo(
        f"checked={summary.checked} repaired={summary.repaired} "
        f"dropped={summary.dropped} unchanged={summary.unchanged}"
    )


@app.command()
def discover(
    mosque_id: str | None = typer.Option(None, "--mosque-id", help="Discover one mosque"),  # noqa: B008
    horizon_days: int = typer.Option(60, "--horizon-days", help="Verification horizon"),  # noqa: B008
    concurrency: int | None = typer.Option(  # noqa: B008
        None, "--concurrency", help="Parallel mosque discovery (default from settings)"
    ),
) -> None:
    """Run the deterministic discovery funnel (liveness → platform → gather)."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    root = settings.candidate_dir
    blocklist = load_blocklist(settings.blocklist_path)
    if mosque_id is not None:
        outcomes = [
            discover_mosque(engine, mosque_id, candidate_root=root,
                            horizon_days=horizon_days, blocklist=blocklist)
        ]
    else:
        outcomes = run_discovery(
            engine, candidate_root=root, horizon_days=horizon_days, blocklist=blocklist,
            concurrency=concurrency or settings.discover_concurrency,
        )
    for o in outcomes:
        typer.echo(f"{o.mosque_id}: outcome={o.outcome} platform={o.platform}")
    typer.echo(f"Discovered {len(outcomes)} mosque(s)")


@app.command()
def author(
    mosque_id: str | None = typer.Option(None, "--mosque-id", help="Author one mosque"),  # noqa: B008
    max_calls: int | None = typer.Option(None, "--max-calls", help="Per-run harness call budget"),  # noqa: B008
    horizon_days: int = typer.Option(60, "--horizon-days", help="Verification horizon"),  # noqa: B008
    agentic: bool = typer.Option(False, "--agentic", help="Enable the stage-4 agentic fallback"),  # noqa: B008
    concurrency: int | None = typer.Option(  # noqa: B008
        None, "--concurrency", help="Parallel authoring workers (default from settings)"
    ),
) -> None:
    """Single-shot authoring of candidate sources via the agent harness."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    harness = OpenCodeHarness()
    fallback = (
        OpenCodeAgenticHarness(
            page_budget=settings.author_page_budget,
            token_budget=settings.author_token_budget,
        )
        if agentic
        else None
    )
    models = (settings.author_model_cheap, settings.author_model_strong)
    root = settings.candidate_dir
    if mosque_id is not None:
        outcomes = [
            author_mosque(
                engine, mosque_id, harness=harness, candidate_root=root, models=models,
                fallback=fallback, fallback_model=settings.author_model_strong,
                bespoke_root=settings.bespoke_dir, horizon_days=horizon_days,
            )
        ]
    else:
        outcomes = run_authoring(
            engine, harness=harness, candidate_root=root, models=models,
            fallback=fallback, fallback_model=settings.author_model_strong,
            bespoke_root=settings.bespoke_dir,
            max_calls=max_calls or settings.author_max_calls,
            concurrency=concurrency or settings.author_concurrency,
            horizon_days=horizon_days,
        )
    for o in outcomes:
        typer.echo(f"{o.mosque_id}: outcome={o.outcome} model={o.model}")
    typer.echo(f"Authored {len(outcomes)} mosque(s)")


if __name__ == "__main__":
    app()
