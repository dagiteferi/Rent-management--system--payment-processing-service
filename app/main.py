import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import httpx
import redis.asyncio as redis
from fastapi_limiter import FastAPILimiter

from app.config import settings
from app.routers import payments
from app.models.payment import Base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.payment import Payment, PaymentStatus
from datetime import datetime, timedelta
from app.utils.retry import async_retry
from app.core.logging import logger # Import structured logger
from app.services.notification import notification_service # Import new notification service

# Database setup
engine = create_async_engine(settings.DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# Scheduler setup
scheduler = AsyncIOScheduler()

@async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
async def notify_landlord_external(payload):
    """Sends notification to the external Notification Service."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{settings.NOTIFICATION_SERVICE_URL}/notifications/send", json=payload, timeout=5)
            response.raise_for_status()
            logger.info("Notification sent successfully via external service", user_id=payload.get('user_id'), subject=payload.get('subject'))
        except httpx.RequestError as exc:
            logger.error("Notification service request error", user_id=payload.get('user_id'), error=str(exc))
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Notification service unavailable")
        except httpx.HTTPStatusError as exc:
            logger.error("Notification service HTTP error", user_id=payload.get('user_id'), status_code=exc.response.status_code, response_text=exc.response.text)
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
            logger.info("Payment timed out and marked as FAILED.", payment_id=payment.id, user_id=payment.user_id)

            # Notify landlord using the new notification service
            # Placeholder for user details, ideally fetched from User Management
            user_details_for_notification = {
                "user_id": str(payment.user_id),
                "email": "landlord@example.com",
                "phone_number": "+251911123456",
                "preferred_language": "en",
            }
            try:
                await notification_service.send_notification(
                    user_id=user_details_for_notification["user_id"],
                    email=user_details_for_notification["email"],
                    phone_number=user_details_for_notification["phone_number"],
                    preferred_language=user_details_for_notification["preferred_language"],
                    template_name="payment_timed_out",
                    template_vars={
                        "property_id": str(payment.property_id)
                    }
                )
            except Exception as e:
                logger.error("Failed to send timeout notification.", payment_id=payment.id, error=str(e))

        await db.commit()
    logger.info("Timeout job for pending payments completed.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Payment Processing Microservice starting up...")

    # Initialize FastAPI-Limiter
    redis_connection = redis.from_url(settings.REDIS_URL, encoding="utf8", decode_responses=True)
    await FastAPILimiter.init(redis_connection)
    logger.info("FastAPILimiter initialized with Redis.")

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
    await FastAPILimiter.close()
    logger.info("FastAPILimiter closed.")

app = FastAPI(lifespan=lifespan, title="Payment Processing Microservice", version="1.0.0")

app.include_router(payments.router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Payment Processing Microservice is running"}