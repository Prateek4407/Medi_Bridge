"""MediBridge ML API (FastAPI).

Endpoints
  GET  /health                    liveness check (used by Render and by the website to "wake up" the service)
  POST /predict/response-delay    ambulance response delay  <- used by the website (frontend contract)
  POST /predict/delay             same model, older 14-field format (kept for compatibility)
  POST /predict/severity          emergency severity (needs data/emergency_severity/emergency_severity_model.joblib)
  POST /predict/beds              next-day bed availability (needs the bed model + dataset)

Settings (environment variables)
  ALLOWED_ORIGINS    websites allowed to call this API from a browser, comma separated.
                     Example: https://medibridge.vercel.app   (default "*" = any website)
  DELAY_MODEL_FILE   model file inside ./models   (default response_delay_model.joblib)
"""
import os
from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
import pandas as pd
import sklearn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
MODEL_FILE = os.getenv("DELAY_MODEL_FILE", "response_delay_model.joblib")
MODEL_VERSION = "random-forest-v1"
PEAK_HOURS = [7, 8, 9, 17, 18, 19, 20]          # same list the model was trained with (train_model.py)
TYPICAL_MAX_KM = 15                             # most training cases were shorter than this

app = FastAPI(title="MediBridge ML API", version="1.1.0")

# The website runs on another domain (Vercel), so the browser needs CORS permission.
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_origins, allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"],
)

# ---- delay model: loaded once when the service starts -----------------------------------------
_model_path = BASE_DIR / "models" / MODEL_FILE
if not _model_path.exists():
    raise RuntimeError(
        f"Delay model not found: {_model_path}. Commit models/{MODEL_FILE} to the repository "
        "(*.joblib must not be git-ignored) and redeploy."
    )
delay_model = joblib.load(_model_path)
_preprocess = delay_model[:-1]
_forest = delay_model.steps[-1][1]              # RandomForestRegressor (200 trees)

severity_model = None                           # loaded on first use


def _predict_delay(row: dict) -> dict:
    """row = the 12 user inputs. Adds the two engineered features exactly as in training and predicts."""
    row = dict(row)
    row["distance_per_ambulance"] = row["distance_km"] / max(row["available_ambulance_count"], 1)
    row["is_peak_hour"] = int(row["hour_of_day"] in PEAK_HOURS)
    frame = pd.DataFrame([row])[list(delay_model.feature_names_in_)]

    minutes = float(delay_model.predict(frame)[0])

    # Range = how much the model's 200 trees disagree (10th to 90th percentile).
    transformed = _preprocess.transform(frame)
    per_tree = np.array([float(t.predict(transformed)[0]) for t in _forest.estimators_])
    low, high = (float(x) for x in np.percentile(per_tree, [10, 90]))
    low, high = min(low, minutes), max(high, minutes)

    warnings = []
    if row["distance_km"] > TYPICAL_MAX_KM:
        warnings.append(f"The distance is above {TYPICAL_MAX_KM} km. Most past cases were shorter, so this estimate is less reliable.")
    if row["available_ambulance_count"] == 0:
        warnings.append("No ambulance is available nearby. Delays in this situation can be much longer than the model usually sees.")
    if row["availability_age_min"] > 120:
        warnings.append("The availability information is more than 2 hours old.")

    return {
        "minutes": round(max(minutes, 0.0), 1),
        "low": round(max(low, 0.0), 1),
        "high": round(max(high, 0.0), 1),
        "warnings": warnings,
    }


# ---- request formats --------------------------------------------------------------------------
Locality = Literal["Airport Road", "Central Market", "Green Park", "Harbor View", "Hillcrest", "Industrial Zone",
                   "Lakeview", "Maple Heights", "Northgate", "Old Town", "Riverside", "Southfield",
                   "Sunrise Colony", "University Area"]


class ResponseDelayRequest(BaseModel):
    """The 12 inputs of the website form (frontend/src/lib/predictionConfig.js)."""
    locality: Locality
    distance_km: float = Field(ge=0, le=200)
    partner_type: Literal["ambulance", "hospital"]
    partner_status: Literal["available", "limited", "busy"]
    availability_age_min: float = Field(ge=0, le=1440)
    hour_of_day: int = Field(ge=0, le=23)
    day_of_week: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    traffic_level: Literal["low", "medium", "high"]
    weather_condition: Literal["clear", "cloudy", "rainy", "foggy"]
    hospital_capacity_status: Literal["available", "limited", "unavailable", "not_applicable"]
    available_ambulance_count: int = Field(ge=0, le=99)
    dispatch_mode: Literal["automated", "manual"]


class DelayRequest(BaseModel):
    """Older format: the 14 model columns (the two engineered ones are optional and computed when missing)."""
    distance_km: float
    availability_age_min: float
    hour_of_day: int
    available_ambulance_count: int
    distance_per_ambulance: float | None = None
    is_peak_hour: int | None = None
    locality: str
    partner_status: str
    partner_type: str
    traffic_level: str
    weather_condition: str
    dispatch_mode: str
    hospital_capacity_status: str
    day_of_week: str


# ---- endpoints --------------------------------------------------------------------------------
@app.get("/")
def home():
    return {
        "message": "MediBridge ML API is running",
        "endpoints": ["/health", "/predict/response-delay", "/predict/delay", "/predict/severity", "/predict/beds", "/docs"],
    }


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_FILE, "model_version": MODEL_VERSION, "scikit_learn": sklearn.__version__}


@app.post("/predict/response-delay")
def predict_response_delay(request: ResponseDelayRequest):
    r = _predict_delay(request.model_dump())
    return {
        "predicted_delay_minutes": r["minutes"],
        "range": {"low": r["low"], "high": r["high"]},
        "range_note": "The range shows how much the model's 200 decision trees disagree (10th to 90th percentile). It is not a guaranteed ETA.",
        "warnings": r["warnings"],
        "model_version": MODEL_VERSION,
        "message": "Estimated delay only; not a live ambulance ETA.",
    }


@app.post("/predict/delay")
def predict_delay(request: DelayRequest):
    data = request.model_dump()
    r = _predict_delay({k: v for k, v in data.items() if k not in ("distance_per_ambulance", "is_peak_hour")})
    return {
        "predicted_response_delay_min": r["minutes"],      # original key, kept for older clients
        "predicted_delay_minutes": r["minutes"],
        "message": "Estimated delay only; not a live ambulance ETA.",
    }


@app.post("/predict/severity")
def predict_severity(data: dict[str, Any]):
    global severity_model
    try:
        model_path = BASE_DIR / "data" / "emergency_severity" / "emergency_severity_model.joblib"
        if severity_model is None:
            if not model_path.exists():
                raise HTTPException(status_code=503, detail="Severity model file is not available.")
            severity_model = joblib.load(model_path)

        expected_columns = list(severity_model.feature_names_in_)
        missing_columns = [col for col in expected_columns if col not in data]
        if missing_columns:
            raise HTTPException(status_code=422, detail={"message": "Some required input fields are missing.", "missing_columns": missing_columns})

        input_data = pd.DataFrame([{col: data[col] for col in expected_columns}])
        prediction = severity_model.predict(input_data)[0]
        return {"predicted_acuity": int(prediction), "message": "Academic prototype prediction only. Not for real medical triage."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(exc)}")


@app.post("/predict/beds")
def predict_beds():
    try:
        from hospital_bed_prediction.predict import predict_bed_availability
        return predict_bed_availability()
    except (FileNotFoundError, ImportError) as exc:          # model / dataset / module not deployed: "not available", not a crash
        raise HTTPException(status_code=503, detail=f"Bed prediction is not available: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Bed prediction failed: {str(exc)}")
