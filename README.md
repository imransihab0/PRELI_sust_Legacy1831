# QueueStorm Investigator

AI copilot for digital finance support operations — SUST CSE Carnival 2026 · Codex Community Hackathon.

Exposes `POST /analyze-ticket` and `GET /health`.

## Tech Stack

- **Runtime**: Python 3.11
- **Framework**: FastAPI + Uvicorn
- **AI**: Groq (llama-3.3-70b-versatile) via tool use for structured JSON output
- **Validation**: Pydantic v2
- **Deployment**: Docker / Uvicorn directly

## MODELS

| Model | Provider | Where it runs | Why chosen |
|---|---|---|---|
| `llama-3.3-70b-versatile` | Groq (Meta Llama 3.3) | Groq Cloud API | Free tier available. Extremely fast inference (~1–3s median) — targets the ≤5s full-credit p95 latency tier. Strong multilingual capability for Bangla/Banglish input. Tool use enforces exact JSON schema output. No GPU required. |

No local models are used. The service makes one outbound HTTPS call to `api.groq.com` per ticket.

Model is configurable via `GROQ_MODEL` env var. Fallback option: `llama-3.1-70b-versatile` or `mixtral-8x7b-32768`.

## Setup

### Prerequisites

- Python 3.11+
- An Anthropic API key (`ANTHROPIC_API_KEY`)

### Local run

```bash
git clone <repo-url>
cd PRELI_sust_Legacy1831

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set GROQ_API_KEY=gsk_...

python main.py
# Service starts on http://localhost:8000
```

### Docker run

```bash
docker build -t queuestorm-investigator .
docker run -p 8000:8000 -e GROQ_API_KEY=gsk_... queuestorm-investigator
```

## Endpoints

### GET /health
```
curl http://localhost:8000/health
# {"status":"ok"}
```

### POST /analyze-ticket
```bash
curl -X POST http://localhost:8000/analyze-ticket \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "TKT-001",
    "complaint": "I sent 5000 taka to a wrong number around 2pm today.",
    "language": "en",
    "channel": "in_app_chat",
    "user_type": "customer",
    "transaction_history": [
      {
        "transaction_id": "TXN-9101",
        "timestamp": "2026-04-14T14:08:22Z",
        "type": "transfer",
        "amount": 5000,
        "counterparty": "+8801719876543",
        "status": "completed"
      }
    ]
  }'
```

## AI Approach

The service uses Claude Haiku with **tool use (function calling)** to produce structured JSON output that exactly matches the required response schema. Tool use enforces enum constraints at the model output level, eliminating schema mismatches.

### Investigation Flow

1. **Request ingestion** — Pydantic validates the incoming ticket.
2. **Prompt injection check** — Complaint text is scanned for injection patterns before being sent to the LLM. Flagged tickets get `human_review_required: true` appended.
3. **LLM call** — A structured system prompt instructs Claude to:
   - Cross-reference complaint text against `transaction_history`
   - Identify the matching transaction (`relevant_transaction_id`)
   - Determine `evidence_verdict` (consistent / inconsistent / insufficient_data)
   - Classify `case_type`, route to correct `department`, set `severity`
   - Write a safe `customer_reply` in the same language as the complaint
4. **Safety post-processing** — A regex layer scans `customer_reply` for:
   - Credential requests (PIN, OTP, password) → stripped and replaced
   - Unauthorized refund promises → replaced with safe official language
   - Missing PIN/OTP reminder → appended automatically
5. **Response** — Pydantic validates the final output before it is returned.

## Safety Logic

All safety rules are enforced at two layers:

**Layer 1 — System prompt (primary)**
- Absolute prohibition on asking for PIN, OTP, password, card number
- Absolute prohibition on confirming refunds or reversals without authority
- Safe language template: "any eligible amount will be returned through official channels"
- Language-matching instruction (Bangla reply for Bangla complaints)

**Layer 2 — Post-processing (safety net)**
- Regex scan of `customer_reply` for prohibited patterns
- Automatic correction if LLM output contains violations
- PIN/OTP reminder injected if missing

## Cost Reasoning

Groq's free tier provides generous daily limits at zero cost. `llama-3.3-70b-versatile` is available on the free tier with no per-token charge, making evaluation-time costs $0. If rate limits are hit, switching to `llama-3.1-8b-instant` reduces latency further.

## Assumptions

- All complaint text is UTF-8 encoded. Bangla Unicode (U+0980–U+09FF) is handled natively by the model.
- `transaction_history` entries with ambiguous timestamps are compared by proximity to times mentioned in the complaint.
- The service is an internal copilot. It does not initiate any financial action.

## Known Limitations

- For ambiguous multi-transaction cases, the model may pick the wrong transaction if the complaint provides no distinguishing details (amount, time, counterparty). The service returns `insufficient_data` in such cases.
- Banglish (romanised Bangla) is handled but accuracy may be slightly lower than pure Bangla or English.
- The service depends on Anthropic API availability. If the API is down, requests will return HTTP 500.
- No caching or retry logic is implemented in this preliminary version.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | Groq API key (free at groq.com) |
| `GROQ_MODEL` | No (default `llama-3.3-70b-versatile`) | Groq model to use |
| `PORT` | No (default 8000) | Port for the HTTP server |
