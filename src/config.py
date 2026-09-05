from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    fernet_key: str
    invite_salt: str = Field(min_length=32)
    database_url: str
    log_level: str = "INFO"


class TestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pat: str = ""
    gitlab_url: str = "https://gl.example.com"
    gitlab_scope_path: str = ""
