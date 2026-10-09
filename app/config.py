"""Validated runtime configuration."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables or `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    category: str = "cold email generation"
    products_per_day: int = Field(default=2, ge=2, le=2)
    max_pages_per_product: int = Field(default=3, ge=1, le=30)
    max_fact_chars_per_page: int = Field(default=12000, ge=4000, le=50000)
    max_page_picker_urls: int = Field(default=24, ge=8, le=60)
    min_screenshots: int = Field(default=3, ge=1, le=20)
    allow_established_alternatives: bool = True
    github_search_enabled: bool = True
    github_token: SecretStr | None = None
    pagespeed_audit_enabled: bool = True
    pagespeed_api_key: SecretStr | None = None
    product_hunt_search_enabled: bool = True
    product_hunt_algolia_app_id: str = "0H4SMABBSG"
    product_hunt_algolia_search_key: SecretStr = SecretStr("9670d2d619b9d07859448d7628eea5f3")
    product_hunt_algolia_index: str = "Post_production"
    max_gap_fill_iterations: int = Field(default=1, ge=0, le=10)
    max_run_cost_usd: float = Field(default=5.0, gt=0)
    max_run_minutes: int = Field(default=20, gt=0)
    llm_fast_input_usd_per_million: float = 1.0
    llm_fast_output_usd_per_million: float = 5.0
    llm_strong_input_usd_per_million: float = 3.0
    llm_strong_output_usd_per_million: float = 15.0
    llm_provider: str = "gemini"
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    groq_api_base_url: str = "https://api.groq.com/openai/v1/chat/completions"
    llm_model_fast: str = "gemini-3.1-flash-lite"
    llm_model_strong: str = "gemini-3.6-flash"
    llm_model_vision: str = "gemini-3.6-flash"
    gemini_fast_input_usd_per_million: float = 0.0
    gemini_fast_output_usd_per_million: float = 0.0
    gemini_strong_input_usd_per_million: float = 0.0
    gemini_strong_output_usd_per_million: float = 0.0
    groq_fast_input_usd_per_million: float = 0.075
    groq_fast_output_usd_per_million: float = 0.30
    groq_fallback_model: str = "openai/gpt-oss-120b"
    groq_json_fallback_model: str = "openai/gpt-oss-20b"
    groq_fallback_input_usd_per_million: float = 0.15
    groq_fallback_output_usd_per_million: float = 0.60
    groq_vision_input_usd_per_million: float = 0.80
    groq_vision_output_usd_per_million: float = 4.0
    search_provider: str = "tavily"
    search_api_base_url: str = "https://api.tavily.com/search"
    sitemap_fallback_paths: str = ""
    search_api_key: SecretStr | None = None
    product_hunt_token: SecretStr | None = None
    user_agent: str = "ProductResearchBot/1.0 (+contact@example.com)"
    database_url: str = "sqlite:///data/app.db"
    screenshots_dir: str = "data/screenshots"
    admin_api_key: SecretStr | None = None
    schedule_cron: str = "0 6 * * *"
    log_level: str = "INFO"

    @field_validator("category")
    @classmethod
    def category_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("category must not be blank")
        return value

    @model_validator(mode="after")
    def llm_models_match_provider(self):
        # Existing .env files may still carry Claude model IDs after changing
        # only LLM_PROVIDER. Replace those incompatible defaults automatically.
        if self.llm_provider.lower() == "gemini":
            if self.llm_model_fast.startswith(("claude-", "openai/", "qwen/")):
                self.llm_model_fast = "gemini-3.1-flash-lite"
            if self.llm_model_strong.startswith(("claude-", "openai/", "qwen/")):
                self.llm_model_strong = "gemini-3.6-flash"
            if self.llm_model_vision.startswith(("claude-", "openai/", "qwen/")):
                self.llm_model_vision = "gemini-3.6-flash"
        elif self.llm_provider.lower() == "groq":
            if self.llm_model_fast.startswith(("claude-", "gemini-")):
                self.llm_model_fast = "openai/gpt-oss-20b"
            if self.llm_model_strong.startswith(("claude-", "gemini-")):
                self.llm_model_strong = "openai/gpt-oss-20b"
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings, suitable for FastAPI dependency injection."""
    return Settings()
