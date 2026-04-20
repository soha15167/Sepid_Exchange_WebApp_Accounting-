# app/models.py
from __future__ import annotations

from sqlalchemy import Column, Integer, Float, String, DateTime
from .database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)

    # عمومی
    type = Column(String, index=True)
    amount = Column(Float, default=0)
    euro_rate_sell = Column(Float, default=0)
    euro_rate_buy = Column(Float, default=0)
    buyer_name = Column(String, default="")
    seller_name = Column(String, default="")
    fee = Column(Float, default=0)
    profit = Column(Float, default=0)
    description = Column(String, default="")
    toman_amount = Column(Float, default=0)

    # تاریخ میلادی (برای سازگاری)
    date = Column(DateTime, nullable=True)

    # --- ایران ---
    iran_type = Column(String, nullable=True)          # "ورودی" / "خروجی" / "تسویه مالیات"
    iran_amount = Column(Float, default=0)
    bank_name = Column(String, nullable=True)          # بانک منبع (برای خروجی: بانک پرداخت‌کننده)
    destination_bank = Column(String, nullable=True)   # بانک مقصد (برای خروجی به بانک‌های دیگر)
    transfer_type = Column(String, nullable=True)      # "کارت به کارت" / "شبا" / "ساتنا" / "حساب به حساب" / "پایا"
    depositor_name = Column(String, nullable=True)
    deposit_fee = Column(Float, default=0)             # کارمزد خروجی
    tax = Column(Float, default=0)                     # مالیات خروجی

    # تاریخ شمسی ذخیره‌شده (کلید اصلی نمایش/گزارش/ماهانه)
    jdate = Column(String, nullable=True)              # مثل "1404/10/07"
