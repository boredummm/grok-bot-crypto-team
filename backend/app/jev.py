"""TypeSafe System One (JEV) typed judgement client.

Implements the official `POST https://api.typesafe.ai/v1/systemone` contract:
- Question types: `noul` (0..1 probability), `choice` (probabilities + confidence),
  and `score` (rubric weighted rating + confidence).
- One request per question set (`market`, `solana`, `social`, `pick`), never per question.
- Never retries HTTP 422 (malformed question).
- Without `TYPESAFE_API_KEY`, returns `None` and reports `JEV NOT CONNECTED` honestly.
- JEV never authorizes trades or overrides `RiskEngine`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.http import ProviderClient, ProviderError, ProviderSnapshot

log = logging.getLogger(__name__)

INPUT_COST_PER_MTOK = 0.042


class NoulAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["noul"]
    noul: float = Field(ge=0.0, le=1.0)


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["choice"]
    choice: str
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float]

    @model_validator(mode="after")
    def _check_probs(self) -> ChoiceAnswer:
        if not self.probabilities or self.choice not in self.probabilities:
            raise ValueError("choice answer missing selected choice in probabilities")
        total = sum(self.probabilities.values())
        if not (0.90 <= total <= 1.10):
            raise ValueError(f"choice probabilities must sum near 1.0, got {total:.4f}")
        return self


class ScoreAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["score"]
    score: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    legend: dict[str, Any] = Field(default_factory=dict)
    probabilities: dict[str, float] = Field(default_factory=dict)


class JevUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


@dataclass
class JevCallRecord:
    question_set: str
    requested_model: str
    resolved_model: str
    request_id: str | None
    answers: dict[str, dict[str, Any]]
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    observed_at: str


MARKET_QUESTIONS: dict[str, dict[str, Any]] = {
    "shape": {
        "type": "choice",
        "instructions": "Classify the shape of this launch from the numeric fields in `state`.",
        "criteria": {
            "crowd": "Holders present and growing with volume; buys outnumber sells across recent windows; volume is distributed rather than a single isolated spike.",
            "one_buyer": "Price climbing without broad participation or buy count concentrated in thin trades.",
            "fading": "Recent volume is a small fraction of trailing pace, or sells outnumber buys.",
            "too_early": "Too few observations or missing fields to distinguish the launch shape.",
        },
    },
    "liquidity_fits_ticket": {
        "type": "noul",
        "instructions": "A paper position of `intended_ticket_usd` could exit into `liquidity_usd` without moving pool price more than a few percent.",
    },
    "momentum_already_spent": {
        "type": "noul",
        "instructions": "The price move in `change` has already happened in older windows rather than building freshly now.",
        "criteria": {
            "true": "The move is exhausted or concentrated in older windows.",
            "false": "Recent windows carry confirmed, unexhausted activity.",
        },
    },
}

SOLANA_CHAIN_QUESTIONS: dict[str, dict[str, Any]] = {
    "authority_risk": {
        "type": "choice",
        "instructions": "Judge contract control risk on Solana from `mint_authority_active` and `freeze_authority_active`.",
        "criteria": {
            "renounced": "Both false. Supply cannot be inflated and token accounts cannot be frozen.",
            "mint_open": "mint_authority_active is true while freeze_authority_active is false.",
            "freeze_open": "freeze_authority_active is true while mint_authority_active is false.",
            "both_open": "Both authorities are active or unknown.",
        },
    },
    "concentration_is_exit_risk": {
        "type": "noul",
        "instructions": "Holding this token carries severe exit-liquidity concentration risk from `top_10_percent`, `top_wallet_percent`, and `holder_count`.",
        "criteria": {
            "true": "A few wallets can collapse the pool by selling.",
            "false": "Supply is distributed widely enough for small paper size.",
        },
    },
    "dev_still_loaded": {
        "type": "noul",
        "instructions": "`developer_holding_percentage` is large enough that creator selling would materially impact the pool.",
    },
}

SOCIAL_QUESTIONS: dict[str, dict[str, Any]] = {
    "account_is_the_project": {
        "type": "noul",
        "instructions": "The account in `x_account` is the token's official project account matching the verified on-chain handle.",
        "criteria": {
            "true": "Handle matches on-chain metadata and content is specifically about this token.",
            "false": "Unrelated subject, impersonator, or missing link back.",
        },
    },
    "audience_is_real": {
        "type": "noul",
        "instructions": "Engagement in `x_account` is consistent with organic readers rather than inflated follower counts with dead replies.",
    },
    "recycled_account": {
        "type": "noul",
        "instructions": "`x_account` shows signs of being recycled or renamed from an older unrelated project.",
    },
    "effort": {
        "type": "score",
        "instructions": "Rate visible project substance from `x_account`.",
        "criteria": [
            "Single post or empty shell.",
            "A few promotional posts only.",
            "Consistent updates with clear context.",
            "Visible builders shipping verifiable artifacts.",
        ],
    },
}


def build_pick_questions(candidates: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Build dynamic `pick` choice + `worth_trading_at_all` noul over 2..255 survivors."""
    if len(candidates) < 2:
        raise ValueError("pick question set requires at least 2 survivors")
    criteria = {row["symbol"]: row["summary"] for row in candidates[:255]}
    return {
        "best": {
            "type": "choice",
            "instructions": (
                "Choose the single candidate in `candidates` with the cleanest setup right now. "
                "Weigh crowd shape, Solana authority safety, holder distribution, and liquidity."
            ),
            "criteria": criteria,
        },
        "worth_trading_at_all": {
            "type": "noul",
            "instructions": "At least one candidate in `candidates` merits a paper position right now rather than all of them being mediocre.",
            "criteria": {
                "true": "At least one candidate has a clean, well-supported setup.",
                "false": "Every candidate has a disqualifying flaw or weak evidence.",
            },
        },
    }


def parse_typed_answer(raw: dict[str, Any], expected_options: set[str] | None = None) -> dict[str, Any]:
    kind = raw.get("type")
    if kind == "noul":
        return NoulAnswer.model_validate(raw).model_dump()
    if kind == "choice":
        ans = ChoiceAnswer.model_validate(raw)
        if expected_options and ans.choice not in expected_options:
            raise ValueError(f"choice {ans.choice!r} not in requested criteria {sorted(expected_options)}")
        return ans.model_dump()
    if kind == "score":
        return ScoreAnswer.model_validate(raw).model_dump()
    raise ValueError(f"unsupported JEV answer type: {kind!r}")


class JevClient:
    """Server-side TypeSafe System One caller. Bots and UI never see `TYPESAFE_API_KEY`."""

    def __init__(
        self,
        client: ProviderClient,
        api_key: str,
        model: str = "jev-latest",
        base_url: str = "https://api.typesafe.ai",
    ) -> None:
        self.client = client
        self.api_key = (api_key or "").strip()
        self.model = model or "jev-latest"
        self.base_url = base_url.rstrip("/")
        self.client.name = "jev"
        self.client.configured = bool(self.api_key)
        self.last_resolved_model: str | None = None
        self.last_request_id: str | None = None
        self.halted_422_reason: str | None = None
        self.total_input_tokens: int = 0
        self.total_calls: int = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.halted_422_reason is None

    def snapshot(self) -> ProviderSnapshot:
        snap = self.client.snapshot()
        if not self.api_key:
            snap.status = "not_connected"
            snap.detail = "JEV NOT CONNECTED (TYPESAFE_API_KEY not set). Deterministic gates active."
        elif self.halted_422_reason:
            snap.status = "halted_422"
            snap.detail = f"Halted on malformed question (422): {self.halted_422_reason}"
        elif self.last_resolved_model:
            snap.detail = f"Connected · model {self.last_resolved_model} · {self.total_calls} calls"
        else:
            snap.detail = f"Configured ({self.model}) · awaiting cycle"
        return snap

    def _questions_for(self, question_set: str, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if question_set == "market":
            return MARKET_QUESTIONS
        if question_set == "solana":
            return SOLANA_CHAIN_QUESTIONS
        if question_set == "social":
            return SOCIAL_QUESTIONS
        if question_set == "pick":
            return build_pick_questions(state.get("candidates") or [])
        raise ValueError(f"unknown JEV question set {question_set!r}")

    async def ask(self, question_set: str, state: dict[str, Any]) -> JevCallRecord | None:
        if not self.api_key or self.halted_422_reason is not None:
            return None
        if not self.client.circuit.allow():
            return None
        questions = self._questions_for(question_set, state)
        await self.client.limiter.acquire()
        url = f"{self.base_url}/v1/systemone"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "state": state,
            "questions": questions,
        }

        last_err: str | None = None
        for attempt in range(3):
            started = time.perf_counter()
            try:
                res = await self.client._http().post(url, headers=headers, json=body)
                self.client.last_latency_ms = int((time.perf_counter() - started) * 1000)
            except Exception as exc:
                last_err = str(exc)
                await self.client._sleep(0.1 * (2**attempt))
                continue

            if res.status_code == 422:
                # Guide invariant: NEVER retry a 422; the question schema is malformed.
                detail = res.text[:240]
                self.halted_422_reason = f"{question_set}: {detail}"
                self.client.last_error = self.halted_422_reason
                log.error("jev 422 malformed question; halting jev calls", extra={"question_set": question_set})
                raise ProviderError("jev", self.halted_422_reason)

            if res.status_code == 401:
                self.client.last_error = "401 invalid TYPESAFE_API_KEY"
                self.client.circuit.failure()
                raise ProviderError("jev", self.client.last_error)

            if res.status_code in {429, 529} or res.status_code >= 500:
                last_err = f"HTTP {res.status_code}"
                if res.status_code >= 500 and res.status_code != 529:
                    self.client.circuit.failure()
                await self.client._sleep(0.25 * (2**attempt))
                continue

            payload = res.json()
            if not isinstance(payload, dict):
                raise ProviderError("jev", "systemone response was not a JSON object")

            resolved_model = str(payload.get("model") or self.model)
            usage = JevUsage.model_validate(payload.get("usage") or {"input_tokens": 0, "output_tokens": 0})
            raw_answers = payload.get("answers")
            if not isinstance(raw_answers, dict):
                raise ProviderError("jev", "systemone response missing answers map")

            validated_answers: dict[str, dict[str, Any]] = {}
            for q_name, q_spec in questions.items():
                ans_raw = raw_answers.get(q_name)
                if not isinstance(ans_raw, dict):
                    raise ProviderError("jev", f"missing answer for {q_name}")
                opts = set((q_spec.get("criteria") or {}).keys()) if q_spec.get("type") == "choice" else None
                validated_answers[q_name] = parse_typed_answer(ans_raw, expected_options=opts)

            req_id = res.headers.get("x-request-id") or res.headers.get("request-id")
            self.last_resolved_model = resolved_model
            self.last_request_id = req_id
            self.total_input_tokens += usage.input_tokens
            self.total_calls += 1
            self.client.circuit.success()
            self.client.last_error = None
            cost = round((usage.input_tokens / 1_000_000.0) * INPUT_COST_PER_MTOK, 8)
            return JevCallRecord(
                question_set=question_set,
                requested_model=self.model,
                resolved_model=resolved_model,
                request_id=req_id,
                answers=validated_answers,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                estimated_cost_usd=cost,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )

        self.client.circuit.failure()
        self.client.last_error = (last_err or "jev request failed")[:240]
        raise ProviderError("jev", self.client.last_error)
