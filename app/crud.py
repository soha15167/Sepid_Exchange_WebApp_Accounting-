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
        ~models.Transaction.transfer_type.like("SETTLE_TAX_LOCK|%"),
        ~models.Transaction.transfer_type.like("SETTLE_BATCH|%"),
        ~models.Transaction.transfer_type.like("SETTLE_RUN|%"),
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
    tt = str(transfer_type or "").strip()
    return tt.startswith("SETTLE_TAX|") or tt.startswith("SETTLE_TAX_LOCK|") or tt.startswith("SETTLE_BATCH|")


def _is_settle_locked(transfer_type: Optional[str]) -> bool:
    return str(transfer_type or "").strip().startswith("SETTLE_TAX_LOCK|")


def _settle_tag(jdate: str, tx_id: int | str) -> str:
    return f"SETTLE_TAX|{jdate}|{tx_id}"


def _settle_lock_tag(jdate: str, tx_id: int | str) -> str:
    return f"SETTLE_TAX_LOCK|{jdate}|{tx_id}"


def _settle_run_tag(jdate_end: str, tx_id: int | str) -> str:
    return f"SETTLE_RUN|{jdate_end}|{tx_id}"


def _settle_run_transactions_for_date(db: Session, jdate_end: str) -> List[models.Transaction]:
    jd = _safe_jdate(jdate_end)
    if not jd:
        return []
    return (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.jdate == jd,
            models.Transaction.transfer_type.like("SETTLE_RUN|%"),
        )
        .order_by(models.Transaction.id.asc())
        .all()
    )


def _settle_run_transactions_for_end_date(db: Session, end_date: str) -> List[models.Transaction]:
    """
    ردیف‌های سیستم برای «ثبت عملیات تسویه تا تاریخ X» را پیدا می‌کند،
    حتی اگر jdate آن‌ها (تاریخ اجرا) متفاوت باشد.
    """
    jd_end = _safe_jdate(end_date)
    if not jd_end:
        return []
    needle = f"ثبت عملیات تسویه تا تاریخ {jd_end}"
    return (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.transfer_type.like("SETTLE_RUN|%"),
            models.Transaction.description == needle,
        )
        .order_by(models.Transaction.id.asc())
        .all()
    )


def _settle_batch_tag(prev_end_exclusive: str, end_inclusive: str, tx_id: int | str) -> str:
    return f"SETTLE_BATCH|{prev_end_exclusive}|{end_inclusive}|{tx_id}"


def _settle_batch_transactions(db: Session) -> List[models.Transaction]:
    return (
        db.query(models.Transaction)
        .filter(_iran_filter(), models.Transaction.transfer_type.like("SETTLE_BATCH|%"))
        .order_by(models.Transaction.id.asc())
        .all()
    )


def _parse_settle_batch_tag(tt: Optional[str]) -> Optional[Dict[str, str]]:
    s = str(tt or "").strip()
    if not s.startswith("SETTLE_BATCH|"):
        return None
    parts = s.split("|")
    # SETTLE_BATCH|prev|end|id
    if len(parts) < 4:
        return None
    return {"prev": parts[1], "end": parts[2], "id": parts[3]}


def _required_tax_for_range(db: Session, prev_end_exclusive: str, end_inclusive: str) -> float:
    """
    مالیات لازم برای بازه (prev_end, end] بر اساس خروجی‌های واقعی.
    مبنا = جمع فیلد `tax` همان خروجی‌ها (نه محاسبه مجدد از مبلغ)،
    تا اگر tax به هر دلیل تغییر کند، تسویه هم دقیقاً مطابق آن باشد.
    """
    prev = _safe_jdate(prev_end_exclusive) or "0000/00/00"
    end = _safe_jdate(end_inclusive)
    if not end:
        return 0.0

    items = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.iran_type == "خروجی",
            models.Transaction.bank_name != TAX_BANK,
            _not_settle_filter(),
            models.Transaction.jdate.is_not(None),
            models.Transaction.jdate > prev,
            models.Transaction.jdate <= end,
        )
        .all()
    )
    total = 0
    for t in items:
        total += int(round(float(t.tax or 0)))
    return float(total)


def reconcile_settle_batches(db: Session) -> None:
    """
    تمام تسویه‌های Batch را به ترتیب end_date دوباره محاسبه می‌کند،
    تا اگر تراکنشی در بازه‌های قبلی تغییر کند، مبالغ بعدی هم درست شود.
    """
    batches = _settle_batch_transactions(db)
    if not batches:
        return

    # مرتب‌سازی بر اساس end_date از داخل tag (نه id)
    def end_key(t: models.Transaction) -> str:
        info = _parse_settle_batch_tag(t.transfer_type) or {}
        return _safe_jdate(info.get("end")) or (t.jdate or "")

    batches = sorted(batches, key=end_key)

    prev_end = "0000/00/00"
    for tx in batches:
        info = _parse_settle_batch_tag(tx.transfer_type) or {}
        end = _safe_jdate(info.get("end")) or _safe_jdate(tx.jdate) or None
        if not end:
            continue

        required = _required_tax_for_range(db, prev_end, end)

        # مبلغ تسویه batch (قابل ویرایش دستی نیست)
        tx.type = "ایران"
        tx.iran_type = "خروجی"
        tx.destination_bank = TAX_BANK
        tx.depositor_name = "تسویه مالیات"
        tx.deposit_fee = 0
        tx.tax = 0
        tx.iran_amount = required
        tx.jdate = end
        tx.transfer_type = _settle_batch_tag(prev_end, end, tx.id)

        if not (tx.description or "").strip():
            tx.description = f"تسویه مالیات (بازه) → {TAX_BANK}"

        prev_end = end

    db.commit()


def cleanup_legacy_settlements(db: Session) -> Dict[str, int]:
    """
    پاکسازی ردیف‌های قدیمی تسویه (روزانه/ثبت عملیات) که قبل از منطق Batch ساخته شده‌اند.
    فقط این‌ها حذف می‌شوند:
    - SETTLE_TAX|...
    - SETTLE_TAX_LOCK|...
    - SETTLE_RUN|...
    """
    legacy = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            or_(
                models.Transaction.transfer_type.like("SETTLE_TAX|%"),
                models.Transaction.transfer_type.like("SETTLE_TAX_LOCK|%"),
                models.Transaction.transfer_type.like("SETTLE_RUN|%"),
            ),
        )
        .all()
    )
    n = 0
    for t in legacy:
        db.delete(t)
        n += 1
    db.commit()

    # بعد از حذف، batch ها دوباره محاسبه شوند
    try:
        reconcile_settle_batches(db)
    except Exception:
        pass

    return {"deleted": n}


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
    # جدول گزارش بر اساس تاریخ شمسی مرتب شود (جدیدترین تاریخ پایین)
    # jdate فرمت YYYY/MM/DD دارد و به‌صورت رشته‌ای قابل مرتب‌سازی است.
    return (
        db.query(models.Transaction)
        .filter(_iran_filter())
        .order_by(
            models.Transaction.jdate.is_(None),
            models.Transaction.jdate.asc(),
            models.Transaction.id.asc(),
        )
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
            or_(
                models.Transaction.transfer_type.like("SETTLE_TAX|%"),
                models.Transaction.transfer_type.like("SETTLE_TAX_LOCK|%"),
            ),
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

    # اگر تسویه این تاریخ «قفل دستی» باشد، reconcile نباید آن را تغییر دهد یا حذف کند.
    if settle_txs and _is_settle_locked(settle_txs[0].transfer_type):
        return

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
        # اگر تراکنش واقعی داخل بازه batch ها باشد، batch ها هم باید آپدیت شوند
        try:
            reconcile_settle_batches(db)
        except Exception:
            pass

    # گزارش PDF ماهانه (ایران/آلمان) را به‌روز کن
    try:
        from . import reports

        reports.regenerate_for_transaction_change(db, None, obj)
    except Exception:
        pass

    return obj


def update_transaction(
    db: Session,
    transaction_id: int,
    transaction: schemas.TransactionUpdate,
) -> Optional[models.Transaction]:
    obj = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not obj:
        return None

    old_snapshot = models.Transaction(
        type=obj.type,
        date=obj.date,
        jdate=obj.jdate,
    )

    data = transaction.model_dump(exclude_unset=True)

    if "type" in data and data.get("type") is not None:
        data["type"] = normalize_type(data.get("type"))

    if "jdate" in data:
        data["jdate"] = _safe_jdate(data.get("jdate"))

    incoming_tt = (data.get("transfer_type") or "").strip()

    # اگر ردیف تسویه باشد، ساختار آن حفظ شود
    if incoming_tt == "تسویه مالیات" or _is_settle_transfer_type(obj.transfer_type):
        jd_for_tag = data.get("jdate") or obj.jdate
        is_batch = str(obj.transfer_type or "").strip().startswith("SETTLE_BATCH|")

        if is_batch:
            # ردیف‌های Batch نباید به تسویه قدیمی تبدیل شوند؛ tag batch حفظ می‌شود
            info = _parse_settle_batch_tag(obj.transfer_type) or {"prev": "0000/00/00", "end": jd_for_tag or (obj.jdate or "")}
            prev = _safe_jdate(info.get("prev")) or "0000/00/00"
            end = _safe_jdate(jd_for_tag or info.get("end")) or _safe_jdate(obj.jdate) or None
            if end:
                data["transfer_type"] = _settle_batch_tag(prev, end, obj.id)
                data["destination_bank"] = TAX_BANK
                data["iran_type"] = "خروجی"
                # مبلغ batch توسط reconcile_settle_batches تنظیم می‌شود
        else:
            wants_lock = incoming_tt.startswith("SETTLE_TAX_LOCK|") if incoming_tt else False
            is_locked_now = _is_settle_locked(obj.transfer_type)
            lock = wants_lock or is_locked_now

            if jd_for_tag:
                data["transfer_type"] = _settle_lock_tag(jd_for_tag, obj.id) if lock else _settle_tag(jd_for_tag, obj.id)
            else:
                data["transfer_type"] = obj.transfer_type or ("SETTLE_TAX_LOCK|tmp" if lock else "SETTLE_TAX|tmp")

            data["destination_bank"] = TAX_BANK
            # fee/tax برای ردیف تسویه قابل ویرایش است (طبق نیاز شما)

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

    # اگر خروجی واقعی ایران است، fee/tax باید همیشه مطابق مبلغ/نوع انتقال به‌روز شود.
    # (UI ممکن است fee/tax قبلی را دوباره ارسال کند؛ اینجا عمداً override می‌کنیم.)
    m_type = normalize_type(merged_for_auto.get("type"))
    m_iran_type = (merged_for_auto.get("iran_type") or "").strip()
    m_bank = (merged_for_auto.get("bank_name") or "").strip()
    m_tt = (merged_for_auto.get("transfer_type") or "").strip()
    is_real_iran_out = (
        m_type == "ایران"
        and m_iran_type == "خروجی"
        and m_bank != TAX_BANK
        and (not _is_settle_transfer_type(m_tt))
    )
    if is_real_iran_out:
        merged_for_auto["deposit_fee"] = None
        merged_for_auto["tax"] = None

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
    if (old_snapshot.type or "") in IRAN_TYPE_VALUES and old_snapshot.jdate:
        affected_dates.add(old_snapshot.jdate)
    if obj.type == "ایران" and obj.jdate:
        affected_dates.add(obj.jdate)

    for jd in affected_dates:
        _reconcile_settlement_for_date(db, jd)

    db.refresh(obj)

    if obj.type == "ایران":
        try:
            reconcile_settle_batches(db)
        except Exception:
            pass

    # گزارش PDF ماهانه (ایران/آلمان) را به‌روز کن
    try:
        from . import reports

        reports.regenerate_for_transaction_change(db, old_snapshot, obj)
    except Exception:
        pass

    return obj


def delete_transaction(db: Session, transaction_id: int) -> bool:
    obj = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    if not obj:
        return False

    old_snapshot = models.Transaction(
        type=obj.type,
        date=obj.date,
        jdate=obj.jdate,
    )

    db.delete(obj)
    db.commit()

    if (old_snapshot.type or "") in IRAN_TYPE_VALUES and old_snapshot.jdate:
        _reconcile_settlement_for_date(db, old_snapshot.jdate)
        try:
            reconcile_settle_batches(db)
        except Exception:
            pass

    # گزارش PDF ماهانه را به‌روز کن
    try:
        from . import reports

        reports.regenerate_for_transaction_change(db, old_snapshot, None)
    except Exception:
        pass

    return True


def compute_iran_pending_tax(db: Session) -> float:
    """
    مالیات تسویه‌نشده:
    - در منطق جدید (Batch): جمع مالیات لازم خروجی‌های واقعی - جمع مبالغ تسویه‌های Batch
    - ردیف‌های قدیمی (SETTLE_TAX/SETTLE_RUN) دیگر مبنای محاسبه نیستند.
    """
    # برای جلوگیری از اختلاف چند ریالی/ناهمگامی، قبل از محاسبه batch ها را reconcile کن
    try:
        reconcile_settle_batches(db)
    except Exception:
        pass

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

    batch_settles = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.transfer_type.like("SETTLE_BATCH|%"),
        )
        .all()
    )

    required_total = 0
    for t in real_outs:
        jd = _safe_jdate(t.jdate)
        if not jd:
            continue
        required_total += int(round(float(t.tax or 0)))

    settled_total = 0
    for t in batch_settles:
        settled_total += int(round(float(t.iran_amount or 0)))

    pending = int(required_total - settled_total)
    return float(pending) if pending > 0 else 0.0


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
    تسویه مالیات به صورت بازه‌ای تا تاریخ انتخابی.

    هنگام کلیک دکمه:
    - تاریخ پایان = jdate (شامل همان روز)
    - برای هر تاریخی که خروجی واقعی دارد، مبلغ تسویه = کل مالیات لازم همان تاریخ
    - بانک پرداخت‌کننده = بانکی که بیشترین مانده را دارد (در لحظه کلیک)
    - برای هر تاریخ فقط یک ردیف تسویه نگه می‌داریم؛ اگر قبلاً وجود داشته باشد آپدیت می‌شود

    نکته: بعد از ویرایش/حذف/افزودن خروجی‌ها، به خاطر `_reconcile_settlement_for_date`
    مبلغ تسویه همان تاریخ به صورت خودکار به‌روز می‌شود (بدون کلیک مجدد).
    """
    jd_end = _safe_jdate(jdate)
    if not jd_end:
        return None

    # end date همان تاریخی است که کاربر انتخاب می‌کند (ممکن است چند روز بعد از آخرین تراکنش باشد)

    # طبق خواسته تو، از بانکی که بیشترین مانده را دارد
    auto_bank = _richest_bank(db)
    fb = (auto_bank or from_bank or "").strip()
    if not fb or fb == TAX_BANK:
        return None

    desc = (description or "").strip()
    base_desc = f"تسویه مالیات → {TAX_BANK}"
    full_desc = base_desc + (f" — {desc}" if desc else "")

    # پیدا کردن آخرین batch قبلی (بر اساس end_date داخل tag)
    prev_end = "0000/00/00"
    batches = _settle_batch_transactions(db)
    if batches:
        # آخرین end
        ends = []
        for t in batches:
            info = _parse_settle_batch_tag(t.transfer_type) or {}
            end = _safe_jdate(info.get("end")) or _safe_jdate(t.jdate)
            if end:
                ends.append(end)
        if ends:
            prev_end = max(ends)

    # اگر end انتخابی <= آخرین end قبلی باشد، این یعنی کاربر دارد بازه قبلی را دوباره می‌زند.
    # در این حالت batch همان end را آپدیت می‌کنیم (و بعد reconcile کل batch ها).
    end = jd_end
    required = _required_tax_for_range(db, prev_end, end) if end > prev_end else _required_tax_for_range(db, "0000/00/00", end)

    # پیدا کردن batch برای همین end (اگر وجود داشته باشد)
    existing = (
        db.query(models.Transaction)
        .filter(
            _iran_filter(),
            models.Transaction.jdate == end,
            models.Transaction.transfer_type.like(f"SETTLE_BATCH|%|{end}|%"),
        )
        .order_by(models.Transaction.id.asc())
        .all()
    )

    if existing:
        tx = existing[0]
        tx.type = "ایران"
        tx.iran_type = "خروجی"
        tx.bank_name = fb
        tx.destination_bank = TAX_BANK
        tx.iran_amount = required
        tx.depositor_name = "تسویه مالیات"
        tx.deposit_fee = 0
        tx.tax = 0
        tx.description = full_desc
        # tag بعد از reconcile کامل درست می‌شود
        for extra in existing[1:]:
            db.delete(extra)
        db.commit()
        db.refresh(tx)
    else:
        tx = models.Transaction(
            type="ایران",
            iran_type="خروجی",
            bank_name=fb,
            destination_bank=TAX_BANK,
            iran_amount=required,
            depositor_name="تسویه مالیات",
            transfer_type="SETTLE_BATCH|tmp",
            deposit_fee=0,
            tax=0,
            description=full_desc,
            date=datetime.now(),
            jdate=end,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

    # بعد از هر ایجاد/آپدیت، کل batch ها reconcile شوند تا بازه‌ها زنجیروار درست بمانند
    reconcile_settle_batches(db)

    return {"settle_id": tx.id, "amount": required, "from_bank": fb, "end_date": end}