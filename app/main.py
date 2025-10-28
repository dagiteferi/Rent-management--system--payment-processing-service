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
from app.dependencies.database import get_db # Import get_db from new database dependency

# Scheduler setup
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Payment Processing Microservice starting up...", service="payment")

    # Initialize FastAPI-Limiter
    redis_connection = redis.from_url(settings.REDIS_URL, encoding="utf8", decode_responses=True)
    await FastAPILimiter.init(redis_connection)
    logger.info("FastAPILimiter initialized with Redis.", service="payment")

    scheduler.add_job(
        payments.timeout_pending_payments,
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