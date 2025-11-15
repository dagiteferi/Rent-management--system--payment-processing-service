import uuid
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from app.config import settings
from app.dependencies.auth import get_authenticated_entity, get_current_user # 
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentResponse, ChapaInitializeRequest, WebhookEvent, NotificationPayload, UserAuthResponse
from app.services.chapa import chapa_service
from app.core.security import encrypt_data, decrypt_data
from app.dependencies.database import get_db, AsyncSessionLocal # 
from app.utils.retry import async_retry
from app.core.logging import logger # Import structured logger
from app.services.notification import notification_service # Import new notification service
import base64 # Import base64

# For rate limiting
from fastapi_limiter.depends import RateLimiter


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
            logger.info(
                    "Payment timed out and marked as FAILED.", 
                        payment_id=payment.id, 
                        user_id=payment.user_id, service="payment"
                        )

            # Update metrics
            metrics_counters["pending_payments"] -= 1
            metrics_counters["failed_payments"] += 1

            # Optionally notify Property Listing Service to reject the property
            try:
                await confirm_payment_with_listing_service(
                    property_id=payment.property_id,
                    payment_id=payment.id,
                    status=PaymentStatus.FAILED
                )
                logger.info("Notified Property Listing Service about timed out payment.", payment_id=payment.id, property_id=payment.property_id, service="payment")
            except Exception as e:
                logger.error("Failed to notify Property Listing Service about timed out payment.", payment_id=payment.id, error=str(e), service="payment")

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

@router.post("/payments/initiate", 
             response_model=PaymentResponse, 
             status_code=status.HTTP_202_ACCEPTED, 
             dependencies=[Depends(RateLimiter(times=10, seconds=60))])
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
    logger.info("Initiating payment request", user_id=authenticated_entity.user_id, property_id=payment_create.property_id, request_id=payment_create.request_id, amount=500, service="payment")

    # Determine the actual user details for Chapa and notifications
    user_details_for_chapa = None
    if authenticated_entity.role == "Service":
        # If authenticated via API key, fetch user details from User Management Service
        user_details_for_chapa = await get_user_details_for_notification(payment_create.user_id)
        if not user_details_for_chapa:
            logger.error("User details not found for service-initiated payment.", user_id=payment_create.user_id, property_id=payment_create.property_id, service="payment")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User details not found for the provided user_id")
        actual_user_id = user_details_for_chapa.user_id
        actual_user_email = user_details_for_chapa.email
        actual_user_phone = user_details_for_chapa.phone_number # Re-introduced
        actual_user_lang = user_details_for_chapa.preferred_language
    else:
        # If authenticated via JWT (Owner role), use the authenticated_entity directly
        user_details_for_chapa = authenticated_entity
        actual_user_id = authenticated_entity.user_id
        actual_user_email = authenticated_entity.email
        actual_user_phone = authenticated_entity.phone_number # Re-introduced
        actual_user_lang = authenticated_entity.preferred_language

    # Phone number is now expected to be unencrypted and normalized from the User Management Service.
    # We can add basic validation here if needed, but assuming User Management provides valid format.
    if not actual_user_phone:
        logger.warning("Missing phone number for user, cannot initiate payment.", user_id=actual_user_id, service="payment")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User phone number not provided")

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
    logger.info("Generated Chapa transaction reference", chapa_tx_ref=chapa_tx_ref, service="payment")

    # Prepare the request for the Chapa API.
    # Note: Chapa's sandbox may require a non-test domain for emails (e.g., not 'example.com').
    chapa_init_request = ChapaInitializeRequest(
        amount=str(500), # Fixed amount for Chapa API
        currency="ETB",
        email=actual_user_email, # Use the authenticated user's email.
        first_name="Owner", # Placeholder, ideally get from User Management
        last_name="User",   # Placeholder
        phone_number=actual_user_phone, # Changed from mobile to phone_number
        tx_ref=chapa_tx_ref,
        callback_url=f"{settings.BASE_URL}/api/v1/webhook/chapa", # Server-to-server POST
        return_url=settings.FRONTEND_REDIRECT_URL, # Redirect to the frontend app
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

    logger.info("Preparing to call Chapa service", user_id=actual_user_id, property_id=payment_create.property_id, phone_mask=f"{actual_user_phone[:3]}***", service="payment")
    try:
        chapa_response = await chapa_service.initialize_payment(chapa_init_request)
        if chapa_response.status != "success":
            logger.error("Chapa payment initialization failed", user_id=actual_user_id, property_id=payment_create.property_id, chapa_message=chapa_response.message, service="payment")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Payment initialization failed: {chapa_response.message}")

        checkout_url = chapa_response.data.get('checkout_url') # Use .get() for safety
        logger.info("Extracted checkout_url from Chapa response", checkout_url=checkout_url, service="payment")

        # Store payment in DB
        new_payment = Payment(
            request_id=payment_create.request_id,
            property_id=payment_create.property_id,
            user_id=actual_user_id,
            amount=payment_create.amount,
            status=PaymentStatus.PENDING,
            chapa_tx_ref=chapa_tx_ref # Store the actual Chapa transaction reference
        )
        db.add(new_payment)
        await db.commit()
        await db.refresh(new_payment)

        metrics_counters["total_payments"] += 1
        metrics_counters["pending_payments"] += 1
        logger.info("Payment initiated and stored", payment_id=new_payment.id, user_id=actual_user_id, property_id=new_payment.property_id, checkout_url_prefix=checkout_url[:30], service="payment")

        return PaymentResponse(
            id=new_payment.id,
            property_id=new_payment.property_id,
            user_id=new_payment.user_id,
            amount=new_payment.amount,
            status=new_payment.status,
            chapa_tx_ref=chapa_tx_ref, # Return the actual chapa_tx_ref
            checkout_url=checkout_url, # Return checkout URL here for the client
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

    payment = await db.get(Payment, payment_id)

    if not payment:
        logger.warning("Payment not found", payment_id=payment_id, user_id=current_user.user_id, service="payment")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

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

    return response_data

@router.api_route("/webhook/chapa", methods=["GET", "POST"], status_code=status.HTTP_200_OK)
async def chapa_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_chapa_signature: str = Header(None, include_in_schema=False) # Chapa webhook signature header
):
    """
    Handles Chapa webhooks for payment status updates.
    Verifies webhook signature for authenticity.
    """
    metrics_counters["webhook_calls"] += 1
    logger.info("Received Chapa webhook notification", method=request.method, headers=request.headers, service="payment")

    chapa_tx_ref = None
    transaction_status = None
    webhook_user_id = None
    webhook_property_id = None

    if request.method == "POST":
        payload_body = await request.body()
        logger.info("Received Chapa webhook (POST)", payload_size=len(payload_body), raw_body=payload_body.decode('utf-8'), service="payment")

        # 1. Verify webhook signature for POST requests
        if not x_chapa_signature:
            logger.error("Webhook received without X-Chapa-Signature header.", service="payment")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Chapa-Signature header missing")

        if not chapa_service.verify_webhook_signature(payload_body, x_chapa_signature):
            logger.error("Invalid Chapa webhook signature.", received_signature=x_chapa_signature, service="payment")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

        try:
            payload = await request.json()
            logger.info("Raw Chapa POST webhook payload", payload=payload, service="payment") # Log raw payload
            # Extract tx_ref and status directly from the root of the payload
            chapa_tx_ref = payload.get("tx_ref")
            transaction_status = payload.get("status")
            # Extract meta_data if available, it might be at the root or within a 'data' key depending on webhook type
            meta_data = payload.get("meta", {}) or payload.get("data", {}).get("meta", {})
            webhook_user_id = meta_data.get("user_id")
            webhook_property_id = meta_data.get("property_id")
            logger.info("Parsed POST webhook payload", chapa_tx_ref=chapa_tx_ref, transaction_status=transaction_status, meta_data=meta_data, service="payment")
        except Exception as e:
            logger.error("Could not parse webhook payload as JSON (POST)", error=str(e), payload_body=payload_body.decode(), service="payment")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    elif request.method == "GET":
        logger.info("Received Chapa webhook (GET redirect)", query_params=request.query_params, service="payment")
        logger.info("Raw Chapa GET webhook query parameters", query_params=request.query_params, service="payment") # Log raw query params
        chapa_tx_ref = request.query_params.get("trx_ref")
        transaction_status = request.query_params.get("status")
        logger.info("Parsed GET redirect query parameters", chapa_tx_ref=chapa_tx_ref, transaction_status=transaction_status, service="payment")
        # For GET redirects, meta data might not be directly available or needs to be extracted differently
        # We will rely on the stored payment record for user_id and property_id later.

    if not chapa_tx_ref or not transaction_status:
        logger.error("Invalid webhook/redirect payload: missing tx_ref or status.", chapa_tx_ref=chapa_tx_ref, transaction_status=transaction_status, service="payment")
        # For GET redirects, we should redirect to frontend with an error
        # return RedirectResponse(url=f"{settings.FRONTEND_REDIRECT_URL}?status=failed&reason=missing_params") # Commented out for debugging
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook/redirect payload")

    logger.info("Processing Chapa webhook/redirect", chapa_tx_ref=chapa_tx_ref, transaction_status=transaction_status, user_id=webhook_user_id, property_id=webhook_property_id, service="payment")

    found_payment = None
    logger.info("Searching for payment in DB", search_tx_ref=chapa_tx_ref, service="payment")
    # Search for the payment using the decrypted tx_ref
    # We search all payments, not just PENDING, in case the webhook is delayed
    # or the payment was already processed by another mechanism.
    all_payments_stmt = select(Payment).where(Payment.chapa_tx_ref == chapa_tx_ref)
    all_payments_result = await db.execute(all_payments_stmt)
    found_payment = all_payments_result.scalar_one_or_none()

    if found_payment:
        logger.info("Payment found in DB", payment_id=found_payment.id, current_db_status=found_payment.status, service="payment")

    if not found_payment:
        logger.warning("No payment found for Chapa tx_ref", chapa_tx_ref=chapa_tx_ref, service="payment")
        # if request.method == "GET":
        #     return RedirectResponse(url=f"{settings.FRONTEND_REDIRECT_URL}?status=failed&reason=not_found") # Commented out for debugging
        return {"message": "Payment not found, no action taken"}

    # If payment is already processed, just redirect the user or return success
    if found_payment.status != PaymentStatus.PENDING:
        logger.info("Payment already processed, skipping update", payment_id=found_payment.id, current_status=found_payment.status, service="payment")
        # if request.method == "GET":
        #     redirect_status = "success" if found_payment.status == PaymentStatus.SUCCESS else "failed"
        #     return RedirectResponse(url=f"{settings.FRONTEND_REDIRECT_URL}?status={redirect_status}&payment_id={found_payment.id}") # Commented out for debugging
        return {"message": "Payment already processed, no action taken"}

    logger.info("Verifying payment with Chapa API", chapa_tx_ref=chapa_tx_ref, service="payment")
    # Verify payment with Chapa API to confirm status (double-check)
    try:
        verification_response = await chapa_service.verify_payment(chapa_tx_ref)
        logger.info("Chapa API verification response", chapa_tx_ref=chapa_tx_ref, api_status=verification_response.status, data_status=verification_response.data.get("status"), service="payment")
        if verification_response.status == "success" and verification_response.data.get("status") == "success":
            new_status = PaymentStatus.SUCCESS
            logger.info("Payment verification successful. Status set to SUCCESS.", payment_id=found_payment.id, chapa_tx_ref=chapa_tx_ref, service="payment")
        else:
            new_status = PaymentStatus.FAILED
            found_payment.failure_reason = f"Chapa verification failed: {verification_response.message} - {verification_response.data.get('status')}"
            logger.warning("Payment verification failed. Status set to FAILED.", payment_id=found_payment.id, chapa_tx_ref=chapa_tx_ref, failure_reason=found_payment.failure_reason, service="payment")
    except Exception as e:
        logger.error("Error verifying payment with Chapa API", chapa_tx_ref=chapa_tx_ref, error=str(e), service="payment")
        new_status = PaymentStatus.FAILED # Default to failed if verification fails
        found_payment.failure_reason = f"Chapa verification API error: {e}"
        logger.error("Payment verification API error. Status set to FAILED.", payment_id=found_payment.id, chapa_tx_ref=chapa_tx_ref, failure_reason=found_payment.failure_reason, service="payment")

    logger.info("Updating payment status in DB", payment_id=found_payment.id, old_status=found_payment.status, new_status=new_status, service="payment")
    # Update payment status in DB
    found_payment.status = new_status
    found_payment.updated_at = datetime.now()
    if new_status == PaymentStatus.SUCCESS:
        found_payment.approved_at = datetime.now()
    db.add(found_payment)
    await db.commit()
    await db.refresh(found_payment)
    logger.info("Payment status updated successfully in DB", payment_id=found_payment.id, final_status=new_status, service="payment")

    # Update metrics
    metrics_counters["pending_payments"] -= 1
    if new_status == PaymentStatus.SUCCESS:
        metrics_counters["success_payments"] += 1
    else:
        metrics_counters["failed_payments"] += 1

    # Trigger Property Listing service for approval if successful
    if new_status == PaymentStatus.SUCCESS:
        logger.info("Attempting to confirm payment with Property Listing Service", payment_id=found_payment.id, property_id=found_payment.property_id, service="payment")
        try:
            listing_service_response = await confirm_payment_with_listing_service(
                property_id=found_payment.property_id,
                payment_id=found_payment.id,
                status=new_status
            )
            logger.info("Payment confirmation sent to Property Listing Service", payment_id=found_payment.id, property_id=found_payment.property_id, listing_service_response=listing_service_response, service="payment")
        except Exception as e:
            logger.error("Failed to send payment confirmation to Property Listing Service.", payment_id=found_payment.id, error=str(e), service="payment")
            # If this is a GET redirect, we still want to redirect the user to a success page
            # if request.method == "GET":
            #     return RedirectResponse(url=f"{settings.FRONTEND_REDIRECT_URL}?status=success&payment_id={found_payment.id}&issue=confirmation_failed") # Commented out for debugging

    # For GET requests, redirect the user to the frontend
    # if request.method == "GET":
    #     redirect_status = "success" if new_status == PaymentStatus.SUCCESS else "failed"
    #     redirect_url = f"{settings.FRONTEND_REDIRECT_URL}?status={redirect_status}&payment_id={found_payment.id}"
    #     logger.info("Redirecting user to frontend", redirect_url=redirect_url, service="payment")
    #     return RedirectResponse(url=redirect_url) # Commented out for debugging

    logger.info("Chapa webhook processing completed.", payment_id=found_payment.id if found_payment else "N/A", final_status=new_status if 'new_status' in locals() else "N/A", service="payment")
    return {"message": "Webhook processed successfully"}

@async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
async def confirm_payment_with_listing_service(property_id: uuid.UUID, payment_id: uuid.UUID, status: PaymentStatus):
    """Sends a confirmation to the property listing service about a payment's status."""
    async with httpx.AsyncClient() as client:
        # Assume PROPERTY_LISTING_SERVICE_URL already includes the /api/v1 base path
        endpoint_url = f"{settings.PROPERTY_LISTING_SERVICE_URL}/payments/confirm"
        
        payload = {
            "property_id": str(property_id),
            "payment_id": str(payment_id),
            "status": status.value
        }
        
        headers = {"X-API-Key": settings.PAYMENT_SERVICE_API_KEY}

        try:
            response = await client.post(endpoint_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(
                "Payment confirmation successfully sent to Property Listing Service.",
                payment_id=payment_id,
                property_id=property_id,
                status=status.value,
                listing_service_response=response.json(),
                service="payment"
            )
            return response.json()
        except httpx.RequestError as exc:
            logger.error("Property Listing service request error during payment confirmation.", property_id=property_id, error=str(exc), service="payment")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Property Listing service unavailable")
        except httpx.HTTPStatusError as exc:
            logger.error("Property Listing service error during payment confirmation.", property_id=property_id, status_code=exc.response.status_code, response_text=exc.response.text, service="payment")
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
            if "phone_number" in user_data and user_data["phone_number"]:
                user_data["phone_number"] = user_data["phone_number"]
            else:
                logger.warning("Phone number not found for user in User Management service.", user_id=user_id, service="payment")
                user_data["phone_number"] = ""

            return NotificationPayload(**user_data)
        except httpx.RequestError as exc:
            logger.error("User Management service request error for notification", user_id=user_id, error=str(exc), service="payment")
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("User Management service error fetching user for notification", user_id=user_id, status_code=exc.response.status_code, response_text=exc.response.text, service="payment")
            return None
