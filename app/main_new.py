from fastapi import FastAPI, Response

app = FastAPI(title="Sepid Exchange Account")

@app.get("/")
def root():
    return {"message": "Sepid Exchange WebApp minimal running"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/favicon.ico")
def favicon():
    return Response(content="", media_type="image/x-icon")
