import uuid
from datetime import datetime
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx

from app.config import settings
from app.dependencies.auth import get_authenticated_entity, get_current_user # Added get_current_user
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentResponse, ChapaInitializeRequest, WebhookEvent, NotificationPayload, UserAuthResponse
from app.services.chapa import chapa_service
from app.core.security import encrypt_data, decrypt_data
from app.dependencies.database import get_db, AsyncSessionLocal # Import get_db from new database dependency
from app.utils.retry import async_retry
from app.core.logging import logger # Import structured logger
from app.services.notification import notification_service # Import new notification service

# For rate limiting
from fastapi_limiter.depends import RateLimiter

# For optional Redis caching
# import redis.asyncio as redis
# from app.config import settings
# redis_client = redis.from_url(settings.REDIS_URL, encoding="utf8", decode_responses=True)

router = APIRouter()

# In-memory metrics counters (for demo purposes, not persistent)
metrics_counters = defaultdict(int)

async def timeout_pending_payments():
    metrics_counters["timeout_jobs_run"] += 1
    logger.info("Running timeout job for pending payments...", service="payment")
    async with AsyncSessionLocal() as db:
        seven_days_ago = datetime.now() - timedelta(days=settings.PAYMENT_TIMEOUT_DAYS)
        result = await db.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING,
                Payment.created_at < seven_days_ago
            )
        )
        pending_payments = result.scalars().all()

        for payment in pending_payments:
            payment.status = PaymentStatus.FAILED
            payment.updated_at = datetime.now()
            db.add(payment)
            logger.info("Payment timed out and marked as FAILED.", payment_id=payment.id, user_id=payment.user_id, service="payment")

            # Update metrics
            metrics_counters["pending_payments"] -= 1
            metrics_counters["failed_payments"] += 1

            # Fetch user details for notification
            user_details = await get_user_details_for_notification(payment.user_id)
            if user_details:
                try:
                    await notification_service.send_notification(
                        user_id=user_details.user_id,
                        email=user_details.email,
                        phone_number=user_details.phone_number,
                        preferred_language=user_details.preferred_language,
                        template_name="payment_timed_out",
                        template_vars={"property_id": str(payment.property_id)}
                    )
                except Exception as e:
                    logger.error("Failed to send timeout notification.", payment_id=payment.id, error=str(e), service="payment")
            else:
                logger.warning("Could not fetch user details for timeout notification.", user_id=payment.user_id, payment_id=payment.id, service="payment")

        await db.commit()
    logger.info("Timeout job for pending payments completed.", service="payment")

@router.get("/health", summary="Health Check")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Performs a health check on the service, including database and Chapa API connectivity.
    """
    health_status = {"status": "healthy"}
    overall_healthy = True

    # Check Database Connection
    try:
        await db.execute(select(1))
        health_status["db"] = "ok"
    except Exception as e:
        logger.error("Health check failed: Database connection error", error=str(e), service="payment")
        health_status["db"] = "error"
        health_status["db_error"] = str(e)
        overall_healthy = False

    # Check Chapa API Availability (e.g., by trying to get banks)
    try:
        await chapa_service.get_banks()
        health_status["chapa_api"] = "ok"
    except Exception as e:
        logger.error("Health check failed: Chapa API error", error=str(e), service="payment")
        health_status["chapa_api"] = "error"
        health_status["chapa_api_error"] = str(e)
        overall_healthy = False

    if not overall_healthy:
        logger.warning("Health check completed with errors", **health_status, service="payment")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=health_status)

    logger.info("Health check completed successfully", **health_status, service="payment")
    return health_status

@router.get("/metrics", summary="Service Metrics")
async def get_metrics():
    """
    Returns in-memory metrics for demo purposes.
    """
    logger.info("Metrics endpoint accessed", service="payment")
    return {
        "total_payments": metrics_counters["total_payments"],
        "pending_payments": metrics_counters["pending_payments"],
        "success_payments": metrics_counters["success_payments"],
        "failed_payments": metrics_counters["failed_payments"],
        "webhook_calls": metrics_counters["webhook_calls"],
        "initiate_calls": metrics_counters["initiate_calls"],
        "status_calls": metrics_counters["status_calls"],
        "timeout_jobs_run": metrics_counters["timeout_jobs_run"],
    }

@router.post("/payments/initiate", response_model=PaymentResponse, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(RateLimiter(times=10, seconds=60))])
async def initiate_payment(
    payment_create: PaymentCreate,
    authenticated_entity: UserAuthResponse = Depends(get_authenticated_entity),
    db: AsyncSession = Depends(get_db)
):
    """
    Initiates a payment for a property listing. Accessible by users with the 'Owner' role
    or by service-to-service calls using an API key.
    This endpoint is idempotent; sending the same `request_id` multiple times will not create duplicate payments.

    - **Idempotency**: Uses `request_id` to prevent duplicate payment initializations.
    - **Chapa Integration**: Generates a unique transaction reference and calls the Chapa API to get a checkout URL.
    - **Database**: Creates a new payment record with a 'PENDING' status.
    - **Notifications**: Sends a notification to the user after successful initialization.
    """
    metrics_counters["initiate_calls"] += 1
    logger.info("Initiating payment request", user_id=authenticated_entity.user_id, property_id=payment_create.property_id, request_id=payment_create.request_id, service="payment")

    # Determine the actual user details for Chapa and notifications
    if authenticated_entity.role == "Service":
        # If authenticated via API key, fetch user details from User Management Service
        user_details = await get_user_details_for_notification(payment_create.user_id)
        if not user_details:
            logger.error("User details not found for service-initiated payment.", user_id=payment_create.user_id, property_id=payment_create.property_id, service="payment")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User details not found for the provided user_id")
        actual_user_id = user_details.user_id
        actual_user_email = user_details.email
        actual_user_phone = user_details.phone_number
        actual_user_lang = user_details.preferred_language
    else:
        # If authenticated via JWT (Owner role), use the authenticated_entity directly
        user_details = authenticated_entity
        actual_user_id = authenticated_entity.user_id
        actual_user_email = authenticated_entity.email
        actual_user_phone = authenticated_entity.phone_number
        actual_user_lang = authenticated_entity.preferred_language

    # Idempotency check: Prevent duplicate payments for the same request.
    existing_payment = await db.execute(
        select(Payment).where(Payment.request_id == payment_create.request_id)
    )
    existing_payment = existing_payment.scalar_one_or_none()

    if existing_payment:
        logger.info("Idempotent request: Payment already exists for request_id", request_id=payment_create.request_id, payment_id=existing_payment.id, service="payment")
        # For idempotency, it's good practice to return the status of the existing resource.
        return PaymentResponse(
            id=existing_payment.id,
            property_id=existing_payment.property_id,
            user_id=existing_payment.user_id,
            amount=existing_payment.amount,
            status=existing_payment.status,
            # The checkout URL is not stored; decrypt the original tx_ref if needed, but masking is safer.
            chapa_tx_ref="********", 
            created_at=existing_payment.created_at,
            updated_at=existing_payment.updated_at
        )

    # Generate a unique transaction reference for this payment attempt.
    chapa_tx_ref = f"tx-{uuid.uuid4()}"

    # Prepare the request for the Chapa API.
    # Note: Chapa's sandbox may require a non-test domain for emails (e.g., not 'example.com').
    chapa_init_request = ChapaInitializeRequest(
        amount=str(payment_create.amount),
        currency="ETB",
        email=actual_user_email, # Use the authenticated user's email.
        first_name="Owner", # Placeholder, ideally get from User Management
        last_name="User",   # Placeholder
        phone_number=actual_user_phone,
        tx_ref=chapa_tx_ref,
        callback_url=f"{settings.BASE_URL}{settings.CHAPA_WEBHOOK_URL}",
        return_url="https://your-rent-management-frontend.com/payment-status", # Placeholder for frontend return URL
        customization={
            "title": "Listing Fee",
            "description": f"Payment for {payment_create.property_id}"
        },
        meta={
            "user_id": str(actual_user_id),
            "property_id": str(payment_create.property_id),
            "request_id": str(payment_create.request_id)
        }
    )

    logger.info("Preparing to call Chapa service")
    try:
        chapa_response = await chapa_service.initialize_payment(chapa_init_request)
        if chapa_response.status != "success":
            logger.error("Chapa payment initialization failed", user_id=actual_user_id, property_id=payment_create.property_id, chapa_message=chapa_response.message, service="payment")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Payment initialization failed: {chapa_response.message}")

        checkout_url = chapa_response.data['checkout_url']

        # Encrypt chapa_tx_ref (which is the actual transaction reference, not the checkout URL) before storing
        encrypted_chapa_tx_ref = encrypt_data(chapa_tx_ref)

        # Store payment in DB
        new_payment = Payment(
            request_id=payment_create.request_id,
            property_id=payment_create.property_id,
            user_id=actual_user_id,
            amount=payment_create.amount,
            status=PaymentStatus.PENDING,
            chapa_tx_ref=encrypted_chapa_tx_ref # Store the actual Chapa transaction reference
        )
        db.add(new_payment)
        await db.commit()
        await db.refresh(new_payment)

        metrics_counters["total_payments"] += 1
        metrics_counters["pending_payments"] += 1
        logger.info("Payment initiated and stored", payment_id=new_payment.id, user_id=actual_user_id, property_id=new_payment.property_id, checkout_url=checkout_url, service="payment")

        # Notify landlord about pending payment using the new notification service
        await notification_service.send_notification(
            user_id=str(actual_user_id),
            email=actual_user_email,
            phone_number=actual_user_phone,
            preferred_language=actual_user_lang,
            template_name="payment_initiated",
            template_vars={
                "property_id": str(payment_create.property_id),
                "payment_link": checkout_url
            }
        )

        return PaymentResponse(
            id=new_payment.id,
            property_id=new_payment.property_id,
            user_id=new_payment.user_id,
            amount=new_payment.amount,
            status=new_payment.status,
            chapa_tx_ref=checkout_url, # Return checkout URL here for the client
            created_at=new_payment.created_at,
            updated_at=new_payment.updated_at
        )

    except HTTPException:
        raise # Re-raise HTTPExceptions
    except Exception as e:
        logger.exception("Error initiating payment", user_id=actual_user_id, property_id=payment_create.property_id, request_id=payment_create.request_id, service="payment")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")

@router.get("/payments/{payment_id}/status", response_model=PaymentResponse)
async def get_payment_status(
    payment_id: uuid.UUID,
    current_user: UserAuthResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve the status of a specific payment.
    """
    metrics_counters["status_calls"] += 1
    logger.info("Fetching status for payment", payment_id=payment_id, user_id=current_user.user_id, service="payment")

    # Optional: Redis caching for payment status
    # cache_key = f"payment_status:{payment_id}"
    # cached_status = await redis_client.get(cache_key)
    # if cached_status:
    #     logger.info("Payment status retrieved from cache", payment_id=payment_id)
    #     return PaymentResponse.model_validate_json(cached_status)

    payment = await db.get(Payment, payment_id)

    if not payment:
        logger.warning("Payment not found", payment_id=payment_id, user_id=current_user.user_id, service="payment")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    # Only the user who made the payment or an admin should view the status
    if payment.user_id != current_user.user_id and current_user.role != "Admin": # Assuming an 'Admin' role exists
        logger.warning("Unauthorized access to payment status", payment_id=payment.id, user_id=current_user.user_id, requested_by_role=current_user.role, service="payment")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this payment status")

    response_data = PaymentResponse(
        id=payment.id,
        property_id=payment.property_id,
        user_id=payment.user_id,
        amount=payment.amount,
        status=payment.status,
        chapa_tx_ref="********", # Masking for security in response
        created_at=payment.created_at,
        updated_at=payment.updated_at
    )

    # Optional: Cache payment status
    # await redis_client.set(cache_key, response_data.model_dump_json(), ex=60) # Cache for 60 seconds
    # logger.info("Payment status cached", payment_id=payment_id)

    return response_data

@router.post("/webhook/chapa", status_code=status.HTTP_200_OK)
async def chapa_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_chapa_signature: str = Header(None) # Chapa webhook signature header
):
    """
    Handles Chapa webhooks for payment status updates.
    Verifies webhook signature for authenticity.
    """
    metrics_counters["webhook_calls"] += 1
    payload_body = await request.body()
    logger.info("Received Chapa webhook", payload_size=len(payload_body), service="payment")

    # 1. Verify webhook signature
    if not x_chapa_signature:
        logger.error("Webhook received without X-Chapa-Signature header.", service="payment")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Chapa-Signature header missing")

    if not chapa_service.verify_webhook_signature(payload_body, x_chapa_signature):
        logger.error("Invalid Chapa webhook signature.", received_signature=x_chapa_signature, service="payment")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception as e:
        logger.error("Could not parse webhook payload as JSON", error=str(e), payload_body=payload_body.decode(), service="payment")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    event_data = payload.get("data", {})
    chapa_tx_ref = event_data.get("tx_ref")
    transaction_status = event_data.get("status")
    meta_data = event_data.get("meta", {})
    webhook_user_id = meta_data.get("user_id")
    webhook_property_id = meta_data.get("property_id")

    if not chapa_tx_ref or not transaction_status:
        logger.error("Invalid webhook payload: missing tx_ref or status.", payload=payload, service="payment")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload")

    logger.info("Processing Chapa webhook", chapa_tx_ref=chapa_tx_ref, transaction_status=transaction_status, user_id=webhook_user_id, property_id=webhook_property_id, service="payment")

    # Decrypt all stored chapa_tx_ref to find a match
    # This is inefficient for large number of payments. A better approach would be to store a hash of tx_ref
    # or use a non-encrypted tx_ref for lookup and encrypt other sensitive data.
    # For this project, given the constraints, we'll iterate and decrypt.
    found_payment = None
    # Only query for PENDING payments to reduce search space
    pending_payments_stmt = select(Payment).where(Payment.status == PaymentStatus.PENDING)
    pending_payments_result = await db.execute(pending_payments_stmt)
    all_pending_payments = pending_payments_result.scalars().all()

    for payment in all_pending_payments:
        try:
            decrypted_ref = decrypt_data(payment.chapa_tx_ref)
            if decrypted_ref == chapa_tx_ref:
                found_payment = payment
                break
        except Exception as e:
            logger.error("Error decrypting chapa_tx_ref for payment", payment_id=payment.id, error=str(e), service="payment")
            continue

    if not found_payment:
        logger.warning("No PENDING payment found for Chapa tx_ref", chapa_tx_ref=chapa_tx_ref, service="payment")
        # It's possible the payment was already processed or timed out, so return 200 to Chapa
        return {"message": "Payment not found or not in PENDING state, no action taken"}

    # Prevent reprocessing if status is already SUCCESS or FAILED
    if found_payment.status != PaymentStatus.PENDING:
        logger.info("Payment already processed, skipping update", payment_id=found_payment.id, current_status=found_payment.status, service="payment")
        return {"message": "Payment already processed, no action taken"}

    # Verify payment with Chapa API to confirm status (double-check)
    try:
        verification_response = await chapa_service.verify_payment(chapa_tx_ref)
        if verification_response.status != "success" or verification_response.data.get("status") != "success":
            logger.warning("Chapa API verification failed", chapa_tx_ref=chapa_tx_ref, api_status=verification_response.status, data_status=verification_response.data.get("status"), service="payment")
            new_status = PaymentStatus.FAILED
        else:
            new_status = PaymentStatus.SUCCESS
    except Exception as e:
        logger.error("Error verifying payment with Chapa API", chapa_tx_ref=chapa_tx_ref, error=str(e), service="payment")
        new_status = PaymentStatus.FAILED # Default to failed if verification fails

    # Update payment status in DB
    found_payment.status = new_status
    found_payment.updated_at = datetime.now()
    db.add(found_payment)
    await db.commit()
    await db.refresh(found_payment)
    logger.info("Payment status updated", payment_id=found_payment.id, old_status=PaymentStatus.PENDING, new_status=new_status, service="payment")

    # Update metrics
    metrics_counters["pending_payments"] -= 1
    if new_status == PaymentStatus.SUCCESS:
        metrics_counters["success_payments"] += 1
    else:
        metrics_counters["failed_payments"] += 1

    # Optional: Invalidate cache for this payment
    # await redis_client.delete(f"payment_status:{found_payment.id}")

    # Trigger Property Listing service for approval if successful
    if new_status == PaymentStatus.SUCCESS:
        try:
            await approve_property_listing(found_payment.property_id)
            logger.info("Property approved via Property Listing Service.", property_id=found_payment.property_id, payment_id=found_payment.id, service="payment")
        except Exception as e:
            logger.error("Failed to approve property via Property Listing Service", property_id=found_payment.property_id, payment_id=found_payment.id, error=str(e), service="payment")

    # Notify landlord/admin using the new notification service
    user_details = await get_user_details_for_notification(found_payment.user_id)

    template_name = "payment_success" if new_status == PaymentStatus.SUCCESS else "payment_failed"
    await notification_service.send_notification(
        user_id=str(found_payment.user_id),
        email=user_details.email if user_details else "admin@example.com", # Fallback
        phone_number=user_details.phone_number if user_details else "+251900000000", # Fallback
        preferred_language=user_details.preferred_language if user_details else "en", # Fallback
        template_name=template_name,
        template_vars={
            "property_id": str(found_payment.property_id)
        }
    )

    return {"message": "Webhook processed successfully"}

@async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
async def approve_property_listing(property_id: uuid.UUID):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{settings.PROPERTY_LISTING_SERVICE_URL}/properties/{property_id}/approve",
                timeout=5
            )
            response.raise_for_status()
            logger.info("Property listing approval request sent", property_id=property_id, service="payment")
            return response.json()
        except httpx.RequestError as exc:
            logger.error("Property Listing service request error", property_id=property_id, error=str(exc), service="payment")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Property Listing service unavailable")
        except httpx.HTTPStatusError as exc:
            logger.error("Property Listing service error", property_id=property_id, status_code=exc.response.status_code, response_text=exc.response.text, service="payment")
            raise HTTPException(status_code=exc.response.status_code, detail="Property Listing service error")

@async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
async def get_user_details_for_notification(user_id: uuid.UUID) -> NotificationPayload | None:
    """
    Fetches user details from User Management service for notification purposes.
    This assumes an endpoint in User Management to get user details by ID.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{settings.USER_MANAGEMENT_URL}/users/{user_id}",
                timeout=5
            )
            response.raise_for_status()
            logger.info("User details fetched for notification", user_id=user_id, service="payment")
            
            user_data = response.json()
            # Decrypt phone_number if it exists and is encrypted
            if "phone_number" in user_data and user_data["phone_number"]:
                original_phone_number = user_data["phone_number"]
                try:
                    decrypted_phone_number = decrypt_data(original_phone_number)
                    user_data["phone_number"] = decrypted_phone_number
                except Exception:
                    logger.warning("Phone number decryption failed in get_user_details_for_notification, using original value.", user_id=user_id, service="payment")
                    user_data["phone_number"] = original_phone_number

            # Assuming User Management returns a structure compatible with NotificationPayload
            return NotificationPayload(**user_data)
        except httpx.RequestError as exc:
            logger.error("User Management service request error for notification", user_id=user_id, error=str(exc), service="payment")
            return None # Return None if service is unavailable
        except httpx.HTTPStatusError as exc:
            logger.warning("User Management service error fetching user for notification", user_id=user_id, status_code=exc.response.status_code, response_text=exc.response.text, service="payment")
            return None # Return None if user not found or other error