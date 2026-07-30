from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://card_reward:card_reward@localhost:5432/card_reward"
    app_base_url: str = "http://localhost:8000"
    activity_timezone: str = "Asia/Shanghai"
    gift_link_ttl_days: int = 7
    gift_link_daily_creation_limit: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

