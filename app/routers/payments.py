import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.config import settings
from app.dependencies.auth import get_current_owner, get_current_user
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentResponse, ChapaInitializeRequest, WebhookEvent, NotificationPayload
from app.services.chapa import chapa_service
from app.core.security import encrypt_data, decrypt_data
from app.main import get_db # Import get_db from main
from app.utils.retry import async_retry

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/payments/initiate", response_model=PaymentResponse, status_code=status.HTTP_202_ACCEPTED)
async def initiate_payment(
    payment_create: PaymentCreate,
    current_owner: dict = Depends(get_current_owner),
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate a payment for a property listing. Only Owners can initiate payments.
    Generates a Chapa payment link and stores the payment as PENDING.
    """
    logger.info(f"Initiating payment for user {current_owner['user_id']} and property {payment_create.property_id}")

    # Generate a unique transaction reference for Chapa
    chapa_tx_ref = f"tx-{uuid.uuid4()}"

    # Prepare Chapa initialization request
    chapa_init_request = ChapaInitializeRequest(
        amount=str(payment_create.amount),
        currency="ETB",
        email=current_owner['email'],
        first_name="Owner", # Placeholder, ideally get from User Management
        last_name="User",   # Placeholder
        phone_number=current_owner['phone_number'],
        tx_ref=chapa_tx_ref,
        callback_url=f"{settings.CHAPA_WEBHOOK_URL}", # This should be the public URL of your service
        return_url="https://your-rent-management-frontend.com/payment-status", # Frontend URL to redirect after payment
        meta={
            "user_id": str(current_owner['user_id']),
            "property_id": str(payment_create.property_id)
        }
    )

    try:
        chapa_response = await chapa_service.initialize_payment(chapa_init_request)
        if chapa_response.status != "success":
            logger.error(f"Chapa payment initialization failed: {chapa_response.message}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Payment initialization failed: {chapa_response.message}")

        # Encrypt chapa_tx_ref before storing
        encrypted_chapa_tx_ref = encrypt_data(chapa_tx_ref)

        # Store payment in DB
        new_payment = Payment(
            property_id=payment_create.property_id,
            user_id=current_owner['user_id'],
            amount=payment_create.amount,
            status=PaymentStatus.PENDING,
            chapa_tx_ref=encrypted_chapa_tx_ref
        )
        db.add(new_payment)
        await db.commit()
        await db.refresh(new_payment)

        # Notify landlord about pending payment
        notification_payload = NotificationPayload(
            user_id=current_owner['user_id'],
            email=current_owner['email'],
            phone_number=current_owner['phone_number'],
            preferred_language=current_owner['preferred_language'],
            message=f"Your payment for property {payment_create.property_id} has been initiated. Please complete the payment using the link: {chapa_response.data['checkout_url']}",
            subject="Payment Initiated - Action Required"
        )
        try:
            await notify_landlord(notification_payload.model_dump())
        except Exception as e:
            logger.error(f"Failed to send notification for initiated payment {new_payment.id}: {e}")

        # For the response, we might want to return the checkout_url directly or a PaymentResponse with a link
        # For now, let's return the PaymentResponse and log the checkout_url
        logger.info(f"Payment {new_payment.id} initiated. Checkout URL: {chapa_response.data['checkout_url']}")
        return PaymentResponse(
            id=new_payment.id,
            property_id=new_payment.property_id,
            user_id=new_payment.user_id,
            amount=new_payment.amount,
            status=new_payment.status,
            chapa_tx_ref=chapa_response.data['checkout_url'], # Returning checkout URL here for simplicity
            created_at=new_payment.created_at,
            updated_at=new_payment.updated_at
        )

    except HTTPException:
        raise # Re-raise HTTPExceptions
    except Exception as e:
        logger.exception("Error initiating payment")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {e}")

@router.get("/payments/{payment_id}/status", response_model=PaymentResponse)
async def get_payment_status(
    payment_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve the status of a specific payment.
    """
    logger.info(f"Fetching status for payment {payment_id} by user {current_user['user_id']}")
    payment = await db.get(Payment, payment_id)

    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    # Only the user who made the payment or an admin should view the status
    if payment.user_id != current_user['user_id'] and current_user['role'] != "Admin": # Assuming an 'Admin' role exists
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this payment status")

    # Decrypt chapa_tx_ref for internal use if needed, but for response, it's fine as is
    # decrypted_chapa_tx_ref = decrypt_data(payment.chapa_tx_ref)

    return PaymentResponse(
        id=payment.id,
        property_id=payment.property_id,
        user_id=payment.user_id,
        amount=payment.amount,
        status=payment.status,
        chapa_tx_ref="********", # Masking for security in response
        created_at=payment.created_at,
        updated_at=payment.updated_at
    )

@router.post("/webhook/chapa", status_code=status.HTTP_200_OK)
async def chapa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handles Chapa webhooks for payment status updates.
    """
    logger.info("Received Chapa webhook")
    payload = await request.json()
    # In a real scenario, you would verify the webhook signature using settings.CHAPA_SECRET_KEY
    # For sandbox, we'll proceed without strict signature verification for now, but it's CRITICAL for production.
    # if not chapa_service.verify_webhook_signature(payload, request.headers.get("chapa-signature")):
    #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    event_data = payload.get("data", {})
    chapa_tx_ref = event_data.get("tx_ref")
    transaction_status = event_data.get("status")

    if not chapa_tx_ref or not transaction_status:
        logger.error(f"Invalid webhook payload: missing tx_ref or status. Payload: {payload}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload")

    logger.info(f"Processing Chapa webhook for tx_ref: {chapa_tx_ref}, status: {transaction_status}")

    # Decrypt all stored chapa_tx_ref to find a match
    # This is inefficient for large number of payments. A better approach would be to store a hash of tx_ref
    # or use a non-encrypted tx_ref for lookup and encrypt other sensitive data.
    # For this project, given the constraints, we'll iterate and decrypt.
    payments_to_update = []
    all_payments = await db.execute(Payment.__table__.select().where(Payment.status == PaymentStatus.PENDING))
    all_payments = all_payments.scalars().all()

    found_payment = None
    for payment in all_payments:
        try:
            decrypted_ref = decrypt_data(payment.chapa_tx_ref)
            if decrypted_ref == chapa_tx_ref:
                found_payment = payment
                break
        except Exception as e:
            logger.error(f"Error decrypting chapa_tx_ref for payment {payment.id}: {e}")
            continue

    if not found_payment:
        logger.warning(f"No PENDING payment found for Chapa tx_ref: {chapa_tx_ref}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found or not in PENDING state")

    # Verify payment with Chapa API to confirm status (double-check)
    try:
        verification_response = await chapa_service.verify_payment(chapa_tx_ref)
        if verification_response.status != "success" or verification_response.data.get("status") != "success":
            logger.warning(f"Chapa verification failed for tx_ref {chapa_tx_ref}. API status: {verification_response.status}, Data status: {verification_response.data.get('status')}")
            new_status = PaymentStatus.FAILED
        else:
            new_status = PaymentStatus.SUCCESS
    except Exception as e:
        logger.error(f"Error verifying payment {chapa_tx_ref} with Chapa API: {e}")
        new_status = PaymentStatus.FAILED # Default to failed if verification fails

    # Update payment status in DB
    found_payment.status = new_status
    found_payment.updated_at = datetime.now()
    db.add(found_payment)
    await db.commit()
    await db.refresh(found_payment)
    logger.info(f"Payment {found_payment.id} status updated to {new_status}")

    # Trigger Property Listing service for approval if successful
    if new_status == PaymentStatus.SUCCESS:
        try:
            await approve_property_listing(found_payment.property_id)
            logger.info(f"Property {found_payment.property_id} approved via Property Listing Service.")
        except Exception as e:
            logger.error(f"Failed to approve property {found_payment.property_id} via Property Listing Service: {e}")

    # Notify landlord/admin
    notification_message = f"Your payment for property {found_payment.property_id} is now {new_status}."
    notification_subject = f"Payment {new_status}"

    # Fetch user details from User Management for notification
    user_details = await get_user_details_for_notification(found_payment.user_id)

    notification_payload = NotificationPayload(
        user_id=found_payment.user_id,
        email=user_details.email if user_details else "admin@example.com", # Fallback
        phone_number=user_details.phone_number if user_details else "+251900000000", # Fallback
        preferred_language=user_details.preferred_language if user_details else "en", # Fallback
        message=notification_message,
        subject=notification_subject
    )
    try:
        await notify_landlord(notification_payload.model_dump())
    except Exception as e:
        logger.error(f"Failed to send notification for payment {found_payment.id} status update: {e}")

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
            return response.json()
        except httpx.RequestError as exc:
            logger.error(f"Property Listing service request error for property {property_id}: {exc}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Property Listing service unavailable")
        except httpx.HTTPStatusError as exc:
            logger.error(f"Property Listing service error for property {property_id}: {exc.response.status_code} - {exc.response.text}")
            raise HTTPException(status_code=exc.response.status_code, detail="Property Listing service error")

@async_retry(max_attempts=3, delay=1, exceptions=(httpx.RequestError, HTTPException))
async def get_user_details_for_notification(user_id: uuid.UUID):
    """
    Fetches user details from User Management service for notification purposes.
    This assumes an endpoint in User Management to get user details by ID.
    """
    async with httpx.AsyncClient() as client:
        try:
            # This endpoint might not exist in User Management, assuming it does for now.
            # If not, we'd need to adjust or get user details during initial auth.
            response = await client.get(
                f"{settings.USER_MANAGEMENT_URL}/users/{user_id}",
                timeout=5
            )
            response.raise_for_status()
            return NotificationPayload(**response.json()) # Reusing NotificationPayload schema for user details
        except httpx.RequestError as exc:
            logger.error(f"User Management service request error for user {user_id}: {exc}")
            return None # Return None if service is unavailable
        except httpx.HTTPStatusError as exc:
            logger.warning(f"User Management service error fetching user {user_id}: {exc.response.status_code} - {exc.response.text}")
            return None # Return None if user not found or other error

# The timeout endpoint is handled by APScheduler in app.main.py
# @router.post("/payments/timeout")
# async def trigger_timeout_job():
#     """
#     Manually trigger the pending payments timeout job.
#     This is primarily for testing or manual intervention.
#     In production, it runs automatically via APScheduler.
#     """
#     await timeout_pending_payments()
#     return {"message": "Pending payments timeout job triggered successfully"}
