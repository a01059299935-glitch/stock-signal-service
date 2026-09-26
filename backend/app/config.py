from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")
    database_url: str = "postgresql+psycopg://stocks:stocks@db:5432/stocks"
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_max_storage_bytes: int = 9_000_000_000
    backup_encryption_key: str = ""
    app_environment: str = "development"
    app_username: str = ""
    app_password: str = ""
    finnhub_api_key: str = ""
    opendart_api_key: str = ""
    sec_user_agent: str = "StockSignal contact@example.com"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    openai_api_key: str = ""
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    alert_move_10m_pct: float = 3.0
    market_data_interval_seconds: int = 300
    realtime_show_milliseconds: bool = False
    weight_news: float = 0.30
    weight_trend: float = 0.20
    weight_volume: float = 0.15
    weight_technical: float = 0.15
    weight_fundamental: float = 0.15
    weight_market: float = 0.05


settings = Settings()
