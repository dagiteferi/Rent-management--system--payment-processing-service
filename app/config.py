from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    CHAPA_API_KEY: str
    CHAPA_SECRET_KEY: str
    JWT_SECRET: str
    USER_MANAGEMENT_URL: str
    NOTIFICATION_SERVICE_URL: str
    PROPERTY_LISTING_SERVICE_URL: str
    ENCRYPTION_KEY: str # Must be 32 bytes for AES-256

    # Chapa specific settings
    CHAPA_BASE_URL: str = "https://api.chapa.co/v1"
    CHAPA_WEBHOOK_URL: str = "/api/v1/webhook/chapa" # This should be the public URL of your service

    # Payment timeout settings
    PAYMENT_TIMEOUT_DAYS: int = 7

settings = Settings()
