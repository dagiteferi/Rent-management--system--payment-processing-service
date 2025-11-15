import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.payment import PaymentStatus

class PaymentBase(BaseModel):
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4) # Added for idempotency
    property_id: uuid.UUID
    user_id: uuid.UUID
    amount: float = Field(default=500.00, ge=0, json_schema_extra={"examples": [500.00]}) # Re-added amount

class PaymentCreate(PaymentBase):
    pass

class PaymentUpdate(BaseModel):
    status: PaymentStatus
    chapa_tx_ref: Optional[str] = None

class PaymentResponse(PaymentBase):
    id: uuid.UUID
    status: PaymentStatus
    chapa_tx_ref: Optional[str] = None # Made optional, as checkout_url is primary for client
    checkout_url: Optional[str] = None # Added checkout_url field
    created_at: datetime
    updated_at: datetime
    failure_reason: Optional[str] = None
    approved_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ChapaInitializeRequest(BaseModel):
    amount: str
    currency: str = "ETB"
    email: str
    first_name: str
    last_name: str
    phone_number: Optional[str] = None # Changed from mobile to phone_number, and made optional
    tx_ref: str
    callback_url: str
    return_url: str
    customization: Optional[dict] = None
    meta: Optional[dict] = None

class ChapaInitializeResponse(BaseModel):
    message: str
    status: str
    data: dict

class ChapaVerifyResponse(BaseModel):
    message: str
    status: str
    data: dict

class WebhookEvent(BaseModel):
    event: str
    data: dict

class UserAuthResponse(BaseModel):
    user_id: uuid.UUID
    role: str
    email: str
    phone_number: str
    preferred_language: str

class NotificationPayload(BaseModel):
    user_id: uuid.UUID
    email: str
    phone_number: str
    preferred_language: str
    message: str
    subject: str
