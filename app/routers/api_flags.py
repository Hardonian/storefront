"""Feature flag and runtime experimentation control plane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import flags as flag_engine
from app.core.config import require_operator
from app.services.analytics_service import record_event

router = APIRouter(prefix="/api/flags", tags=["Feature Flags"])


class FlagUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    value: bool | float | str


class ExperimentControl(BaseModel):
    action: str = Field(..., pattern="^(start|stop)$")
    flag: str = ""
    force_winner: str | None = None


@router.get("", response_class=JSONResponse)
async def get_flags(_: None = Depends(require_operator)):
    """Retrieve active flags, schema, and running experiments."""
    flags = flag_engine.load_flags()
    exp = flag_engine._active_experiment(flag_engine.DEFAULT_FLAG_PATH)
    return {
        "flags": flags,
        "schema": dict(flag_engine.FLAG_SCHEMA),
        "active_experiment": exp,
        "flag_file": str(flag_engine.DEFAULT_FLAG_PATH),
    }


@router.post("", response_class=JSONResponse)
async def set_flag(payload: FlagUpdate, _: None = Depends(require_operator)):
    """Mutate a feature flag value with strict schema type validation."""
    spec = flag_engine.FLAG_SCHEMA.get(payload.name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown flag: {payload.name}")

    # Type validate
    if spec.get("type") == "bool" and not isinstance(payload.value, bool):
        raise HTTPException(status_code=422, detail=f"{payload.name} requires a bool")
    if spec.get("type") == "float":
        try:
            fval = float(payload.value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{payload.name} requires a float") from None
        if not 0.0 <= fval <= 1.0:
            raise HTTPException(status_code=422, detail="sampling must be 0..1")
        payload.value = fval
    if spec.get("type") == "ab" and str(payload.value) not in spec.get("variants", []):
        raise HTTPException(status_code=422, detail=f"{payload.name} must be one of {spec.get('variants')}")

    if spec.get("type") == "ab":
        exp = flag_engine._active_experiment(flag_engine.DEFAULT_FLAG_PATH)
        if exp and exp.get("flag") == payload.name:
            raise HTTPException(status_code=409, detail="Stop the experiment first (POST /api/flags/experiment stop)")

    ok = flag_engine.set_flag(payload.name, payload.value)
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown flag")

    record_event(
        f"flag_set:{payload.name}={payload.value}",
        page="/api/flags",
    )
    return {"ok": True, "flag": payload.name, "value": payload.value}


@router.post("/experiment", response_class=JSONResponse)
async def control_experiment(payload: ExperimentControl, _: None = Depends(require_operator)):
    """Start or stop an A/B experiment."""
    if payload.action == "stop":
        flag_engine.stop_experiment()
        record_event("flag_experiment:stop", page="/api/flags/experiment")
        return {"ok": True, "experiment": None}

    if not payload.flag:
        raise HTTPException(status_code=422, detail="flag required to start")

    try:
        exp = flag_engine.start_experiment(payload.flag, payload.force_winner)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    record_event(
        f"flag_experiment:start:{payload.flag}" + (f":winner={payload.force_winner}" if payload.force_winner else ""),
        page="/api/flags/experiment",
    )
    return {"ok": True, "experiment": exp}
