import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from models import TicketRequest, TicketResponse
from llm import analyze_ticket


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QueueStorm Investigator starting up")
    yield
    logger.info("QueueStorm Investigator shutting down")


app = FastAPI(
    title="QueueStorm Investigator",
    description="AI copilot for digital finance support operations",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/analyze-ticket", response_model=TicketResponse)
async def analyze(request: TicketRequest):
    try:
        result = await analyze_ticket(request)
        return result
    except ValidationError as e:
        logger.error("Response validation error: %s", str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal response validation error"},
        )
    except Exception as e:
        logger.error("Unexpected error: %s", type(e).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid request", "details": str(exc)},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
