from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_serializer


class TransactionCreate(BaseModel):
    # عمومی
    type: str
    amount: float = 0
    euro_rate_sell: float = 0
    euro_rate_buy: float = 0
    buyer_name: Optional[str] = ""
    seller_name: Optional[str] = ""
    fee: float = 0
    profit: float = 0
    description: Optional[str] = ""
    toman_amount: float = 0

    # تاریخ میلادی
    date: Optional[datetime] = None

    # ایران
    iran_type: Optional[str] = None
    iran_amount: Optional[float] = 0
    bank_name: Optional[str] = None
    destination_bank: Optional[str] = None
    transfer_type: Optional[str] = None
    depositor_name: Optional[str] = None
    deposit_fee: Optional[float] = 0
    tax: Optional[float] = 0
    jdate: Optional[str] = None


class TransactionUpdate(BaseModel):
    # عمومی
    type: Optional[str] = None
    amount: Optional[float] = None
    euro_rate_sell: Optional[float] = None
    euro_rate_buy: Optional[float] = None
    buyer_name: Optional[str] = None
    seller_name: Optional[str] = None
    fee: Optional[float] = None
    profit: Optional[float] = None
    description: Optional[str] = None
    toman_amount: Optional[float] = None
    date: Optional[datetime] = None

    # ایران
    iran_type: Optional[str] = None
    iran_amount: Optional[float] = None
    bank_name: Optional[str] = None
    destination_bank: Optional[str] = None
    transfer_type: Optional[str] = None
    depositor_name: Optional[str] = None
    deposit_fee: Optional[float] = None
    tax: Optional[float] = None
    jdate: Optional[str] = None


class TransactionOut(TransactionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int

    @field_serializer("date")
    def serialize_date(self, v: Optional[datetime]):
        if not v:
            return None
        return v.strftime("%Y-%m-%d")