import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
import uuid
from datetime import datetime, timedelta

from app.models.payment import Payment, PaymentStatus
from app.core.security import encrypt_data, decrypt_data
from app.config import settings

# Mock settings for testing encryption key
@pytest.fixture(autouse=True)
def mock_settings_encryption_key():
    with patch('app.config.settings.ENCRYPTION_KEY', "a_32_byte_secret_key_for_aes_encryption"):
        yield

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Payment Processing Microservice is running"}

@pytest.mark.asyncio
async def test_initiate_payment_success(
    client: AsyncClient,
    mock_chapa_service,
    mock_auth_dependency,
    mock_notification_service,
    test_db
):
    owner_user_id = mock_auth_dependency['owner'].return_value['user_id']
    property_id = uuid.uuid4()
    payment_data = {"property_id": str(property_id), "user_id": str(owner_user_id), "amount": 100.00}

    response = await client.post("/api/v1/payments/initiate", json=payment_data)

    assert response.status_code == 202
    response_json = response.json()
    assert response_json['status'] == PaymentStatus.PENDING.value
    assert response_json['property_id'] == str(property_id)
    assert response_json['user_id'] == str(owner_user_id)
    assert "checkout_url" in response_json['chapa_tx_ref'] # chapa_tx_ref returns checkout_url for simplicity

    mock_chapa_service.initialize_payment.assert_called_once()
    mock_notification_service.assert_called_once()

    # Verify payment is in DB
    payment_in_db = await test_db.get(Payment, uuid.UUID(response_json['id']))
    assert payment_in_db is not None
    assert payment_in_db.status == PaymentStatus.PENDING
    assert decrypt_data(payment_in_db.chapa_tx_ref).startswith("tx-")

@pytest.mark.asyncio
async def test_initiate_payment_not_owner(
    client: AsyncClient,
    mock_auth_dependency
):
    # Mock get_current_owner to raise 403
    mock_auth_dependency['owner'].side_effect = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Owners can perform this action")

    property_id = uuid.uuid4()
    payment_data = {"property_id": str(property_id), "user_id": str(uuid.uuid4()), "amount": 100.00}

    response = await client.post("/api/v1/payments/initiate", json=payment_data)

    assert response.status_code == 403
    assert response.json() == {"detail": "Only Owners can perform this action"}

@pytest.mark.asyncio
async def test_get_payment_status_success(
    client: AsyncClient,
    mock_auth_dependency,
    create_payment,
    test_db
):
    user_id = mock_auth_dependency['user'].return_value['user_id']
    payment = await create_payment(user_id=user_id, chapa_tx_ref=encrypt_data("test-tx-ref-123"))

    response = await client.get(f"/api/v1/payments/{payment.id}/status")

    assert response.status_code == 200
    response_json = response.json()
    assert response_json['id'] == str(payment.id)
    assert response_json['status'] == PaymentStatus.PENDING.value
    assert response_json['chapa_tx_ref'] == "********" # Masked

@pytest.mark.asyncio
async def test_get_payment_status_not_found(
    client: AsyncClient,
    mock_auth_dependency
):
    non_existent_id = uuid.uuid4()
    response = await client.get(f"/api/v1/payments/{non_existent_id}/status")
    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}

@pytest.mark.asyncio
async def test_get_payment_status_unauthorized_user(
    client: AsyncClient,
    mock_auth_dependency,
    create_payment,
    test_db
):
    # Create a payment for a different user
    other_user_id = uuid.uuid4()
    payment = await create_payment(user_id=other_user_id, chapa_tx_ref=encrypt_data("test-tx-ref-456"))

    # Current user is mocked as a Tenant, not the owner of this payment
    current_user_id = mock_auth_dependency['user'].return_value['user_id']
    assert current_user_id != other_user_id

    response = await client.get(f"/api/v1/payments/{payment.id}/status")
    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorized to view this payment status"}

@pytest.mark.asyncio
async def test_chapa_webhook_success(
    client: AsyncClient,
    mock_chapa_service,
    mock_notification_service,
    mock_property_listing_service,
    mock_get_user_details_for_notification,
    create_payment,
    test_db
):
    original_tx_ref = "webhook-test-tx-ref-789"
    encrypted_tx_ref = encrypt_data(original_tx_ref)
    payment = await create_payment(chapa_tx_ref=encrypted_tx_ref, status=PaymentStatus.PENDING)

    webhook_payload = {
        "event": "charge.success",
        "data": {
            "tx_ref": original_tx_ref,
            "status": "success",
            "amount": 100,
            "currency": "ETB",
            "customization": {"title": "Payment for Property", "description": ""},
            "meta": {"user_id": str(payment.user_id), "property_id": str(payment.property_id)}
        }
    }

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload)

    assert response.status_code == 200
    assert response.json() == {"message": "Webhook processed successfully"}

    mock_chapa_service.verify_webhook_signature.assert_called_once()
    mock_chapa_service.verify_payment.assert_called_once_with(original_tx_ref)
    mock_property_listing_service.assert_called_once_with(payment.property_id)
    mock_notification_service.assert_called_once()
    mock_get_user_details_for_notification.assert_called_once_with(payment.user_id)

    # Verify payment status updated in DB
    updated_payment = await test_db.get(Payment, payment.id)
    assert updated_payment.status == PaymentStatus.SUCCESS

@pytest.mark.asyncio
async def test_chapa_webhook_failed_status(
    client: AsyncClient,
    mock_chapa_service,
    mock_notification_service,
    create_payment,
    test_db
):
    original_tx_ref = "webhook-test-tx-ref-failed"
    encrypted_tx_ref = encrypt_data(original_tx_ref)
    payment = await create_payment(chapa_tx_ref=encrypted_tx_ref, status=PaymentStatus.PENDING)

    # Mock Chapa verification to return a failed status
    mock_chapa_service.verify_payment.return_value = AsyncMock(
        status="success", # Chapa API might return success for the call, but data.status is failed
        message="Payment failed",
        data={
            "status": "failed",
            "amount": 100,
            "currency": "ETB",
            "tx_ref": original_tx_ref
        }
    )

    webhook_payload = {
        "event": "charge.failed",
        "data": {
            "tx_ref": original_tx_ref,
            "status": "failed",
            "amount": 100,
            "currency": "ETB",
            "customization": {"title": "Payment for Property", "description": ""},
            "meta": {"user_id": str(payment.user_id), "property_id": str(payment.property_id)}
        }
    }

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload)

    assert response.status_code == 200
    assert response.json() == {"message": "Webhook processed successfully"}

    mock_chapa_service.verify_payment.assert_called_once_with(original_tx_ref)
    mock_notification_service.assert_called_once()

    # Verify payment status updated in DB
    updated_payment = await test_db.get(Payment, payment.id)
    assert updated_payment.status == PaymentStatus.FAILED

@pytest.mark.asyncio
async def test_chapa_webhook_payment_not_found(
    client: AsyncClient,
    mock_chapa_service
):
    webhook_payload = {
        "event": "charge.success",
        "data": {
            "tx_ref": "non-existent-tx-ref",
            "status": "success",
            "amount": 100,
            "currency": "ETB",
            "customization": {"title": "Payment for Property", "description": ""},
            "meta": {"user_id": str(uuid.uuid4()), "property_id": str(uuid.uuid4())}
        }
    }

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload)

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found or not in PENDING state"}

@pytest.mark.asyncio
async def test_timeout_pending_payments_job(
    test_db,
    create_payment,
    mock_notification_service
):
    # Create a pending payment older than 7 days
    old_pending_payment = await create_payment(
        status=PaymentStatus.PENDING,
        created_at=datetime.now() - timedelta(days=8),
        chapa_tx_ref=encrypt_data("old-pending-tx-ref")
    )

    # Create a recent pending payment
    recent_pending_payment = await create_payment(
        status=PaymentStatus.PENDING,
        created_at=datetime.now() - timedelta(days=1),
        chapa_tx_ref=encrypt_data("recent-pending-tx-ref")
    )

    # Create a successful payment (should not be affected)
    successful_payment = await create_payment(
        status=PaymentStatus.SUCCESS,
        created_at=datetime.now() - timedelta(days=10),
        chapa_tx_ref=encrypt_data("successful-tx-ref")
    )

    from app.main import timeout_pending_payments
    await timeout_pending_payments()

    # Verify old pending payment is FAILED
    updated_old_payment = await test_db.get(Payment, old_pending_payment.id)
    assert updated_old_payment.status == PaymentStatus.FAILED
    mock_notification_service.assert_called_once() # Only one notification for the timed out payment

    # Verify recent pending payment is still PENDING
    updated_recent_payment = await test_db.get(Payment, recent_pending_payment.id)
    assert updated_recent_payment.status == PaymentStatus.PENDING

    # Verify successful payment is still SUCCESS
    updated_successful_payment = await test_db.get(Payment, successful_payment.id)
    assert updated_successful_payment.status == PaymentStatus.SUCCESS

@pytest.mark.asyncio
async def test_encryption_decryption():
    original_data = "some_secret_chapa_reference_123"
    encrypted = encrypt_data(original_data)
    decrypted = decrypt_data(encrypted)
    assert original_data == decrypted
    assert encrypted != original_data

    # Test with a different key (should fail decryption)
    with patch('app.config.settings.ENCRYPTION_KEY', "another_32_byte_secret_key_for_aes"):
        with pytest.raises(Exception): # Fernet.decrypt raises various exceptions
            decrypt_data(encrypted)
