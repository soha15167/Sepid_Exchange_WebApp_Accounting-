from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import not_, or_
from sqlalchemy.orm import Session

from . import models, schemas

IRAN_TYPE_VALUES = {"ایران", "iran", "Iran", "IRAN"}
TAX_BANK = "سامان (مالیات)"
AUTO_TAX_RATE = 0.002  # 0.2%

# جدول کارمزد 1404 برای انواع انتقالی که در سیستم استفاده می‌کنی
FEE_RULES = {
    # کارت به کارت / کارت به شبا (سحاب)
    # تا 10,000,000 ریال = 9,000 ریال
    # هر 10M یا کسریِ مازاد = 3,200 ریال اضافه
    "کارت به کارت": {
        "type": "tiered",
        "base_fee": 9000,
        "extra_fee": 3200,
        "tier_size": 10_000_000,
    },
    "شبا": {
        "type": "tiered",
        "base_fee": 9000,
        "extra_fee": 3200,
        "tier_size": 10_000_000,
    },

    # پایا: 0.01% با کف و سقف
    "پایا": {
        "type": "percent",
        "value": 0.0001,
        "min": 3000,
        "max": 75000,
    },

    # ساتنا: 0.02% تا سقف 350,000
    "ساتنا": {
        "type": "percent",
        "value": 0.0002,
        "min": 0,
        "max": 350000,
    },

    # حساب به حساب: 0.01%
    "حساب به حساب": {
        "type": "percent",
        "value": 0.0001,
    },

    # نقدی خودپرداز بین‌بانکی: 0.5% با کف 2,000
    "نقدی": {
        "type": "percent",
        "value": 0.005,
        "min": 2000,
    },

    # پیش‌فرض
    "نامشخص": {
        "type": "percent",
        "value": 0.0001,
    },
}


def normalize_type(t: Optional[str]) -> str:
    if not t:
        return ""
    t2 = str(t).strip()
    if t2 in IRAN_TYPE_VALUES:
        return "ایران"
    return t2


def _iran_filter():
    return or_(models.Transaction.type == "ایران", models.Transaction.type == "iran")


def _not_settle_filter():
    return or_(
        models.Transaction.transfer_type.is_(None),
        ~models.Transaction.transfer_type.like("SETTLE_TAX|%"),
    )


def _safe_jdate(jdate: Optional[str]) -> Optional[str]:
    if not jdate:
        return None

    import re

    s = str(jdate).strip().replace("-", "/")
    m = re.match(r"^(\d{4})\/(\d{1,2})\/(\d{1,2})$", s)
    if not m:
        return None

    y = int(m.group(1))
    mo = int(m.group(2))
    d = int(m.group(3))

    if not (1300 <= y <= 1600):
        return None
    if not (1 <= mo <= 12):
        return None

    if mo <= 6:
        max_day = 31
    elif mo <= 11:
        max_day = 30
    else:
        max_day = 29

    if not (1 <= d <= max_day):
        return None

    return f"{y:04d}/{mo:02d}/{d:02d}"


def _is_settle_transfer_type(transfer_type: Optional[str]) -> bool:
    return str(transfer_type or "").strip().startswith("SETTLE_TAX|")


def _settle_tag(jdate: str, tx_id: int | str) -> str:
    return f"SETTLE_TAX|{jdate}|{tx_id}"


def _compute_fee(amount: float, transfer_type: str) -> float:
    t = (transfer_type or "نامشخص").strip()
    rule = FEE_RULES.get(t) or FEE_RULES["نامشخص"]

    if amount <= 0:
        return 0.0

    rtype = rule.get("type", "percent")

    if rtype == "tiered":
        import math

        base_fee = float(rule.get("base_fee", 9000))
        extra_fee = float(rule.get("extra_fee", 3200))
        tier_size = float(rule.get("tier_size", 10_000_000))

        if amount <= tier_size:
            return round(base_fee)

        extra_tiers = math.ceil((amount - tier_size) / tier_size)
        return round(base_fee + extra_tiers * extra_fee)

    if rtype == "fixed":
        return round(float(rule.get("value", 0)))

    if rtype == "percent":
        val = float(rule.get("value", 0))
        min_fee = float(rule.get("min", 0))
        max_fee = float(rule.get("max", float("inf")))
        fee = amount * val
        fee = max(fee, min_fee)
        fee = min(fee, max_fee)
        return round(fee)

    return 0.0


def _apply_auto_fee_tax_if_needed(data: dict) -> None:
    """
    برای خروجی از حساب:
    - مالیات = 0.2%
    - کارمزد طبق جدول 1404
    - برای تسویه مالیات یا صندوق TAX_BANK همیشه fee/tax صفر
    """
    if data.get("type") != "ایران":
        return

    iran_type = (data.get("iran_type") or "").strip()
    bank = (data.get("bank_name") or "").strip()
    transfer_type = (data.get("transfer_type") or "نامشخص").strip()

    if iran_type != "خروجی":
        return

    if _is_settle_transfer_type(transfer_type):
        data["deposit_fee"] = 0
        data["tax"] = 0
        data["destination_bank"] = TAX_BANK
        return

    if bank == TAX_BANK:
        data["deposit_fee"] = 0
        data["tax"] = 0
        return

    amt = float(data.get("iran_amount") or 0)

    dep_fee = data.get("deposit_fee")
    tax = data.get("tax")

    if dep_fee in (None, "") or float(dep_fee or 0) <= 0:
        data["deposit_fee"] = _compute_fee(amt, transfer_type)

    if tax in (None, "") or float(tax or 0) <= 0:
        data["tax"] = round(amt * AUTO_TAX_RATE)


def get_iran_transactions(db: Session) -> List[models.Transaction]:
    # جدیدترین ثبت‌شده پایین‌تر دیده شود
    return (
        db.query(models.Transaction)
        .filter(_iran_filter())
        .order_by(models.Transaction.id.asc())
        .all()
    )


def get_germany_transactions(db: Session) -> List[models.Transaction]:
    """تراکنش‌های بخش آلمان (همان مسیری که /transactions/de مصرف می‌کند)."""
    return (
        db.query(models.Transaction)
        .filter(not_(_iran_filter()))
        .order_by(models.Transaction.id.desc())
        .all()
    )


def _required_tax_for_date(db: Session, jdate: str) -> float:
    """
    مالیات لازم برای یک تاریخ:
    جمع 0.2% خروجی‌های واقعی همان تاریخ
    """
    jd = _safe_jdate(jdate)
    if not jd:
        return 0.0

    items = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.iran_type == "خروجی",
            models.Transaction.jdate == jd,
            models.Transaction.bank_name != TAX_BANK,
            _not_settle_filter(),
        )
        .all()
    )

    total = 0.0
    for t in items:
        total += round(float(t.iran_amount or 0) * AUTO_TAX_RATE)

    return float(int(round(total)))


def _settle_transactions_for_date(db: Session, jdate: str) -> List[models.Transaction]:
    jd = _safe_jdate(jdate)
    if not jd:
        return []

    return (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.jdate == jd,
            models.Transaction.transfer_type.like("SETTLE_TAX|%"),
        )
        .order_by(models.Transaction.id.asc())
        .all()
    )


def _settled_tax_for_date(db: Session, jdate: str) -> float:
    total = 0.0
    for t in _settle_transactions_for_date(db, jdate):
        total += float(t.iran_amount or 0)
    return total


def _reconcile_settlement_for_date(db: Session, jdate: Optional[str]) -> None:
    """
    اگر برای یک تاریخ قبلاً تسویه زده شده باشد،
    بعد از هر ویرایش/حذف/افزودن روی همان تاریخ، همان ردیف تسویه آپدیت می‌شود.
    """
    jd = _safe_jdate(jdate)
    if not jd:
        return

    required = _required_tax_for_date(db, jd)
    settle_txs = _settle_transactions_for_date(db, jd)

    # اگر دیگر مالیاتی لازم نیست، تسویه همان تاریخ حذف شود
    if required <= 0:
        if settle_txs:
            for t in settle_txs:
                db.delete(t)
            db.commit()
        return

    # اگر هنوز برای این تاریخ تسویه‌ای ثبت نشده، چیزی آپدیت نکن
    if not settle_txs:
        return

    # فقط یک ردیف تسویه برای هر تاریخ نگه می‌داریم
    primary = settle_txs[0]
    primary.type = "ایران"
    primary.iran_type = "خروجی"
    primary.destination_bank = TAX_BANK
    primary.depositor_name = "تسویه مالیات"
    primary.deposit_fee = 0
    primary.tax = 0
    primary.iran_amount = required
    primary.jdate = jd
    primary.transfer_type = _settle_tag(jd, primary.id)

    if not (primary.description or "").strip():
        primary.description = f"تسویه مالیات → {TAX_BANK}"

    for extra in settle_txs[1:]:
        db.delete(extra)

    db.commit()
    db.refresh(primary)


def create_transaction(db: Session, transaction: schemas.TransactionCreate) -> models.Transaction:
    data = transaction.model_dump()
    data["type"] = normalize_type(data.get("type"))
    data["jdate"] = _safe_jdate(data.get("jdate"))

    if not data.get("date"):
        data["date"] = datetime.now()

    _apply_auto_fee_tax_if_needed(data)

    obj = models.Transaction(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)

    if obj.type == "ایران" and obj.jdate:
        # اگر کسی به هر دلیلی settle ساخت، tag کامل شود
        if _is_settle_transfer_type(obj.transfer_type):
            obj.transfer_type = _settle_tag(obj.jdate, obj.id)
            obj.destination_bank = TAX_BANK
            obj.deposit_fee = 0
            obj.tax = 0
            db.commit()
            db.refresh(obj)

        _reconcile_settlement_for_date(db, obj.jdate)

    return obj


def update_transaction(
    db: Session,
    transaction_id: int,
    transaction: schemas.TransactionUpdate,
) -> Optional[models.Transaction]:
    obj = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not obj:
        return None

    old_type = obj.type
    old_jdate = obj.jdate

    data = transaction.model_dump(exclude_unset=True)

    if "type" in data and data.get("type") is not None:
        data["type"] = normalize_type(data.get("type"))

    if "jdate" in data:
        data["jdate"] = _safe_jdate(data.get("jdate"))

    incoming_tt = (data.get("transfer_type") or "").strip()

    # اگر ردیف تسویه باشد، ساختار آن حفظ شود
    if incoming_tt == "تسویه مالیات" or _is_settle_transfer_type(obj.transfer_type):
        jd_for_tag = data.get("jdate") or obj.jdate
        if jd_for_tag:
            data["transfer_type"] = _settle_tag(jd_for_tag, obj.id)
        else:
            data["transfer_type"] = obj.transfer_type or "SETTLE_TAX|tmp"

        data["destination_bank"] = TAX_BANK
        data["deposit_fee"] = 0
        data["tax"] = 0

    merged_for_auto = {
        "type": data.get("type", obj.type),
        "iran_type": data.get("iran_type", obj.iran_type),
        "bank_name": data.get("bank_name", obj.bank_name),
        "destination_bank": data.get("destination_bank", obj.destination_bank),
        "transfer_type": data.get("transfer_type", obj.transfer_type),
        "iran_amount": data.get("iran_amount", obj.iran_amount),
        "deposit_fee": data.get("deposit_fee", obj.deposit_fee),
        "tax": data.get("tax", obj.tax),
    }
    _apply_auto_fee_tax_if_needed(merged_for_auto)

    if merged_for_auto.get("type") == "ایران":
        data["deposit_fee"] = merged_for_auto.get("deposit_fee")
        data["tax"] = merged_for_auto.get("tax")
        data["destination_bank"] = merged_for_auto.get("destination_bank")

    for k, v in data.items():
        setattr(obj, k, v)

    db.commit()
    db.refresh(obj)

    affected_dates = set()
    if old_type in IRAN_TYPE_VALUES and old_jdate:
        affected_dates.add(old_jdate)
    if obj.type == "ایران" and obj.jdate:
        affected_dates.add(obj.jdate)

    for jd in affected_dates:
        _reconcile_settlement_for_date(db, jd)

    db.refresh(obj)
    return obj


def delete_transaction(db: Session, transaction_id: int) -> bool:
    obj = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not obj:
        return False

    jd = obj.jdate
    typ = obj.type

    db.delete(obj)
    db.commit()

    if typ in IRAN_TYPE_VALUES and jd:
        _reconcile_settlement_for_date(db, jd)

    return True


def compute_iran_pending_tax(db: Session) -> float:
    """
    مالیات تسویه‌نشده = جمع (مالیات لازم هر تاریخ - تسویه همان تاریخ)
    اگر تاریخی قبلاً تسویه شده و بعداً ویرایش/حذف/افزودن شده باشد،
    reconcile همان تاریخ را آپدیت می‌کند و pending برای آن تاریخ بالا نمی‌رود.
    """
    real_outs = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.iran_type == "خروجی",
            models.Transaction.bank_name != TAX_BANK,
            _not_settle_filter(),
        )
        .all()
    )

    settle_txs = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.transfer_type.like("SETTLE_TAX|%"),
        )
        .all()
    )

    required_by_date: Dict[str, float] = {}
    for t in real_outs:
        jd = _safe_jdate(t.jdate)
        if not jd:
            continue
        required_by_date[jd] = required_by_date.get(jd, 0.0) + round(float(t.iran_amount or 0) * AUTO_TAX_RATE)

    settled_by_date: Dict[str, float] = {}
    for t in settle_txs:
        jd = _safe_jdate(t.jdate)
        if not jd:
            continue
        settled_by_date[jd] = settled_by_date.get(jd, 0.0) + float(t.iran_amount or 0)

    pending = 0.0
    for jd, required in required_by_date.items():
        settled = settled_by_date.get(jd, 0.0)
        diff = required - settled
        if diff > 0:
            pending += diff

    return pending


def compute_iran_balances(db: Session) -> Dict[str, object]:
    items = get_iran_transactions(db)

    balances: Dict[str, float] = {}
    tax_balance = 0.0

    for t in items:
        bank = (t.bank_name or "").strip()
        if not bank:
            continue

        iran_type = (t.iran_type or "").strip()
        amt = float(t.iran_amount or 0)
        dep_fee = float(t.deposit_fee or 0)
        tax = float(t.tax or 0)
        is_settle = _is_settle_transfer_type(t.transfer_type)

        # صندوق مالیات جدا
        if bank == TAX_BANK:
            if iran_type in ("ورودی", "تسویه مالیات"):
                tax_balance += amt
            elif iran_type == "خروجی":
                tax_balance -= (amt + dep_fee + tax)
            continue

        balances.setdefault(bank, 0.0)

        if iran_type == "ورودی":
            balances[bank] += amt

        elif iran_type == "خروجی":
            balances[bank] -= (amt + dep_fee + tax)

            # اگر خروجی از نوع تسویه مالیات باشد، مبلغ به صندوق مالیات اضافه می‌شود
            if is_settle:
                tax_balance += amt

    total = sum(balances.values()) if balances else 0.0
    return {
        "balances": balances,
        "total": total,
        "tax_bank_balance": tax_balance,
    }


def _richest_bank(db: Session) -> Optional[str]:
    result = compute_iran_balances(db)
    balances = result.get("balances", {})
    if not balances:
        return None
    return max(balances, key=lambda k: balances[k])


def settle_iran_tax(db: Session, from_bank: str, jdate: str, description: str = "") -> Optional[Dict]:
    """
    فقط یک ردیف تسویه برای هر تاریخ.
    هنگام کلیک دکمه:
    - مبلغ = کل مالیات لازم همان تاریخ
    - بانک پرداخت‌کننده = بانکی که بیشترین مانده را دارد
    - اگر قبلاً برای این تاریخ تسویه ثبت شده، همان ردیف آپدیت می‌شود
    """
    jd = _safe_jdate(jdate)
    if not jd:
        return None

    required = _required_tax_for_date(db, jd)
    if required <= 0:
        # اگر چیزی برای این تاریخ لازم نیست، settle قبلی هم پاک شود
        existing = _settle_transactions_for_date(db, jd)
        if existing:
            for t in existing:
                db.delete(t)
            db.commit()
        return None

    # طبق خواسته تو، از بانکی که بیشترین مانده را دارد
    auto_bank = _richest_bank(db)
    fb = (auto_bank or from_bank or "").strip()
    if not fb or fb == TAX_BANK:
        return None

    desc = (description or "").strip()
    base_desc = f"تسویه مالیات → {TAX_BANK}"
    full_desc = base_desc + (f" — {desc}" if desc else "")

    settle_txs = _settle_transactions_for_date(db, jd)

    if settle_txs:
        tx = settle_txs[0]
        tx.type = "ایران"
        tx.iran_type = "خروجی"
        tx.bank_name = fb
        tx.destination_bank = TAX_BANK
        tx.iran_amount = required
        tx.depositor_name = "تسویه مالیات"
        tx.deposit_fee = 0
        tx.tax = 0
        tx.description = full_desc
        tx.transfer_type = _settle_tag(jd, tx.id)

        for extra in settle_txs[1:]:
            db.delete(extra)

        db.commit()
        db.refresh(tx)
        return {"settle_id": tx.id, "amount": required, "from_bank": fb}

    settle_tx = models.Transaction(
        type="ایران",
        iran_type="خروجی",
        bank_name=fb,
        destination_bank=TAX_BANK,
        iran_amount=required,
        depositor_name="تسویه مالیات",
        transfer_type="SETTLE_TAX|tmp",
        deposit_fee=0,
        tax=0,
        description=full_desc,
        date=datetime.now(),
        jdate=jd,
    )
    db.add(settle_tx)
    db.commit()
    db.refresh(settle_tx)

    settle_tx.transfer_type = _settle_tag(jd, settle_tx.id)
    db.commit()
    db.refresh(settle_tx)

    return {"settle_id": settle_tx.id, "amount": required, "from_bank": fb}