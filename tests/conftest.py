import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.main import app, get_db
from app.models.payment import Base, Payment, PaymentStatus
from app.config import settings
from unittest.mock import AsyncMock, patch
import uuid
from datetime import datetime, timedelta
import hmac
import hashlib

# Use an in-memory SQLite database for testing
# For a real PostgreSQL test, you'd use testcontainers or a dedicated test DB
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

@pytest_asyncio.fixture(name="test_engine")
async def test_engine_fixture():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest_asyncio.fixture(name="test_db")
async def test_db_fixture(test_engine):
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine, class_=AsyncSession)
    async with TestSessionLocal() as session:
        yield session

@pytest_asyncio.fixture(name="client")
async def client_fixture(test_db):
    # Override the get_db dependency to use the test database session
    app.dependency_overrides[get_db] = lambda: test_db
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest_asyncio.fixture
def mock_chapa_service():
    with patch('app.services.chapa.chapa_service', new_callable=AsyncMock) as mock_chapa:
        mock_chapa.initialize_payment.return_value = AsyncMock(
            status="success",
            message="Payment link generated successfully",
            data={
                "checkout_url": "https://chapa.co/checkout/test-link",
                "transaction_ref": "test-tx-ref"
            }
        )
        mock_chapa.verify_payment.return_value = AsyncMock(
            status="success",
            message="Payment verified successfully",
            data={
                "status": "success",
                "amount": 100,
                "currency": "ETB",
                "tx_ref": "test-tx-ref"
            }
        )
        # Mock webhook signature verification
        mock_chapa.verify_webhook_signature.return_value = True
        yield mock_chapa

@pytest_asyncio.fixture
def mock_auth_dependency():
    with patch('app.dependencies.auth.get_current_owner', new_callable=AsyncMock) as mock_owner_auth,
         patch('app.dependencies.auth.get_current_user', new_callable=AsyncMock) as mock_user_auth:
        mock_owner_auth.return_value = AsyncMock(
            user_id=uuid.uuid4(),
            role="Owner",
            email="owner@example.com",
            phone_number="+251911123456",
            preferred_language="en"
        )
        mock_user_auth.return_value = AsyncMock(
            user_id=uuid.uuid4(),
            role="Tenant", # Default to Tenant for general user access
            email="tenant@example.com",
            phone_number="+251911123457",
            preferred_language="en"
        )
        yield {"owner": mock_owner_auth, "user": mock_user_auth}

@pytest_asyncio.fixture
def mock_notification_service():
    with patch('app.services.notification.notification_service', new_callable=AsyncMock) as mock_notify_service,
         patch('app.main.notification_service', new_callable=AsyncMock) as mock_main_notify_service:
        mock_notify_service.send_notification.return_value = None
        mock_main_notify_service.send_notification.return_value = None
        yield mock_notify_service

@pytest_asyncio.fixture
def mock_property_listing_service():
    with patch('app.routers.payments.approve_property_listing', new_callable=AsyncMock) as mock_approve:
        mock_approve.return_value = {"message": "Property approved"}
        yield mock_approve

@pytest_asyncio.fixture
def mock_get_user_details_for_notification():
    with patch('app.routers.payments.get_user_details_for_notification', new_callable=AsyncMock) as mock_get_user_details:
        mock_get_user_details.return_value = AsyncMock(
            user_id=uuid.uuid4(),
            email="testuser@example.com",
            phone_number="+251911123456",
            preferred_language="en",
            message="", # Not used in this schema for return
            subject="" # Not used in this schema for return
        )
        yield mock_get_user_details

@pytest_asyncio.fixture
async def create_payment(test_db):
    async def _create_payment(
        request_id: uuid.UUID = uuid.uuid4(),
        property_id: uuid.UUID = uuid.uuid4(),
        user_id: uuid.UUID = uuid.uuid4(),
        amount: float = 100.00,
        status: PaymentStatus = PaymentStatus.PENDING,
        chapa_tx_ref: str = "encrypted_test_ref",
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now()
    ):
        payment = Payment(
            request_id=request_id,
            property_id=property_id,
            user_id=user_id,
            amount=amount,
            status=status,
            chapa_tx_ref=chapa_tx_ref,
            created_at=created_at,
            updated_at=updated_at
        )
        test_db.add(payment)
        await test_db.commit()
        await test_db.refresh(payment)
        return payment
    return _create_payment

@pytest_asyncio.fixture(autouse=True)
def mock_settings_encryption_key():
    with patch('app.config.settings.ENCRYPTION_KEY', "a_32_byte_secret_key_for_aes_encryption"):
        yield

@pytest_asyncio.fixture(autouse=True)
def mock_settings_chapa_webhook_secret():
    with patch('app.config.settings.CHAPA_WEBHOOK_SECRET', "test_webhook_secret"):
        yield

@pytest_asyncio.fixture
def generate_chapa_webhook_signature():
    def _generate_signature(payload_body: bytes, secret: str = "test_webhook_secret") -> str:
        return hmac.new(secret.encode('utf-8'), payload_body, hashlib.sha256).hexdigest()
    return _generate_signature