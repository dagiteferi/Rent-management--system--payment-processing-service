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

    # Fixed Payment Settings
    FIXED_AMOUNT: int = 500
    CURRENCY: str = "ETB"

    # Chapa specific settings
    CHAPA_BASE_URL: str = "https://api.chapa.co/v1"
    CHAPA_TEST_MODE: bool = True # Set to True to use Chapa sandbox/test keys
    CHAPA_WEBHOOK_URL: str = "/api/v1/webhook/chapa" 
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # BASE_URL: str = "https://rent-management-system-payment.onrender.com"
    BASE_URL: str = "https://unconceding-annabella-nonsimulate.ngrok-free.dev"

    PAYMENT_SERVICE_API_KEY: str # New API key for service-to-service authentication
    FRONTEND_REDIRECT_URL: str # URL for frontend redirect after payment

    # Payment timeout settings
    PAYMENT_TIMEOUT_DAYS: int = 7

settings = Settings()
