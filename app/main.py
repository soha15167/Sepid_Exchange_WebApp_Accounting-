from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from .database import SessionLocal, engine
from . import models, schemas, crud, reports

app = FastAPI(title="Sepid Exchange Account")

try:
    models.Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Error creating tables: {e}")


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

templates: Optional[Jinja2Templates] = None

try:
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    templates = Jinja2Templates(directory=str(FRONTEND_DIR))
except Exception as e:
    print(f"Error setting up static/templates: {e}")
    templates = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns():
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("PRAGMA table_info(transactions);")).fetchall()
            cols = {r[1] for r in rows}

            needed = {
                "jdate": "TEXT",
                "iran_type": "TEXT",
                "bank_name": "TEXT",
                "destination_bank": "TEXT",
                "iran_amount": "REAL",
                "deposit_fee": "REAL",
                "tax": "REAL",
                "transfer_type": "TEXT",
                "depositor_name": "TEXT",
                "description": "TEXT",
            }

            for col, col_type in needed.items():
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {col} {col_type};"))

            conn.commit()
    except Exception as e:
        print(f"Error in _ensure_columns: {e}")


_ensure_columns()


@app.get("/de", response_class=HTMLResponse)
def germany_accounting(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/de/report", response_class=HTMLResponse)
def germany_report(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("report.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/iran", response_class=HTMLResponse)
def iran_menu(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("iran_index.html", {"request": request})


@app.get("/iran/report", response_class=HTMLResponse)
def iran_report_page(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("iran_report.html", {"request": request})


@app.get("/iran/input", response_class=HTMLResponse)
def iran_input_page(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("iran_input.html", {"request": request})


@app.get("/iran/output", response_class=HTMLResponse)
def iran_output_page(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Error: Templates not loaded</h1>")
    return templates.TemplateResponse("iran_output.html", {"request": request})


@app.get("/transactions", response_model=List[schemas.TransactionOut])
def api_list_all_transactions(db: Session = Depends(get_db)):
    return db.query(models.Transaction).order_by(models.Transaction.id.desc()).all()


@app.get("/transactions/de", response_model=List[schemas.TransactionOut])
def api_list_germany_transactions(db: Session = Depends(get_db)):
    return crud.get_germany_transactions(db)


@app.get("/transactions/iran", response_model=List[schemas.TransactionOut])
def api_list_iran_transactions(db: Session = Depends(get_db)):
    return crud.get_iran_transactions(db)


@app.post("/transactions", response_model=schemas.TransactionOut)
def api_create_transaction(transaction: schemas.TransactionCreate, db: Session = Depends(get_db)):
    return crud.create_transaction(db=db, transaction=transaction)


@app.put("/transactions/{transaction_id}", response_model=schemas.TransactionOut)
def api_update_transaction(
    transaction_id: int,
    transaction: schemas.TransactionUpdate,
    db: Session = Depends(get_db),
):
    updated = crud.update_transaction(db=db, transaction_id=transaction_id, transaction=transaction)
    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return updated


@app.delete("/transactions/{transaction_id}")
def api_delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    ok = crud.delete_transaction(db=db, transaction_id=transaction_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"ok": True}


@app.get("/iran/monthly-withdrawals")
def iran_monthly_withdrawals(month: str, db: Session = Depends(get_db)):
    """
    فقط خروجی‌های واقعی ماه (بدون settle tax)
    """
    month_clean = (month or "").strip()
    items = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.type == "ایران",
            models.Transaction.iran_type == "خروجی",
            models.Transaction.jdate.like(f"{month_clean}%"),
            models.Transaction.bank_name != "سامان (مالیات)",
            or_(
                models.Transaction.transfer_type.is_(None),
                ~models.Transaction.transfer_type.like("SETTLE_TAX|%"),
            ),
        )
        .all()
    )

    totals: dict = {}
    for t in items:
        bank = (t.bank_name or "").strip()
        if not bank:
            continue

        amt = float(t.iran_amount or 0)
        dep_fee = float(t.deposit_fee or 0)
        tax = float(t.tax or 0)
        totals[bank] = totals.get(bank, 0.0) + amt + dep_fee + tax

    return {"month": month_clean, "totals": totals}


@app.get("/iran/balances")
def iran_balances(db: Session = Depends(get_db)):
    return crud.compute_iran_balances(db)


@app.get("/iran/pending-tax")
def iran_pending_tax(db: Session = Depends(get_db)):
    pending = crud.compute_iran_pending_tax(db)
    richest = crud._richest_bank(db)
    return {"pending_tax": pending, "suggested_bank": richest}


class SettleTaxPayload(BaseModel):
    from_bank: Optional[str] = ""
    jdate: str
    description: Optional[str] = ""


@app.post("/iran/settle-tax")
def iran_settle_tax(payload: SettleTaxPayload, db: Session = Depends(get_db)):
    res = crud.settle_iran_tax(
        db=db,
        from_bank=(payload.from_bank or "").strip(),
        jdate=(payload.jdate or "").strip(),
        description=(payload.description or "").strip(),
    )
    if res is None:
        return {
            "ok": False,
            "message": "برای این تاریخ مالیات قابل تسویه‌ای وجود ندارد یا تاریخ نامعتبر است.",
        }
    return {"ok": True, **res, "tax_bank": "سامان (مالیات)"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/favicon.ico")
def favicon():
    return Response(content="", media_type="image/x-icon")


@app.get("/reports")
def api_reports_index(db: Session = Depends(get_db)):
    return reports.get_reports_index(db)


@app.get("/reports/{region}/{month}")
def api_get_report(region: str, month: str, db: Session = Depends(get_db)):
    if region not in ("iran", "de"):
        raise HTTPException(status_code=400, detail="Invalid region")
    p = reports.report_path(region, month)  # type: ignore[arg-type]
    if not p.exists():
        # auto-generate on demand if month exists in DB
        try:
            reports.regenerate_month_pdf(db, region, month)  # type: ignore[arg-type]
        except Exception:
            pass
    if not p.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    # inline باعث می‌شود داخل مرورگر/iframe نمایش داده شود (نه دانلود اجباری)
    return FileResponse(
        str(p),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{p.name}"'},
    )


class ReportRenamePayload(BaseModel):
    display_name: str


@app.put("/reports/{region}/{month}")
def api_rename_report(region: str, month: str, payload: ReportRenamePayload):
    if region not in ("iran", "de"):
        raise HTTPException(status_code=400, detail="Invalid region")
    reports.set_display_name(region, month, payload.display_name)  # type: ignore[arg-type]
    return {"ok": True}


@app.post("/reports/{region}/{month}/regenerate")
def api_regenerate_report(region: str, month: str, db: Session = Depends(get_db)):
    if region not in ("iran", "de"):
        raise HTTPException(status_code=400, detail="Invalid region")
    p = reports.regenerate_month_pdf(db, region, month)  # type: ignore[arg-type]
    return {"ok": True, "path": str(p)}


@app.delete("/reports/{region}/{month}")
def api_delete_report(region: str, month: str):
    if region not in ("iran", "de"):
        raise HTTPException(status_code=400, detail="Invalid region")
    reports.delete_report(region, month)  # type: ignore[arg-type]
    return {"ok": True}