import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.errors import AppError, app_error_handler

app = FastAPI(
    title="五灵集卡 API",
    version="1.0.0",
    description="Take-Home 演示 API；X-Demo-User-Id 仅用于本地演示，不是生产鉴权。",
)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-Id", f"req_{uuid.uuid4().hex[:16]}")
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/g/{token}", include_in_schema=False)
def gift_page(token: str):
    return FileResponse(static_dir / "index.html")


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}

