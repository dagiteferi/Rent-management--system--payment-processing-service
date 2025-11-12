import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
import uuid
from datetime import datetime, timedelta
import json

from app.models.payment import Payment, PaymentStatus
from app.core.security import encrypt_data, decrypt_data
from app.config import settings
from app.routers.payments import metrics_counters # Import the metrics counter

# Mock settings for testing encryption key and webhook secret
@pytest.fixture(autouse=True)
def mock_settings_encryption_key_and_webhook_secret():
    with patch('app.config.settings.ENCRYPTION_KEY', "a_32_byte_secret_key_for_aes_encryption"), \
         patch('app.config.settings.CHAPA_WEBHOOK_SECRET', "test_webhook_secret"):
        yield

@pytest.mark.asyncio
async def test_health_check_success(client: AsyncClient, mock_chapa_service):
    # Mock DB connection to be successful (default behavior of test_db fixture)
    # Mock Chapa service to return success for get_banks
    mock_chapa_service.get_banks.return_value = AsyncMock(status="success", data=[{"name": "Bank A"}])

    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "db": "ok", "chapa_api": "ok"}

@pytest.mark.asyncio
async def test_health_check_db_failure(client: AsyncClient, mock_chapa_service, test_db):
    # Mock DB connection to fail
    with patch.object(test_db, 'execute', side_effect=Exception("DB connection error")):
        response = await client.get("/api/v1/health")
        assert response.status_code == 503
        assert response.json()['status'] == "healthy"
        assert response.json()['db'] == "error"
        assert "DB connection error" in response.json()['db_error']
        assert response.json()['chapa_api'] == "ok"

@pytest.mark.asyncio
async def test_health_check_chapa_failure(client: AsyncClient, mock_chapa_service):
    # Mock Chapa service to fail for get_banks
    mock_chapa_service.get_banks.side_effect = Exception("Chapa API error")

    response = await client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()['status'] == "healthy"
    assert response.json()['db'] == "ok"
    assert response.json()['chapa_api'] == "error"
    assert "Chapa API error" in response.json()['chapa_api_error']

@pytest.mark.asyncio
async def test_metrics_endpoint(client: AsyncClient):
    # Reset counters for a clean test
    for key in metrics_counters:
        metrics_counters[key] = 0

    response = await client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert response.json() == {
        "total_payments": 0,
        "pending_payments": 0,
        "success_payments": 0,
        "failed_payments": 0,
        "webhook_calls": 0,
        "initiate_calls": 0,
        "status_calls": 0,
        "timeout_jobs_run": 0,
    }

    # Simulate some calls to update metrics
    metrics_counters["initiate_calls"] += 1
    metrics_counters["total_payments"] += 1
    metrics_counters["pending_payments"] += 1
    metrics_counters["webhook_calls"] += 1
    metrics_counters["success_payments"] += 1
    metrics_counters["pending_payments"] -= 1 # From webhook success

    response = await client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert response.json()["initiate_calls"] == 1
    assert response.json()["total_payments"] == 1
    assert response.json()["pending_payments"] == 0
    assert response.json()["success_payments"] == 1
    assert response.json()["webhook_calls"] == 1

@pytest.mark.asyncio
async def test_initiate_payment_success(
    client: AsyncClient,
    mock_chapa_service,
    mock_auth_dependency,
    mock_notification_service,
    test_db
):
    owner_user_id = mock_auth_dependency['owner'].return_value.user_id
    property_id = uuid.uuid4()
    request_id = uuid.uuid4()
    payment_data = {"request_id": str(request_id), "property_id": str(property_id), "user_id": str(owner_user_id), "amount": 100.00}

    response = await client.post("/api/v1/payments/initiate", json=payment_data)

    assert response.status_code == 202
    response_json = response.json()
    assert response_json['status'] == PaymentStatus.PENDING.value
    assert response_json['property_id'] == str(property_id)
    assert response_json['user_id'] == str(owner_user_id)
    assert "checkout_url" in response_json['chapa_tx_ref'] # chapa_tx_ref returns checkout_url for simplicity

    mock_chapa_service.initialize_payment.assert_called_once()
    mock_notification_service.send_notification.assert_called_once()

    # Verify payment is in DB
    payment_in_db = await test_db.get(Payment, uuid.UUID(response_json['id']))
    assert payment_in_db is not None
    assert payment_in_db.status == PaymentStatus.PENDING
    assert decrypt_data(payment_in_db.chapa_tx_ref).startswith("tx-")
    assert payment_in_db.request_id == request_id

@pytest.mark.asyncio
async def test_initiate_payment_idempotency_same_request_id(
    client: AsyncClient,
    mock_chapa_service,
    mock_auth_dependency,
    mock_notification_service,
    test_db,
    create_payment
):
    owner_user_id = mock_auth_dependency['owner'].return_value.user_id
    property_id = uuid.uuid4()
    request_id = uuid.uuid4()
    payment_data = {"request_id": str(request_id), "property_id": str(property_id), "user_id": str(owner_user_id), "amount": 100.00}

    # First call - creates the payment
    response1 = await client.post("/api/v1/payments/initiate", json=payment_data)
    assert response1.status_code == 202
    mock_chapa_service.initialize_payment.assert_called_once()
    mock_notification_service.send_notification.assert_called_once()

    # Reset mocks for second call
    mock_chapa_service.initialize_payment.reset_mock()
    mock_notification_service.send_notification.reset_mock()

    # Second call with the same request_id - should return existing payment
    response2 = await client.post("/api/v1/payments/initiate", json=payment_data)
    assert response2.status_code == 202
    assert response2.json()['id'] == response1.json()['id']
    mock_chapa_service.initialize_payment.assert_not_called() # Should not call Chapa again
    mock_notification_service.send_notification.assert_not_called() # Should not send notification again

@pytest.mark.asyncio
async def test_initiate_payment_idempotency_different_property_id_same_request_id(
    client: AsyncClient,
    mock_chapa_service,
    mock_auth_dependency,
    mock_notification_service,
    test_db,
    create_payment
):
    owner_user_id = mock_auth_dependency['owner'].return_value.user_id
    request_id = uuid.uuid4()
    property_id_1 = uuid.uuid4()
    property_id_2 = uuid.uuid4()

    payment_data_1 = {"request_id": str(request_id), "property_id": str(property_id_1), "user_id": str(owner_user_id), "amount": 100.00}
    payment_data_2 = {"request_id": str(request_id), "property_id": str(property_id_2), "user_id": str(owner_user_id), "amount": 100.00}

    # First call - creates the payment for property_id_1
    response1 = await client.post("/api/v1/payments/initiate", json=payment_data_1)
    assert response1.status_code == 202
    assert response1.json()['property_id'] == str(property_id_1)

    # Reset mocks for second call
    mock_chapa_service.initialize_payment.reset_mock()
    mock_notification_service.send_notification.reset_mock()

    # Second call with the same request_id but different property_id - should return existing payment for property_id_1
    response2 = await client.post("/api/v1/payments/initiate", json=payment_data_2)
    assert response2.status_code == 202
    assert response2.json()['id'] == response1.json()['id']
    assert response2.json()['property_id'] == str(property_id_1) # Should still be property_id_1
    mock_chapa_service.initialize_payment.assert_not_called()
    mock_notification_service.send_notification.assert_not_called()

@pytest.mark.asyncio
async def test_initiate_payment_not_owner(
    client: AsyncClient,
    mock_auth_dependency
):
    # Mock get_current_owner to raise 403
    mock_auth_dependency['owner'].side_effect = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Owners can perform this action")

    property_id = uuid.uuid4()
    request_id = uuid.uuid4()
    payment_data = {"request_id": str(request_id), "property_id": str(property_id), "user_id": str(uuid.uuid4()), "amount": 100.00}

    response = await client.post("/api/v1/payments/initiate", json=payment_data)

    assert response.status_code == 403
    assert "Only Owners can perform this action" in response.json()["detail"]

@pytest.mark.asyncio
async def test_initiate_payment_service_api_key_success(client: AsyncClient, db_session: AsyncSession, mock_external_services):
    service_user_id = uuid.uuid4() # This user_id will be in the payload
    property_id = uuid.uuid4()
    request_id = uuid.uuid4()
    amount = 100.00

    # Mock get_user_details_for_notification for service calls
    mock_external_services["get_user_details"].return_value = UserAuthResponse(
        user_id=service_user_id,
        role="Tenant", # Role doesn't matter for service, but needs to be present
        email="service_user@example.com",
        phone_number="+251911111111",
        preferred_language="en"
    )

    payload = {
        "request_id": str(request_id),
        "property_id": str(property_id),
        "user_id": str(service_user_id), # User ID from the service
        "amount": amount,
    }
    response = await client.post(
        "/api/v1/payments/initiate",
        json=payload,
        headers={"X-API-Key": settings.PAYMENT_SERVICE_API_KEY}
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "PENDING"
    assert "chapa.co/checkout" in data["chapa_tx_ref"]

    # Verify that a payment record was created in the DB
    payment_in_db = await db_session.get(Payment, uuid.UUID(data["id"]))
    assert payment_in_db is not None
    assert payment_in_db.status == PaymentStatus.PENDING
    assert decrypt_data(payment_in_db.chapa_tx_ref) == "test-ref"
    assert payment_in_db.user_id == service_user_id # User ID from payload

    mock_external_services["chapa_init"].assert_called_once()
    mock_external_services["get_user_details"].assert_called_once_with(service_user_id)
    mock_external_services["send_notification"].assert_called_once()
    args, kwargs = mock_external_services["send_notification"].call_args
    assert kwargs["template_name"] == "payment_initiated"
    assert kwargs["email"] == "service_user@example.com"

@pytest.mark.asyncio
async def test_initiate_payment_service_api_key_invalid(client: AsyncClient, db_session: AsyncSession, mock_external_services):
    service_user_id = uuid.uuid4()
    property_id = uuid.uuid4()
    request_id = uuid.uuid4()
    amount = 100.00

    payload = {
        "request_id": str(request_id),
        "property_id": str(property_id),
        "user_id": str(service_user_id),
        "amount": amount,
    }
    response = await client.post(
        "/api/v1/payments/initiate",
        json=payload,
        headers={"X-API-Key": "invalid-api-key"}
    )
    assert response.status_code == 401
    assert "Invalid API Key" in response.json()["detail"]
    mock_external_services["chapa_init"].assert_not_called()
    mock_external_services["get_user_details"].assert_not_called()
    mock_external_services["send_notification"].assert_not_called()

@pytest.mark.asyncio
async def test_initiate_payment_no_auth_provided(client: AsyncClient, db_session: AsyncSession, mock_external_services):
    service_user_id = uuid.uuid4()
    property_id = uuid.uuid4()
    request_id = uuid.uuid4()
    amount = 100.00

    payload = {
        "request_id": str(request_id),
        "property_id": str(property_id),
        "user_id": str(service_user_id),
        "amount": amount,
    }
    response = await client.post(
        "/api/v1/payments/initiate",
        json=payload,
        # No Authorization header and no X-API-Key header
    )
    assert response.status_code == 401
    assert "Not authenticated: Provide a valid API Key or Owner JWT" in response.json()["detail"]
    mock_external_services["chapa_init"].assert_not_called()
    mock_external_services["get_user_details"].assert_not_called()
    mock_external_services["send_notification"].assert_not_called()

@pytest.mark.asyncio
async def test_get_payment_status_success(client: AsyncClient, db_session: AsyncSession, mock_user_token, create_payment_in_db, mock_external_services):
    client: AsyncClient,
    mock_auth_dependency,
    create_payment,
    test_db
):
    user_id = mock_auth_dependency['user'].return_value.user_id
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
    current_user_id = mock_auth_dependency['user'].return_value.user_id
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
    test_db,
    generate_chapa_webhook_signature
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
    payload_body = json.dumps(webhook_payload).encode('utf-8')
    signature = generate_chapa_webhook_signature(payload_body)

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload, headers={"X-Chapa-Signature": signature})

    assert response.status_code == 200
    assert response.json() == {"message": "Webhook processed successfully"}

    mock_chapa_service.verify_webhook_signature.assert_called_once_with(payload_body, signature)
    mock_chapa_service.verify_payment.assert_called_once_with(original_tx_ref)
    mock_property_listing_service.assert_called_once_with(payment.property_id)
    mock_notification_service.send_notification.assert_called_once()
    mock_get_user_details_for_notification.assert_called_once_with(payment.user_id)

    # Verify payment status updated in DB
    updated_payment = await test_db.get(Payment, payment.id)
    assert updated_payment.status == PaymentStatus.SUCCESS

@pytest.mark.asyncio
async def test_chapa_webhook_invalid_signature(
    client: AsyncClient,
    mock_chapa_service,
    generate_chapa_webhook_signature
):
    webhook_payload = {
        "event": "charge.success",
        "data": {
            "tx_ref": "some-tx-ref",
            "status": "success",
            "amount": 100,
            "currency": "ETB",
            "meta": {"user_id": str(uuid.uuid4()), "property_id": str(uuid.uuid4())}
        }
    }
    payload_body = json.dumps(webhook_payload).encode('utf-8')
    invalid_signature = generate_chapa_webhook_signature(payload_body, secret="wrong_secret")

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload, headers={"X-Chapa-Signature": invalid_signature})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}
    mock_chapa_service.verify_webhook_signature.assert_called_once_with(payload_body, invalid_signature)

@pytest.mark.asyncio
async def test_chapa_webhook_failed_status(
    client: AsyncClient,
    mock_chapa_service,
    mock_notification_service,
    create_payment,
    test_db,
    generate_chapa_webhook_signature
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
    payload_body = json.dumps(webhook_payload).encode('utf-8')
    signature = generate_chapa_webhook_signature(payload_body)

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload, headers={"X-Chapa-Signature": signature})

    assert response.status_code == 200
    assert response.json() == {"message": "Webhook processed successfully"}

    mock_chapa_service.verify_webhook_signature.assert_called_once_with(payload_body, signature)
    mock_chapa_service.verify_payment.assert_called_once_with(original_tx_ref)
    mock_notification_service.send_notification.assert_called_once()

    # Verify payment status updated in DB
    updated_payment = await test_db.get(Payment, payment.id)
    assert updated_payment.status == PaymentStatus.FAILED

@pytest.mark.asyncio
async def test_chapa_webhook_payment_not_found(
    client: AsyncClient,
    mock_chapa_service,
    generate_chapa_webhook_signature
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
    payload_body = json.dumps(webhook_payload).encode('utf-8')
    signature = generate_chapa_webhook_signature(payload_body)

    response = await client.post("/api/v1/webhook/chapa", json=webhook_payload, headers={"X-Chapa-Signature": signature})

    assert response.status_code == 200 # Should return 200 even if not found to avoid Chapa retries
    assert response.json() == {"message": "Payment not found or not in PENDING state, no action taken"}
    mock_chapa_service.verify_webhook_signature.assert_called_once_with(payload_body, signature)

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
    mock_notification_service.send_notification.assert_called_once() # Only one notification for the timed out payment

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