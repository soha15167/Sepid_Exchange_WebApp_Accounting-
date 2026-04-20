from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from sqlalchemy.orm import Session

from . import models

Region = Literal["iran", "de"]


BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
META_PATH = REPORTS_DIR / "meta.json"


def _ensure_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "iran").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "de").mkdir(parents=True, exist_ok=True)


def _load_meta() -> Dict[str, dict]:
    _ensure_dirs()
    if not META_PATH.exists():
        return {}
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _save_meta(meta: Dict[str, dict]) -> None:
    _ensure_dirs()
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _meta_key(region: Region, month: str) -> str:
    return f"{region}:{month}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iran_month_key(jdate: Optional[str]) -> Optional[str]:
    # expects "YYYY/MM/DD" -> "YYYY-MM"
    if not jdate:
        return None
    s = str(jdate).strip()
    if len(s) < 7:
        return None
    parts = s.split("/")
    if len(parts) < 2:
        return None
    y, m = parts[0], parts[1]
    if not (y.isdigit() and m.isdigit()):
        return None
    return f"{int(y):04d}-{int(m):02d}"


def _de_month_key(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.strftime("%Y-%m")


def list_available_months(db: Session) -> Dict[str, List[str]]:
    # months derived from existing transactions
    iran_months = set()
    de_months = set()
    for t in db.query(models.Transaction).all():
        if (t.type or "").strip() == "ایران":
            mk = _iran_month_key(t.jdate)
            if mk:
                iran_months.add(mk)
        else:
            mk = _de_month_key(t.date)
            if mk:
                de_months.add(mk)
    return {"iran": sorted(iran_months), "de": sorted(de_months)}


def report_path(region: Region, month: str) -> Path:
    _ensure_dirs()
    safe_month = str(month).strip()
    return REPORTS_DIR / region / f"{safe_month}.pdf"


def get_reports_index(db: Session) -> Dict[str, object]:
    meta = _load_meta()
    months = list_available_months(db)

    def build(region: Region) -> List[dict]:
        out: List[dict] = []
        for m in months[region]:
            key = _meta_key(region, m)
            entry = meta.get(key, {})
            out.append(
                {
                    "region": region,
                    "month": m,
                    "display_name": entry.get("display_name") or f"{region.upper()} {m}",
                    "updated_at": entry.get("updated_at"),
                    "exists": report_path(region, m).exists(),
                }
            )
        return out

    return {"iran": build("iran"), "de": build("de")}


def set_display_name(region: Region, month: str, display_name: str) -> None:
    meta = _load_meta()
    key = _meta_key(region, month)
    meta[key] = {
        **(meta.get(key) or {}),
        "display_name": (display_name or "").strip(),
        "updated_at": _now_iso(),
    }
    _save_meta(meta)


def delete_report(region: Region, month: str) -> None:
    p = report_path(region, month)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    meta = _load_meta()
    key = _meta_key(region, month)
    if key in meta:
        meta.pop(key, None)
        _save_meta(meta)


def _fa_text(s: str) -> str:
    """
    Persian shaping for PDF. Works best with a Persian-capable TTF (e.g. Tahoma/Vazirmatn).
    If reshaper isn't available, returns raw text.
    """
    try:
        import arabic_reshaper  # type: ignore
        from bidi.algorithm import get_display  # type: ignore

        reshaped = arabic_reshaper.reshape(s)
        return get_display(reshaped)
    except Exception:
        return s


def _register_font(canvas) -> Tuple[str, bool]:
    """
    Try to register a Persian-capable font. Prefer Windows Tahoma if available.
    Returns (font_name, ok)
    """
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # Windows default
        tahoma = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "tahoma.ttf"
        if tahoma.exists():
            pdfmetrics.registerFont(TTFont("Tahoma", str(tahoma)))
            return "Tahoma", True
    except Exception:
        pass
    return "Helvetica", False


def regenerate_month_pdf(db: Session, region: Region, month: str) -> Path:
    _ensure_dirs()
    p = report_path(region, month)

    from reportlab.lib import colors  # type: ignore
    from reportlab.lib.pagesizes import A4, landscape  # type: ignore
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # type: ignore

    # برای ایران Landscape بهتر است تا همه ستون‌ها در یک خط جا شوند
    page_size = landscape(A4) if region == "iran" else A4

    doc = SimpleDocTemplate(
        str(p),
        pagesize=page_size,
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
        title=f"{region}-{month}",
    )

    # Register font (Tahoma on Windows) for Persian
    try:
        from reportlab.pdfbase import pdfmetrics  # type: ignore
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore

        tahoma = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "tahoma.ttf"
        if tahoma.exists():
            pdfmetrics.registerFont(TTFont("Tahoma", str(tahoma)))
            base_font = "Tahoma"
        else:
            base_font = "Helvetica"
    except Exception:
        base_font = "Helvetica"

    styles = getSampleStyleSheet()
    rtl_style = ParagraphStyle(
        "rtl",
        parent=styles["Normal"],
        fontName=base_font,
        fontSize=9,
        leading=12,
        alignment=2,  # RIGHT
    )
    rtl_head = ParagraphStyle(
        "rtl_head",
        parent=rtl_style,
        fontSize=11,
        leading=14,
        spaceAfter=10,
    )
    ltr_style = ParagraphStyle(
        "ltr",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=0,  # LEFT
    )

    story = []

    if region == "iran":
        story.append(Paragraph(_fa_text(f"گزارش ماهانه ایران — {month}"), rtl_head))
        ym = month.replace("-", "/")
        items = (
            db.query(models.Transaction)
            .filter(models.Transaction.type == "ایران", models.Transaction.jdate.like(f"{ym}%"))
            .order_by(models.Transaction.id.asc())
            .all()
        )
        header = ["#", "تاریخ", "نوع", "بانک", "مقصد", "مبلغ", "کارمزد", "مالیات", "توضیح"]
        # نکته: برای اینکه هر خانه در یک خط بماند، از Paragraph استفاده نمی‌کنیم (wrap می‌کند).
        data = [[_fa_text(h) for h in header]]

        # Landscape: عرض ستون‌ها (جمع باید داخل عرض صفحه جا شود)
        col_widths = [18, 70, 55, 70, 75, 70, 60, 60, 260]

        def clip_to_width(txt: str, width_pt: float) -> str:
            """
            متن را طوری کوتاه می‌کند که داخل عرض ستون جا شود
            (به‌جای wrap شدن/به‌هم‌ریختن جدول).
            """
            s = _fa_text(str(txt or ""))
            max_w = max(0.0, float(width_pt) - 10.0)
            try:
                from reportlab.pdfbase import pdfmetrics  # type: ignore

                def w(t: str) -> float:
                    return float(pdfmetrics.stringWidth(t, base_font, 9))

                if w(s) <= max_w:
                    return s
                ell = "…"
                out = s
                while out and w(out + ell) > max_w:
                    out = out[:-1]
                return (out + ell) if out else ell
            except Exception:
                return s[:80]
        i = 1
        for t in items:
            row = [
                str(i),
                str(t.jdate or ""),
                str(t.iran_type or ""),
                str(t.bank_name or ""),
                str(t.destination_bank or ""),
                f"{int(t.iran_amount or 0):,}",
                f"{int(t.deposit_fee or 0):,}",
                f"{int(t.tax or 0):,}",
                str(t.description or ""),
            ]
            clipped = [clip_to_width(row[idx], col_widths[idx]) for idx in range(len(col_widths))]
            data.append(clipped)
            i += 1

        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#e5e7eb")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, -1), base_font),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph(f"Monthly Report (DE) — {month}", styles["Title"]))
        items = (
            db.query(models.Transaction)
            .filter(models.Transaction.type != "ایران")
            .order_by(models.Transaction.id.asc())
            .all()
        )
        filtered = [t for t in items if _de_month_key(t.date) == month]
        header = ["#", "date", "type", "amount", "sell", "buy", "fee", "profit", "toman", "desc"]
        data = [header]
        i = 1
        for t in filtered:
            data.append(
                [
                    str(i),
                    (t.date.strftime("%Y-%m-%d") if t.date else ""),
                    str(t.type or ""),
                    str(t.amount or 0),
                    str(t.euro_rate_sell or 0),
                    str(t.euro_rate_buy or 0),
                    str(t.fee or 0),
                    str(t.profit or 0),
                    str(t.toman_amount or 0),
                    str(t.description or ""),
                ]
            )
            i += 1

        data2 = [[Paragraph(str(x), ltr_style) for x in row] for row in data]
        col_widths = [18, 60, 55, 45, 40, 40, 35, 40, 45, 170]
        table = Table(data2, colWidths=col_widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(table)

    doc.build(story)

    # touch meta updated_at
    meta = _load_meta()
    key = _meta_key(region, month)
    meta[key] = {**(meta.get(key) or {}), "updated_at": _now_iso()}
    if not meta[key].get("display_name"):
        meta[key]["display_name"] = f"{region.upper()} {month}"
    _save_meta(meta)

    return p


def regenerate_for_transaction_change(db: Session, old_obj: Optional[models.Transaction], new_obj: Optional[models.Transaction]) -> None:
    """
    Regenerate PDFs for affected month(s). Called from CRUD after commit.
    """
    affected: List[Tuple[Region, str]] = []

    def add(region: Region, month_key: Optional[str]) -> None:
        if month_key:
            affected.append((region, month_key))

    if old_obj is not None:
        if (old_obj.type or "").strip() == "ایران":
            add("iran", _iran_month_key(old_obj.jdate))
        else:
            add("de", _de_month_key(old_obj.date))

    if new_obj is not None:
        if (new_obj.type or "").strip() == "ایران":
            add("iran", _iran_month_key(new_obj.jdate))
        else:
            add("de", _de_month_key(new_obj.date))

    # de-dup
    affected = list(dict.fromkeys(affected))
    for region, month in affected:
        try:
            regenerate_month_pdf(db, region, month)
        except Exception:
            # keep app responsive even if PDF generation fails
            continue

