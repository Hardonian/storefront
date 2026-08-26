"""Feature flag management, dynamic experiments, and runtime configuration."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import flags as flag_engine
from app.core.config import require_operator
from app.services.analytics_service import record_event

router = APIRouter(prefix="/api/flags", tags=["Feature Flags"])
logger = logging.getLogger("storefront.flags")


class FlagUpdateRequest(BaseModel):
    name: str = Field(..., max_length=64)
    value: Any


class ExperimentControlRequest(BaseModel):
    action: str = Field(..., max_length=32)
    flag: str | None = Field(default=None, max_length=64)
    force_winner: str | None = Field(default=None, max_length=8)


@router.get("")
async def get_flags(_: None = Depends(require_operator)):
    """Retrieve full feature flag schema, current values, and active experiment."""
    current_flags = flag_engine.load_flags()
    exp = flag_engine.get_active_experiment()
    return {
        "flags": current_flags,
        "schema": flag_engine.SCHEMA,
        "active_experiment": exp,
    }


@router.post("")
async def update_flag(payload: FlagUpdateRequest, _: None = Depends(require_operator)):
    """Update a feature flag with strict schema validation."""
    if payload.name not in flag_engine.SCHEMA:
        raise HTTPException(status_code=404, detail=f"Unknown flag: {payload.name}")

    schema = flag_engine.SCHEMA[payload.name]
    expected_type = schema["type"]

    # Validate value type
    if expected_type == "bool" and not isinstance(payload.value, bool):
        raise HTTPException(status_code=422, detail=f"Flag {payload.name} must be boolean")
    elif expected_type == "float":
        if not isinstance(payload.value, (int, float)) or isinstance(payload.value, bool):
            raise HTTPException(status_code=422, detail=f"Flag {payload.name} must be numeric")
        val = float(payload.value)
        if "min" in schema and val < schema["min"]:
            raise HTTPException(status_code=422, detail=f"Value {val} below minimum {schema['min']}")
        if "max" in schema and val > schema["max"]:
            raise HTTPException(status_code=422, detail=f"Value {val} above maximum {schema['max']}")
    elif expected_type == "choice":
        if payload.value not in schema.get("choices", []):
            raise HTTPException(status_code=422, detail=f"Invalid choice: {payload.value}")

    # Check for active experiment conflict
    exp = flag_engine.get_active_experiment()
    if exp and exp.get("flag") == payload.name:
        raise HTTPException(status_code=409, detail=f"Cannot pin flag {payload.name} while an experiment is active")

    flag_engine.set_flag(payload.name, payload.value)
    record_event(f"flag_set:{payload.name}", value=str(payload.value))

    return {"name": payload.name, "value": payload.value, "status": "ok"}


@router.post("/experiment")
async def control_experiment(payload: ExperimentControlRequest, _: None = Depends(require_operator)):
    """Start or stop A/B experiments."""
    if payload.action not in ("start", "stop"):
        raise HTTPException(status_code=422, detail=f"Unknown action: {payload.action}")

    if payload.action == "start":
        if not payload.flag or payload.flag not in flag_engine.SCHEMA:
            raise HTTPException(status_code=422, detail=f"Invalid experiment flag: {payload.flag}")
        exp = flag_engine.start_experiment(payload.flag, force_winner=payload.force_winner)
        record_event(f"experiment_start:{payload.flag}", force_winner=payload.force_winner)
        return {"status": "ok", "experiment": exp}
    else:
        stopped = flag_engine.stop_experiment()
        record_event("experiment_stop")
        return {"status": "ok", "stopped": stopped}
