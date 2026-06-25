import time
from collections import Counter
from pathlib import Path

import typer

from directory.config import Settings
from directory.db import init_db, make_engine
from directory.ingest.author import (
    author_mosque,
    run_authoring,
    run_reauthor,
    run_verify_retry,
)
from directory.ingest.blocklist import load_blocklist
from directory.ingest.discover import discover_mosque, run_discovery
from directory.ingest.extractors.bespoke import load_bespoke
from directory.ingest.fetch import render_playwright, render_playwright_nav
from directory.ingest.harness import (
    ClaudeCodeAgenticHarness,
    ClaudeCodeHarness,
    OpenCodeAgenticHarness,
    OpenCodeHarness,
    request_shutdown,
)
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


def _build_harness(settings, *, harness_name: str, fallback: bool, agentic: bool):
    """Assemble (harness, models, fallback_harness, fallback_model) for the
    authoring funnel from settings + flags.

    claude-code: Opus 4.8 @low by default; --fallback appends Opus 4.8 @high;
    --agentic browses at @low. opencode: the legacy cheap→strong ladder (the
    high-effort --fallback knob does not apply)."""
    if harness_name == "claude-code":
        harness = ClaudeCodeHarness()
        models = (settings.claude_code_model,)
        if fallback:
            models = (*models, settings.claude_code_fallback_model)
        fb = (
            ClaudeCodeAgenticHarness(
                page_budget=settings.author_page_budget,
                token_budget=settings.author_token_budget,
            )
            if agentic
            else None
        )
        return harness, models, fb, settings.claude_code_agentic_model
    if harness_name == "opencode":
        harness = OpenCodeHarness()
        models = (settings.author_model_cheap, settings.author_model_strong)
        fb = (
            OpenCodeAgenticHarness(
                page_budget=settings.author_page_budget,
                token_budget=settings.author_token_budget,
            )
            if agentic
            else None
        )
        return harness, models, fb, settings.author_model_strong
    raise typer.BadParameter(f"unknown harness '{harness_name}' (claude-code|opencode)")


def _make_reporter(label: str):
    """Build a live progress callback for the long-running funnel commands.

    Returns ``(tally, report, elapsed)``: a Counter of statuses seen so far, an
    ``on_outcome(done, total, result)`` callback that prints one line per
    completed source (skipping budget no-ops), and a callable for total elapsed
    seconds. Works for both AuthorOutcome and ExtractOutcome results."""
    start = time.monotonic()
    tally: Counter[str] = Counter()

    def report(done: int, total: int, result) -> None:
        if result is None:  # budget exhausted / not dispatched — nothing to show
            return
        status = getattr(result, "outcome", None) or getattr(result, "triage_status", "?")
        ident = getattr(result, "mosque_id", None) or getattr(result, "source_id", "?")
        model = getattr(result, "model", None)
        rows = getattr(result, "rows_written", None)
        tally[status] += 1
        extra = f" model={model}" if model else ""
        if rows is not None:
            extra += f" rows={rows}"
        typer.echo(
            f"[{done}/{total}] {ident}: {label}={status}{extra}"
            f"  ({time.monotonic() - start:.0f}s)"
        )

    return tally, report, lambda: time.monotonic() - start


def _summarise(tally: Counter[str]) -> str:
    return "  ".join(f"{k}={v}" for k, v in sorted(tally.items()))


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
def curate(
    input: Path = typer.Option(  # noqa: B008
        Path("data/curation/duplicates.json"), "--input", help="Curation overlay JSON"
    ),
) -> None:
    """Apply the reviewed duplicate-curation overlay (merge dupes, flag shared-URL
    venues for review). Run after `seed`, before `discover`."""
    from directory.ingest.curate import apply_curation, load_curation

    engine = _engine_from_env()
    summary = apply_curation(engine, load_curation(input))
    typer.echo(
        f"merged={summary.merged} flagged={summary.flagged} skipped={summary.skipped}"
    )


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
    render_js: bool = typer.Option(  # noqa: B008
        True, "--render-js/--no-render-js",
        help="Render JS sources (and click month paging) with a headless browser",
    ),
) -> None:
    """Run the deterministic daily extract over authored sources."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    load_bespoke(settings.bespoke_dir)
    renderer = render_playwright if render_js else None
    nav_renderer = render_playwright_nav if render_js else None
    if source_id is not None:
        outcomes = [
            extract_source(engine, source_id, horizon_days=horizon_days,
                           renderer=renderer, nav_renderer=nav_renderer)
        ]
    else:
        outcomes = run_extract(
            engine, horizon_days=horizon_days,
            concurrency=concurrency or settings.discover_concurrency,
            renderer=renderer, nav_renderer=nav_renderer,
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
    render_js: bool = typer.Option(  # noqa: B008
        True, "--render-js/--no-render-js",
        help="Re-render JS-shell pages with a headless browser after a static miss",
    ),
    force: bool = typer.Option(  # noqa: B008
        False, "--force",
        help="Re-discover even sources that already hold a config (overwrites it)",
    ),
) -> None:
    """Run the deterministic discovery funnel (liveness → platform → gather).

    Sources that already hold a config are preserved (skipped) unless --force, so
    a re-run never wipes a flaky-but-correct config a verify-retry could salvage."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    root = settings.candidate_dir
    blocklist = load_blocklist(settings.blocklist_path)
    renderer = render_playwright if render_js else None
    nav_renderer = render_playwright_nav if render_js else None
    if mosque_id is not None:
        outcomes = [
            discover_mosque(engine, mosque_id, candidate_root=root,
                            horizon_days=horizon_days, blocklist=blocklist, renderer=renderer,
                            nav_renderer=nav_renderer, force=force)
        ]
    else:
        outcomes = run_discovery(
            engine, candidate_root=root, horizon_days=horizon_days, blocklist=blocklist,
            concurrency=concurrency or settings.discover_concurrency, renderer=renderer,
            nav_renderer=nav_renderer, force=force,
        )
    for o in outcomes:
        typer.echo(f"{o.mosque_id}: outcome={o.outcome} platform={o.platform}")
    typer.echo(f"Discovered {len(outcomes)} mosque(s)")


@app.command()
def author(
    mosque_id: str | None = typer.Option(None, "--mosque-id", help="Author one mosque"),  # noqa: B008
    max_calls: int | None = typer.Option(None, "--max-calls", help="Per-run harness call budget"),  # noqa: B008
    horizon_days: int = typer.Option(60, "--horizon-days", help="Verification horizon"),  # noqa: B008
    harness_name: str | None = typer.Option(  # noqa: B008
        None, "--harness", help="Authoring backend (claude-code|opencode; default from settings)"
    ),
    fallback: bool = typer.Option(  # noqa: B008
        False, "--fallback",
        help="Opt in to the high-effort fallback model (claude-code: Opus 4.8 @high)",
    ),
    agentic: bool = typer.Option(False, "--agentic", help="Enable the stage-4 agentic fallback"),  # noqa: B008
    concurrency: int | None = typer.Option(  # noqa: B008
        None, "--concurrency", help="Parallel authoring workers (default from settings)"
    ),
    render_js: bool = typer.Option(  # noqa: B008
        True, "--render-js/--no-render-js",
        help="Render JS sources (and click month paging) when verifying configs",
    ),
) -> None:
    """Single-shot authoring of candidate sources via the agent harness. Defaults
    to Claude Code with Opus 4.8 at low effort; --fallback opts into a high-effort
    retry, --agentic adds the browsing fallback (at low effort)."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    renderer = render_playwright if render_js else None
    nav_renderer = render_playwright_nav if render_js else None
    harness, models, fb, fb_model = _build_harness(
        settings, harness_name=harness_name or settings.author_harness,
        fallback=fallback, agentic=agentic,
    )
    root = settings.candidate_dir
    workers = concurrency or settings.author_concurrency
    tally, report, elapsed = _make_reporter("outcome")
    try:
        if mosque_id is not None:
            outcomes = [
                author_mosque(
                    engine, mosque_id, harness=harness, candidate_root=root, models=models,
                    fallback=fb, fallback_model=fb_model,
                    bespoke_root=settings.bespoke_dir, horizon_days=horizon_days,
                    renderer=renderer, nav_renderer=nav_renderer,
                    feedback_retries=settings.author_feedback_retries,
                )
            ]
            for o in outcomes:
                typer.echo(f"{o.mosque_id}: outcome={o.outcome} model={o.model}")
        else:
            typer.echo(
                f"Authoring remaining candidates (concurrency={workers}). "
                "Completed sources are skipped automatically; Ctrl-C stops cleanly "
                "and the run resumes where it left off on the next invocation."
            )
            outcomes = run_authoring(
                engine, harness=harness, candidate_root=root, models=models,
                fallback=fb, fallback_model=fb_model,
                bespoke_root=settings.bespoke_dir,
                max_calls=max_calls or settings.author_max_calls,
                concurrency=workers,
                horizon_days=horizon_days,
                renderer=renderer, nav_renderer=nav_renderer,
                feedback_retries=settings.author_feedback_retries,
                on_outcome=report,
            )
    except KeyboardInterrupt:
        request_shutdown()  # idempotent: ensure no agent subprocess is left running
        typer.secho(
            f"\nInterrupted after {sum(tally.values())} mosque(s) in {elapsed():.0f}s; "
            "in-flight agents terminated.",
            err=True, fg=typer.colors.YELLOW,
        )
        if tally:
            typer.secho("  " + _summarise(tally), err=True)
        raise typer.Exit(130) from None

    if tally:
        typer.echo("Summary: " + _summarise(tally))
    typer.echo(f"Authored {len(outcomes)} mosque(s) in {elapsed():.0f}s")


@app.command()
def reauthor(
    verify_only: bool = typer.Option(  # noqa: B008
        True, "--verify-only/--no-verify-only",
        help="FREE: re-run extraction on retained configs, no model call (default)",
    ),
    horizon_days: int = typer.Option(60, "--horizon-days", help="Verification horizon"),  # noqa: B008
    harness_name: str | None = typer.Option(  # noqa: B008
        None, "--harness", help="Authoring backend (claude-code|opencode; default from settings)"
    ),
    fallback: bool = typer.Option(  # noqa: B008
        False, "--fallback",
        help="Opt in to the high-effort fallback model (claude-code: Opus 4.8 @high)",
    ),
    agentic: bool = typer.Option(False, "--agentic", help="Enable the stage-4 agentic fallback"),  # noqa: B008
    max_calls: int | None = typer.Option(None, "--max-calls", help="Per-run harness call budget"),  # noqa: B008
    concurrency: int | None = typer.Option(  # noqa: B008
        None, "--concurrency", help="Parallel workers (default from settings)"
    ),
    render_js: bool = typer.Option(  # noqa: B008
        True, "--render-js/--no-render-js",
        help="Render JS sources (and click month paging) when verifying configs",
    ),
) -> None:
    """Recover the `needs_reauthor` cohort.

    --verify-only (default): FREE re-run of the daily extract on each source's
    retained config — no model call. Salvages render-flakiness false-negatives.
    Run this first, before any paid batch.

    --no-verify-only: model re-authoring of sources that still have a candidate
    bundle (Claude Code / Opus 4.8 by default). The prior config is restored if a
    re-author attempt fails, so a bad model roll never discards a good config."""
    settings = Settings()
    engine = make_engine(settings.database_url)
    load_bespoke(settings.bespoke_dir)  # a retained config may reference a bespoke module
    renderer = render_playwright if render_js else None
    nav_renderer = render_playwright_nav if render_js else None
    try:
        if verify_only:
            outcomes = run_verify_retry(
                engine, horizon_days=horizon_days,
                concurrency=concurrency or settings.discover_concurrency,
                renderer=renderer, nav_renderer=nav_renderer,
            )
            for o in outcomes:
                typer.echo(f"{o.source_id}: status={o.triage_status} rows={o.rows_written}")
            recovered = sum(1 for o in outcomes if o.triage_status != "needs_reauthor")
            typer.echo(f"Verified {len(outcomes)} source(s); recovered {recovered}")
            return

        harness, models, fb, fb_model = _build_harness(
            settings, harness_name=harness_name or settings.author_harness,
            fallback=fallback, agentic=agentic,
        )
        outcomes = run_reauthor(
            engine, harness=harness, candidate_root=settings.candidate_dir, models=models,
            fallback=fb, fallback_model=fb_model, bespoke_root=settings.bespoke_dir,
            max_calls=max_calls or settings.author_max_calls,
            concurrency=concurrency or settings.author_concurrency,
            horizon_days=horizon_days, renderer=renderer, nav_renderer=nav_renderer,
            feedback_retries=settings.author_feedback_retries,
        )
    except KeyboardInterrupt:
        request_shutdown()  # idempotent: ensure no agent subprocess is left running
        typer.secho("\nInterrupted; in-flight agents terminated.",
                    err=True, fg=typer.colors.YELLOW)
        raise typer.Exit(130) from None
    for o in outcomes:
        typer.echo(f"{o.mosque_id}: outcome={o.outcome} model={o.model}")
    authored = sum(1 for o in outcomes if o.outcome in {"authored", "review", "deferred_media"})
    typer.echo(f"Re-authored {len(outcomes)} source(s); recovered {authored}")


if __name__ == "__main__":
    app()
