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
