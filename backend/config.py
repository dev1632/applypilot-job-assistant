"""Application settings. Secrets come from the environment, .env, or macOS Keychain."""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    applypilot_data_dir: Path = Path("./data")
    applypilot_use_llm: bool = True
    applypilot_max_posting_age_hours: int = 24
    applypilot_require_verified_freshness: bool = False
    applypilot_http_delay_seconds: float = 1.0
    applypilot_dev_routes: bool = False

    @property
    def data_dir(self) -> Path:
        path = self.applypilot_data_dir.expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        (path / "resumes").mkdir(exist_ok=True)
        return path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'applypilot.db'}"

    def resolve_openai_key(self) -> str | None:
        if self.openai_api_key:
            return self.openai_api_key
        return _keychain_secret("applypilot", "OPENAI_API_KEY")


def _keychain_secret(service: str, account: str) -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
