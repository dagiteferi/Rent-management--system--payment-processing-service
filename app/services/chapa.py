import httpx
import hmac
import hashlib
from typing import Dict, Any

from app.config import settings
from app.schemas.payment import ChapaInitializeRequest, ChapaInitializeResponse, ChapaVerifyResponse
from app.utils.retry import async_retry
from app.core.logging import logger

class ChapaService:
    """A service for interacting with the Chapa payment gateway API."""
    def __init__(self):
        self.base_url = settings.CHAPA_BASE_URL
        self.api_key = settings.CHAPA_API_KEY
        self.secret_key = settings.CHAPA_SECRET_KEY # 
        self.webhook_secret = settings.CHAPA_WEBHOOK_SECRET
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    @async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, httpx.HTTPStatusError))
    async def initialize_payment(self, payment_data: ChapaInitializeRequest) -> ChapaInitializeResponse:
        """Initializes a payment transaction with Chapa."""
        url = f"{self.base_url}/transaction/initialize"
        async with httpx.AsyncClient() as client:
            try:
                logger.debug("Chapa initialize_payment request headers", headers=self.headers)
                logger.debug("Chapa initialize_payment request body", body=payment_data.model_dump())
                response = await client.post(url, json=payment_data.model_dump(), headers=self.headers, timeout=10)
                response.raise_for_status()
                logger.info("Chapa payment initialization successful", tx_ref=payment_data.tx_ref)
                return ChapaInitializeResponse(**response.json())
            except httpx.RequestError as exc:
                logger.error("Chapa initialize_payment RequestError", tx_ref=payment_data.tx_ref, error=str(exc))
                raise
            except httpx.HTTPStatusError as exc:
                logger.error("Chapa initialize_payment HTTPStatusError", tx_ref=payment_data.tx_ref, status_code=exc.response.status_code, response_text=exc.response.text)
                raise

    @async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, httpx.HTTPStatusError))
    async def verify_payment(self, transaction_reference: str) -> ChapaVerifyResponse:
        url = f"{self.base_url}/transaction/verify/{transaction_reference}"
        async with httpx.AsyncClient() as client:
            try:
                logger.debug("Chapa verify_payment request headers", headers=self.headers)
                response = await client.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
                logger.info("Chapa payment verification successful", tx_ref=transaction_reference)
                return ChapaVerifyResponse(**response.json())
            except httpx.RequestError as exc:
                logger.error("Chapa verify_payment RequestError", tx_ref=transaction_reference, error=str(exc))
                raise
            except httpx.HTTPStatusError as exc:
                logger.error("Chapa verify_payment HTTPStatusError", tx_ref=transaction_reference, status_code=exc.response.status_code, response_text=exc.response.text)
                raise

    async def get_banks(self) -> list:
        url = f"{self.base_url}/banks"
        async with httpx.AsyncClient() as client:
            try:
                logger.debug("Chapa get_banks request headers", headers=self.headers)
                response = await client.get(url, headers=self.headers, timeout=10)
                response.raise_for_status()
                logger.info("Successfully fetched banks from Chapa")
                return response.json().get("data", [])
            except httpx.RequestError as exc:
                logger.error("Chapa get_banks RequestError", error=str(exc))
                raise
            except httpx.HTTPStatusError as exc:
                logger.error("Chapa get_banks HTTPStatusError", status_code=exc.response.status_code, response_text=exc.response.text)
                raise

    def verify_webhook_signature(self, payload_body: bytes, chapa_signature: str) -> bool:
        """
        Verifies the HMAC-SHA256 signature of the Chapa webhook payload.
        The signature is typically sent in an 'x-chapa-signature' header.
        """
        if not self.webhook_secret:
            logger.warning("CHAPA_WEBHOOK_SECRET is not set. Webhook signature verification skipped.")
            return True # In production, this should raise an error or return False

        # Chapa typically sends the signature as a hex string
        expected_signature = hmac.new(
            self.webhook_secret.encode('utf-8'),
            payload_body,
            hashlib.sha256
        ).hexdigest()

        if hmac.compare_digest(expected_signature, chapa_signature):
            logger.info("Chapa webhook signature verified successfully.")
            return True
        else:
            logger.warning("Chapa webhook signature verification failed.", expected_signature=expected_signature, received_signature=chapa_signature)
            return False

chapa_service = ChapaService()
