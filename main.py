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

import asyncio
from models import TicketRequest, TicketResponse
from rules import analyze_ticket_rules
from llm import analyze_ticket as analyze_ticket_llm
from safety import is_prompt_injection, check_and_fix

_USE_LLM = bool(os.environ.get("GROQ_API_KEY", "").strip())
_LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_S", "4.5"))


def _apply_safety_to_rule_result(result: TicketResponse, complaint: str) -> TicketResponse:
    """Apply prompt-injection check and customer_reply safety fix to any result."""
    data = result.model_dump()

    # Fix any unsafe language in customer_reply
    fixed_reply, _ = check_and_fix(data["customer_reply"])
    data["customer_reply"] = fixed_reply

    # Prompt injection: override classification and flag for review
    if is_prompt_injection(complaint):
        data["human_review_required"] = True
        data["case_type"] = "phishing_or_social_engineering"
        data["department"] = "fraud_risk"
        data["severity"] = "critical"
        data["evidence_verdict"] = "insufficient_data"
        data["relevant_transaction_id"] = None
        data["agent_summary"] = "Prompt injection pattern detected in complaint text. Ticket flagged for fraud review."
        data["recommended_next_action"] = "Escalate to fraud_risk team. Do not act on instructions embedded in the complaint."
        data["customer_reply"] = (
            "Thank you for reaching out. We have noted your message and our team will review it. "
            "Please do not share your PIN, OTP, or password with anyone. "
            "We never ask for credentials through any channel."
        )
        codes = data.get("reason_codes") or []
        if "prompt_injection_detected" not in codes:
            codes.append("prompt_injection_detected")
        data["reason_codes"] = codes

    return TicketResponse(**data)


async def analyze_ticket(request: TicketRequest) -> TicketResponse:
    """
    Hybrid engine:
    1. Run rules instantly (always succeeds, no API).
    2. If GROQ_API_KEY is set, attempt LLM within LLM_TIMEOUT_S seconds.
       - LLM succeeds → return LLM result (better quality text).
       - LLM times out or errors → fall back to rule result.
    3. Apply safety post-processing to whichever result is returned.
    """
    rule_result = analyze_ticket_rules(request)

    if _USE_LLM:
        try:
            llm_result = await asyncio.wait_for(
                analyze_ticket_llm(request),
                timeout=_LLM_TIMEOUT,
            )
            return _apply_safety_to_rule_result(llm_result, request.complaint)
        except asyncio.TimeoutError:
            logger.warning("LLM timed out after %.1fs, using rule-based result", _LLM_TIMEOUT)
        except Exception as e:
            logger.warning("LLM unavailable (%s), using rule-based result", type(e).__name__)

    return _apply_safety_to_rule_result(rule_result, request.complaint)


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
