from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    CHAPA_API_KEY: str
    CHAPA_SECRET_KEY: str
    CHAPA_WEBHOOK_SECRET: str 
    JWT_SECRET: str
    USER_MANAGEMENT_URL: str
    NOTIFICATION_SERVICE_URL: str
    PROPERTY_LISTING_SERVICE_URL: str
    ENCRYPTION_KEY: str #
    REDIS_URL: str #

    # Chapa specific settings
    CHAPA_BASE_URL: str = "https://api.chapa.co/v1"
    CHAPA_WEBHOOK_URL: str = "/api/v1/webhook/chapa" 
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    BASE_URL: str = "http://localhost:8120"
    PAYMENT_SERVICE_API_KEY: str # New API key for service-to-service authentication

    # Payment timeout settings
    PAYMENT_TIMEOUT_DAYS: int = 7

settings = Settings()
