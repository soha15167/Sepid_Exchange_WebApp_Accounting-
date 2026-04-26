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
            tt = str(t.transfer_type or "").strip()
            is_legacy = tt.startswith("SETTLE_TAX|") or tt.startswith("SETTLE_TAX_LOCK|") or tt.startswith("SETTLE_RUN|")
            if is_legacy:
                continue
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


def _pick_font_path() -> Optional[Path]:
    """
    Find a Persian/Arabic-capable TTF on the current OS.
    - Windows: Tahoma
    - Linux: Noto/DejaVu common paths
    """
    # Explicit override (useful on servers)
    override = (os.environ.get("SEPID_PDF_FONT") or "").strip()
    if override:
        p = Path(override)
        if p.exists():
            return p

    # Windows default
    tahoma = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "tahoma.ttf"
    if tahoma.exists():
        return tahoma

    linux_candidates = [
        # Noto (best)
        Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoNaskhArabicUI-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansArabicUI-Regular.ttf"),
        # Noto Kufi Arabic (common in fonts-noto-extra)
        Path("/usr/share/fonts/truetype/noto/NotoKufiArabic-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoKufiArabic-Medium.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf"),
        # Vazirmatn (some distros)
        Path("/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf"),
        Path("/usr/share/fonts/truetype/vazirmatn/Vazirmatn-FD-Regular.ttf"),
        # DejaVu fallback
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"),
        # FreeFont fallback
        Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
    ]
    for p in linux_candidates:
        if p.exists():
            return p
    return None


def _register_font_name() -> Tuple[str, bool]:
    """
    Register a font in reportlab and return (font_name, ok).
    """
    try:
        from reportlab.pdfbase import pdfmetrics  # type: ignore
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore

        p = _pick_font_path()
        if not p:
            return "Helvetica", False
        name = "SepidFont"
        pdfmetrics.registerFont(TTFont(name, str(p)))
        return name, True
    except Exception:
        return "Helvetica", False


def regenerate_month_pdf(db: Session, region: Region, month: str) -> Path:
    _ensure_dirs()
    p = report_path(region, month)

    from reportlab.lib import colors  # type: ignore
    from reportlab.lib.pagesizes import A4, landscape  # type: ignore
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # type: ignore

    # برای ایران: صفحه عریض‌تر از A4 (برای اینکه جدول کامل و بدون فشرده‌سازی جا شود).
    # ارتفاع مثل A4 landscape می‌ماند تا پرینت با "Fit/Scale" راحت انجام شود.
    if region == "iran":
        a4_land = landscape(A4)
        page_size = (1200, a4_land[1])  # wider than A4 landscape
    else:
        page_size = A4

    doc = SimpleDocTemplate(
        str(p),
        pagesize=page_size,
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
        title=f"{region}-{month}",
    )

    base_font, font_ok = _register_font_name()
    if region == "iran" and not font_ok:
        raise RuntimeError(
            "Persian font not found on server. Install a Persian-capable font (recommended: Noto) "
            "or set SEPID_PDF_FONT to a .ttf path. Example on Ubuntu: "
            "`sudo apt install -y fonts-noto-core fonts-noto-extra fonts-dejavu-core`"
        )

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
        # همسو با گزارش: تسویه‌های قدیمی (روزانه/ثبت عملیات) را وارد PDF نکن
        items = [
            t
            for t in items
            if not (
                str(t.transfer_type or "").strip().startswith("SETTLE_TAX|")
                or str(t.transfer_type or "").strip().startswith("SETTLE_TAX_LOCK|")
                or str(t.transfer_type or "").strip().startswith("SETTLE_RUN|")
            )
        ]
        # ترتیب و ستون‌ها دقیقاً مثل صفحه گزارش ایران
        header = [
            "ردیف",
            "نوع",
            "بانک (منبع)",
            "بانک مقصد",
            "نوع حواله",
            "واریز/برداشت کننده",
            "مبلغ (ریال)",
            "کارمزد",
            "مالیات",
            "تاریخ (شمسی)",
            "توضیحات",
        ]
        # کاربر خواسته هیچ کوتاه‌سازی انجام نشود: از Paragraph استفاده می‌کنیم تا متن‌ها کامل و چندخطی نمایش داده شوند.
        data = [[Paragraph(_fa_text(h), rtl_style) for h in header]]

        # عرض ستون‌ها متناسب با صفحه عریض‌تر (جمع ~ عرض مفید صفحه)
        col_widths = [36, 60, 90, 90, 90, 120, 90, 70, 70, 90, 336]

        def cell(v: object) -> Paragraph:
            return Paragraph(_fa_text(str(v or "")), rtl_style)
        i = 1
        def normalize_jdate(s: str) -> str:
            raw = (s or "").strip()
            if not raw:
                return ""
            parts = raw.split("/")
            if len(parts) != 3:
                return raw
            y, m, d = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if len(m) == 1:
                m = "0" + m
            if len(d) == 1:
                d = "0" + d
            return f"{y}/{m}/{d}"

        # دقیقاً مثل گزارش: بر اساس تاریخ شمسی سپس id مرتب
        items = sorted(
            items,
            key=lambda t: (
                normalize_jdate(str(t.jdate or "")) or "9999/99/99",
                int(t.id or 0),
            ),
        )

        for t in items:
            jdate_norm = normalize_jdate(str(t.jdate or ""))
            row = [
                str(i),
                str(t.iran_type or ""),
                str(t.bank_name or ""),
                str(t.destination_bank or ""),
                ("تسویه مالیات (بازه)" if str(t.transfer_type or "").startswith("SETTLE_BATCH|") else str(t.transfer_type or "")),
                str(t.depositor_name or ""),
                f"{int(t.iran_amount or 0):,}",
                f"{int(t.deposit_fee or 0):,}",
                f"{int(t.tax or 0):,}",
                jdate_norm,
                str(t.description or ""),
            ]
            data.append([cell(v) for v in row])
            i += 1

        # RTL واقعی: جدول را از راست به چپ نمایش می‌دهیم (معکوس‌کردن ستون‌ها)
        # (سمت راست = ردیف، سمت چپ = توضیحات؛ مثل گزارش تراکنش)
        data_rtl = [list(reversed(r)) for r in data]
        col_widths_rtl = list(reversed(col_widths))

        table = Table(data_rtl, colWidths=col_widths_rtl, repeatRows=1, hAlign="RIGHT")
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

