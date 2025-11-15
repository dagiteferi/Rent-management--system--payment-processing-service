import uuid
from datetime import datetime
from typing import Optional
from enum import Enum as PyEnum 
from sqlalchemy import Column, String, DateTime, DECIMAL, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy import Enum as SQLAlchemyEnum 

Base = declarative_base()

class PaymentStatus(str, PyEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

class Payment(Base):
    __tablename__ = "payments"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: uuid.UUID = Column(UUID(as_uuid=True), unique=True, nullable=False) # Added for idempotency
    property_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    user_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    amount: float = Column(DECIMAL(10, 2), nullable=False, default=500.00) # Fixed amount
    status: PaymentStatus = Column(SQLAlchemyEnum(PaymentStatus, name="payment_status"), nullable=False, default=PaymentStatus.PENDING)
    chapa_tx_ref: str = Column(String, nullable=False)
    failure_reason: Optional[str] = Column(String, nullable=True) # New field for Chapa error details
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
    approved_at: Optional[datetime] = Column(DateTime(timezone=True), nullable=True) # New field for approval timestamp

    __table_args__ = (
        Index('idx_payments_property_id', "property_id"),
        Index('idx_payments_status', "status"),
        Index('idx_payments_chapa_tx_ref', "chapa_tx_ref"), # For faster webhook lookups
    )

    def __repr__(self):
        return f"<Payment(id={self.id}, user_id={self.user_id}, status={self.status})>"