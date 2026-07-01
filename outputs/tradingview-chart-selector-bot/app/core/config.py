from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    cache_db_path: str = Field(default="data/cache.sqlite3", alias="CACHE_DB_PATH")
    cache_ttl_seconds: int = Field(default=86_400, alias="CACHE_TTL_SECONDS")
    max_candles: int = Field(default=1000, alias="MAX_CANDLES")
    quote_policy_year: int = Field(default=2015, alias="QUOTE_POLICY_YEAR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
