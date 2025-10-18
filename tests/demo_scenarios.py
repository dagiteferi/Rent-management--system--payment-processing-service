import asyncio
import httpx
import json
import uuid
from unittest.mock import patch, AsyncMock
from app.config import settings
from app.core.logging import logger
from app.models.payment import PaymentStatus

# Mock settings for testing encryption key and webhook secret
@patch('app.config.settings.ENCRYPTION_KEY', "a_32_byte_secret_key_for_aes_encryption")
@patch('app.config.settings.CHAPA_WEBHOOK_SECRET', "test_webhook_secret")
async def simulate_error_scenarios(base_url: str, owner_jwt: str):
    logger.info("Starting demo error simulation scenarios...")

    async with httpx.AsyncClient(base_url=base_url) as client:
        # Scenario 1: Simulate invalid Chapa test card (Chapa API returns failure)
        logger.info("\n--- Scenario 1: Invalid Chapa Test Card (Chapa API returns failure) ---")
        property_id_1 = uuid.uuid4()
        request_id_1 = uuid.uuid4()
        payment_data_1 = {"request_id": str(request_id_1), "property_id": str(property_id_1), "user_id": str(uuid.uuid4()), "amount": 100.00}

        with patch('app.services.chapa.chapa_service.initialize_payment', new_callable=AsyncMock) as mock_init_payment:
            mock_init_payment.return_value = AsyncMock(
                status="failed",
                message="Invalid card details",
                data={}
            )
            try:
                response = await client.post(
                    "/api/v1/payments/initiate",
                    json=payment_data_1,
                    headers={
                        "Authorization": f"Bearer {owner_jwt}",
                        "Content-Type": "application/json"
                    }
                )
                logger.info(f"Initiate payment response (expected 400): Status {response.status_code}, Detail: {response.json().get('detail')}")
                assert response.status_code == 400
            except Exception as e:
                logger.error(f"Error during Scenario 1: {e}")

        # Scenario 2: Simulate User Management Microservice network timeout
        logger.info("\n--- Scenario 2: User Management Microservice Network Timeout ---")
        property_id_2 = uuid.uuid4()
        request_id_2 = uuid.uuid4()
        payment_data_2 = {"request_id": str(request_id_2), "property_id": str(property_id_2), "user_id": str(uuid.uuid4()), "amount": 100.00}

        with patch('app.dependencies.auth.httpx.AsyncClient.post', new_callable=AsyncMock) as mock_user_management_post:
            mock_user_management_post.side_effect = httpx.RequestError("Connection timed out", request=httpx.Request("POST", "http://user-management:8000/api/v1/auth/verify"))
            try:
                response = await client.post(
                    "/api/v1/payments/initiate",
                    json=payment_data_2,
                    headers={
                        "Authorization": f"Bearer {owner_jwt}",
                        "Content-Type": "application/json"
                    }
                )
                logger.info(f"Initiate payment response (expected 503): Status {response.status_code}, Detail: {response.json().get('detail')}")
                assert response.status_code == 503
            except Exception as e:
                logger.error(f"Error during Scenario 2: {e}")

        # Scenario 3: Simulate delayed webhook (payment times out before webhook arrives)
        logger.info("\n--- Scenario 3: Delayed Webhook (Payment Times Out) ---")
        property_id_3 = uuid.uuid4()
        request_id_3 = uuid.uuid4()
        owner_user_id_3 = uuid.uuid4()
        payment_data_3 = {"request_id": str(request_id_3), "property_id": str(property_id_3), "user_id": str(owner_user_id_3), "amount": 100.00}

        # Mock auth for initiate
        with patch('app.dependencies.auth.get_current_owner', new_callable=AsyncMock) as mock_owner_auth:
            mock_owner_auth.return_value = AsyncMock(
                user_id=owner_user_id_3,
                role="Owner",
                email="owner3@example.com",
                phone_number="+251911123458",
                preferred_language="en"
            )
            # Mock Chapa init to return a valid checkout URL
            with patch('app.services.chapa.chapa_service.initialize_payment', new_callable=AsyncMock) as mock_init_payment:
                mock_init_payment.return_value = AsyncMock(
                    status="success",
                    message="Payment link generated successfully",
                    data={
                        "checkout_url": "https://chapa.co/checkout/delayed-link",
                        "transaction_ref": "delayed-tx-ref"
                    }
                )
                init_response = await client.post("/api/v1/payments/initiate", json=payment_data_3, headers={
                    "Authorization": f"Bearer {owner_jwt}",
                    "Content-Type": "application/json"
                })
                payment_id_3 = init_response.json()['id']
                logger.info(f"Initiated payment {payment_id_3} for delayed webhook scenario.")

        # Manually set payment to FAILED (simulating timeout job)
        from app.main import AsyncSessionLocal
        from app.models.payment import Payment
        from app.core.security import encrypt_data
        async with AsyncSessionLocal() as db:
            payment_in_db = await db.get(Payment, uuid.UUID(payment_id_3))
            payment_in_db.status = PaymentStatus.FAILED
            payment_in_db.chapa_tx_ref = encrypt_data("delayed-tx-ref") # Ensure it's encrypted
            await db.commit()
            await db.refresh(payment_in_db)
            logger.info(f"Manually set payment {payment_id_3} to FAILED to simulate timeout.")

        # Now send the webhook (it should be ignored or handled as already failed)
        webhook_payload_3 = {
            "event": "charge.success",
            "data": {
                "tx_ref": "delayed-tx-ref",
                "status": "success",
                "amount": 100,
                "currency": "ETB",
                "meta": {"user_id": str(owner_user_id_3), "property_id": str(property_id_3)}
            }
        }
        payload_body_3 = json.dumps(webhook_payload_3).encode('utf-8')
        from tests.conftest import generate_chapa_webhook_signature
        signature_3 = generate_chapa_webhook_signature()(payload_body_3, secret=settings.CHAPA_WEBHOOK_SECRET)

        webhook_response = await client.post("/api/v1/webhook/chapa", json=webhook_payload_3, headers={"X-Chapa-Signature": signature_3})
        logger.info(f"Webhook response for delayed payment (expected 200, message about already processed): Status {webhook_response.status_code}, Detail: {webhook_response.json().get('message')}")
        assert webhook_response.status_code == 200
        assert "already processed" in webhook_response.json()['message']

        # Verify status is still FAILED in DB
        async with AsyncSessionLocal() as db:
            payment_in_db_after_webhook = await db.get(Payment, uuid.UUID(payment_id_3))
            assert payment_in_db_after_webhook.status == PaymentStatus.FAILED

    logger.info("Demo error simulation scenarios completed.")

if __name__ == "__main__":
    # This part is for running the script directly, e.g., for a demo
    # In a real test environment, you'd use pytest.
    # You'll need to replace this with a valid JWT for an Owner user.
    # For a quick demo, you might hardcode a dummy JWT if User Management isn't running.
    DUMMY_OWNER_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmYjQxYjQxMi0xYjQxLTRiNDEtYjQxMi0xYjQxMmI0MTJiNDEiLCJyb2xlIjoiT3duZXIiLCJleHAiOjE3NjI4MjI0MDB9.some_signature_here" # Replace with a valid JWT
    BASE_URL = "http://localhost:8000"

    # Configure logging for direct script execution
    from app.core.logging import configure_logging
    configure_logging()

    asyncio.run(simulate_error_scenarios(BASE_URL, DUMMY_OWNER_JWT))