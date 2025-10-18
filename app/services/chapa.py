import httpx
import logging
from typing import Dict, Any

from app.config import settings
from app.schemas.payment import ChapaInitializeRequest, ChapaInitializeResponse, ChapaVerifyResponse
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)

class ChapaService:
    def __init__(self):
        self.base_url = settings.CHAPA_BASE_URL
        self.api_key = settings.CHAPA_API_KEY
        self.secret_key = settings.CHAPA_SECRET_KEY # Not directly used for API calls, but good to have
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    @async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, httpx.HTTPStatusError))
    async def initialize_payment(self, payment_data: ChapaInitializeRequest) -> ChapaInitializeResponse:
        url = f"{self.base_url}/transaction/initialize"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payment_data.model_dump(), headers=self.headers, timeout=10)
                response.raise_for_status()
                return ChapaInitializeResponse(**response.json())
            except httpx.RequestError as exc:
                logger.error(f"Chapa initialize_payment RequestError: {exc}")
                raise
            except httpx.HTTPStatusError as exc:
                logger.error(f"Chapa initialize_payment HTTPStatusError: {exc.response.status_code} - {exc.response.text}")
                raise

    @async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, httpx.HTTPStatusError))
    async def verify_payment(self, transaction_reference: str) -> ChapaVerifyResponse:
        url = f"{self.base_url}/transaction/verify/{transaction_reference}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
                return ChapaVerifyResponse(**response.json())
            except httpx.RequestError as exc:
                logger.error(f"Chapa verify_payment RequestError: {exc}")
                raise
            except httpx.HTTPStatusError as exc:
                logger.error(f"Chapa verify_payment HTTPStatusError: {exc.response.status_code} - {exc.response.text}")
                raise

    # Webhook verification (simplified for sandbox, but in production, verify signature)
    def verify_webhook_signature(self, payload: Dict[str, Any], signature: str) -> bool:
        # In a real scenario, you would compute a hash of the payload using your secret key
        # and compare it with the signature provided in the header.
        # For Chapa sandbox, this might be a placeholder or rely on IP whitelisting.
        # For now, we'll return True for simplicity in sandbox, but this MUST be implemented securely.
        logger.warning("Webhook signature verification is currently a placeholder. Implement actual signature verification in production!")
        return True

chapa_service = ChapaService()
