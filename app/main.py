import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import httpx
import redis.asyncio as redis
from fastapi_limiter import FastAPILimiter

from app.config import settings
from app.routers import payments, auth
from app.models.payment import Payment, PaymentStatus
from datetime import datetime, timedelta
from app.utils.retry import async_retry
from app.core.logging import logger 
from app.services.notification import notification_service 
from app.dependencies.database import get_db, AsyncSessionLocal # Import get_db and AsyncSessionLocal from new database dependency

# Scheduler setup
scheduler = AsyncIOScheduler()

async def timeout_pending_payments():
    payments.metrics_counters["timeout_jobs_run"] += 1
    logger.info("Running timeout job for pending payments...", service="payment")
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
            logger.info("Payment timed out and marked as FAILED.", payment_id=payment.id, user_id=payment.user_id, service="payment")

            # Update metrics
            payments.metrics_counters["pending_payments"] -= 1
            payments.metrics_counters["failed_payments"] += 1

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
                logger.error("Failed to send timeout notification.", payment_id=payment.id, error=str(e), service="payment")

        await db.commit()
    logger.info("Timeout job for pending payments completed.", service="payment")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Payment Processing Microservice starting up...", service="payment")

    # Initialize FastAPI-Limiter
    redis_connection = redis.from_url(settings.REDIS_URL, encoding="utf8", decode_responses=True)
    await FastAPILimiter.init(redis_connection)
    logger.info("FastAPILimiter initialized with Redis.", service="payment")

    scheduler.add_job(
        timeout_pending_payments,
        IntervalTrigger(hours=24), # Run once every 24 hours
        id="timeout_pending_payments_job",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Scheduler started.", service="payment")
    yield
    # Shutdown
    logger.info("Payment Processing Microservice shutting down...", service="payment")
    scheduler.shutdown()
    logger.info("Scheduler shut down.", service="payment")
    await FastAPILimiter.close()
    logger.info("FastAPILimiter closed.", service="payment")

app = FastAPI(lifespan=lifespan, title="Payment Processing Microservice", version="1.0.0")

app.include_router(auth.router, tags=["Authentication"])
app.include_router(payments.router, prefix="/api/v1", tags=["Payments"])