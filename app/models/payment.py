import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, DECIMAL, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

class Payment(Base):
    __tablename__ = "payments"

    id: uuid.UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: uuid.UUID = Column(UUID(as_uuid=True), unique=True, nullable=False) # Added for idempotency
    property_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    user_id: uuid.UUID = Column(UUID(as_uuid=True), nullable=False)
    amount: float = Column(DECIMAL(10, 2), nullable=False, default=100.00)
    status: PaymentStatus = Column(Enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING)
    chapa_tx_ref: str = Column(String, nullable=False)
    created_at: datetime = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: datetime = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Define indexes explicitly if not already done in schema.sql
    # __table_args__ = (
    #     Index('idx_payments_user_id', "user_id"),
    #     Index('idx_payments_property_id', "property_id"),
    #     Index('idx_payments_status', "status"),
    # )

    def __repr__(self):
        return f"<Payment(id={self.id}, user_id={self.user_id}, status={self.status})>"
