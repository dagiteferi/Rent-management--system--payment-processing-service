import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import httpx

from app.config import settings
from app.routers import payments
from app.models.payment import Base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.payment import Payment, PaymentStatus
from datetime import datetime, timedelta
from app.utils.retry import async_retry

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database setup
engine = create_async_engine(settings.DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# Scheduler setup
scheduler = AsyncIOScheduler()

@async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
async def notify_landlord(payload):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{settings.NOTIFICATION_SERVICE_URL}/notify", json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"Notification sent successfully for user {payload.get('user_id')}")
        except httpx.RequestError as exc:
            logger.error(f"Notification service request error: {exc}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Notification service unavailable")
        except httpx.HTTPStatusError as exc:
            logger.error(f"Notification service error: {exc.response.status_code} - {exc.response.text}")
            raise HTTPException(status_code=exc.response.status_code, detail="Notification service error")

async def timeout_pending_payments():
    logger.info("Running timeout job for pending payments...")
    async with AsyncSessionLocal() as db:
        seven_days_ago = datetime.now() - timedelta(days=settings.PAYMENT_TIMEOUT_DAYS)
        pending_payments = await db.execute(
            Payment.__table__.select().where(
                Payment.status == PaymentStatus.PENDING,
                Payment.created_at < seven_days_ago
            )
        )
        pending_payments = pending_payments.scalars().all()

        for payment in pending_payments:
            payment.status = PaymentStatus.FAILED
            payment.updated_at = datetime.now()
            db.add(payment)
            logger.info(f"Payment {payment.id} timed out and marked as FAILED.")

            # Notify landlord
            notification_payload = {
                "user_id": str(payment.user_id),
                "email": "landlord@example.com", # Placeholder, ideally fetch from User Management
                "phone_number": "+251911123456", # Placeholder
                "preferred_language": "en", # Placeholder
                "message": f"Your payment for property {payment.property_id} has timed out and failed. Please try again.",
                "subject": "Payment Failed - Action Required"
            }
            try:
                await notify_landlord(notification_payload)
            except Exception as e:
                logger.error(f"Failed to send timeout notification for payment {payment.id}: {e}")

        await db.commit()
    logger.info("Timeout job for pending payments completed.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Payment Processing Microservice starting up...")
    # No need to create tables here, migrate.sh handles it
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.create_all)

    scheduler.add_job(
        timeout_pending_payments,
        IntervalTrigger(hours=24), # Run once every 24 hours
        id="timeout_pending_payments_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Scheduler started.")
    yield
    # Shutdown
    logger.info("Payment Processing Microservice shutting down...")
    scheduler.shutdown()
    logger.info("Scheduler shut down.")

app = FastAPI(lifespan=lifespan, title="Payment Processing Microservice", version="1.0.0")

app.include_router(payments.router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Payment Processing Microservice is running"}
