from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIRECTORY_", env_file=".env")

    db_path: Path = Path("data/directory.db")
    admin_api_key: str | None = None
    snapshot_horizon_days: int = 45
    candidate_dir: Path = Path("data/candidates")
    # Default authoring backend. "claude-code" drives `claude -p`; "opencode" keeps
    # the legacy OpenCode ladder (author_model_cheap → author_model_strong);
    # "command-code" drives `commandcode -p` (DeepSeek V4 Flash by default).
    author_harness: str = "claude-code"
    author_model_cheap: str = "anthropic/claude-haiku-4-5"
    author_model_strong: str = "anthropic/claude-opus-4-8"
    # Claude Code model specs carry an optional @effort suffix (low|medium|high|...).
    # Default is Opus 4.8 at low effort; the high-effort fallback is opt-in
    # (--fallback); agentic browsing runs at low effort.
    claude_code_model: str = "opus@low"
    claude_code_fallback_model: str = "opus@high"
    claude_code_agentic_model: str = "opus@low"
    # Command Code drives `commandcode -p` with --yolo/--trust/--skip-onboarding.
    # Default model is DeepSeek V4 Flash. Command Code has no @effort concept, so
    # the high-effort --fallback knob does not apply.
    command_code_model: str = "deepseek/deepseek-v4-flash"
    # Cap on Command Code conversation turns in -p mode. Its own default (10) is
    # far too low for an author-then-verify loop (the agent hits the cap and
    # returns an incomplete config). The real cost/runaway ceiling is the 600s
    # subprocess timeout, not the turn count, so this is set generously high: high
    # enough to never bind on legitimate authoring (~3-5x any realistic
    # fetch→build→self-correct loop), while still cutting off a degenerate agent
    # stuck in a fast tool-call loop. Not truly unbounded for exactly that reason.
    command_code_max_turns: int = 100
    # Kimchi drives `kimchi -p --yolo` (non-interactive, full toolset, no
    # classifier). Default model is GLM-5.2 (the fp8 quant). Kimchi's --model
    # accepts a `:thinking` suffix instead of an @effort split, so the spec is
    # passed through verbatim and the high-effort --fallback knob does not apply.
    kimchi_model: str = "kimchi-dev/glm-5.2-fp8"
    # Cursor drives `agent -p --yolo --trust` (non-interactive print mode, full
    # toolset, workspace trusted). Default model is composer-2.5. Cursor has no
    # @effort concept, so the high-effort --fallback knob does not apply.
    cursor_model: str = "composer-2.5"
    author_max_calls: int = 50
    # Hard subprocess ceiling for one single-shot harness call. A tool-enabled
    # agent that re-fetches the live page (WebFetch) to verify its selectors needs
    # headroom past a bare generation; 300s guillotined those recoveries mid-flight
    # (3/10 timeouts on a byteplus eval), so the floor is 600s. The agentic browse
    # stage keeps its own (longer) ceiling.
    author_harness_timeout: float = 600.0
    # Corrective re-prompts on the single-shot stage: a rejected config is re-fed
    # its own verify error so the tool-enabled agent can fix it. 0 disables.
    author_feedback_retries: int = 1
    author_page_budget: int = 8
    author_token_budget: int = 200_000
    bespoke_dir: Path = Path("data/bespoke")
    blocklist_path: Path | None = None
    discover_concurrency: int = 16
    author_concurrency: int = 4
    # Hard cap on concurrent headless-browser (Playwright) renders, independent of
    # discover_concurrency: static httpx fetches stay parallel, but only this many
    # chromium instances launch at once. Too many concurrent browsers caused render
    # timeouts. Set to 1 to render synchronously.
    render_concurrency: int = 2

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
