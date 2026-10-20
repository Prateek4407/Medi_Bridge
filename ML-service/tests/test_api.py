"""Run:  pip install -r requirements.txt pytest   then   python -m pytest -q"""
import joblib, pandas as pd
from pathlib import Path
from fastapi.testclient import TestClient
import main

client = TestClient(main.app)
ROOT = Path(__file__).resolve().parent.parent

# exactly what the website sends (frontend/src/lib/predictionConfig.js buildPayload)
SITE = {
    "locality": "Central Market", "distance_km": 5, "partner_type": "ambulance", "partner_status": "available",
    "availability_age_min": 10, "hour_of_day": 14, "day_of_week": "monday", "traffic_level": "medium",
    "weather_condition": "clear", "hospital_capacity_status": "available", "available_ambulance_count": 3, "dispatch_mode": "automated",
}


def test_health():
    r = client.get("/health"); assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["scikit_learn"] == "1.9.1"


def test_website_contract():
    r = client.post("/predict/response-delay", json=SITE); assert r.status_code == 200
    j = r.json()
    assert isinstance(j["predicted_delay_minutes"], float) and j["predicted_delay_minutes"] > 0
    assert 0 <= j["range"]["low"] <= j["predicted_delay_minutes"] <= j["range"]["high"]
    assert j["model_version"] == "random-forest-v1" and isinstance(j["warnings"], list) and j["range_note"]


def test_matches_the_trained_model_directly():
    """The service must add the two engineered features exactly like training did."""
    model = joblib.load(ROOT / "models" / "response_delay_model.joblib")
    row = dict(SITE, distance_per_ambulance=5 / 3, is_peak_hour=0)
    expected = round(float(model.predict(pd.DataFrame([row]))[0]), 1)
    assert client.post("/predict/response-delay", json=SITE).json()["predicted_delay_minutes"] == expected


def test_peak_hour_is_computed():
    off = client.post("/predict/response-delay", json=dict(SITE, hour_of_day=14)).json()["predicted_delay_minutes"]
    peak = client.post("/predict/response-delay", json=dict(SITE, hour_of_day=8)).json()["predicted_delay_minutes"]
    assert off != peak          # hour 8 is a peak hour, so the engineered feature changes the answer


def test_warnings():
    far = client.post("/predict/response-delay", json=dict(SITE, distance_km=40)).json()
    assert any("15 km" in w for w in far["warnings"])
    none = client.post("/predict/response-delay", json=dict(SITE, available_ambulance_count=0, availability_age_min=300)).json()
    assert len(none["warnings"]) == 2


def test_validation_errors_are_in_the_format_the_website_reads():
    bad = dict(SITE, locality="Mars", distance_km=-1, hour_of_day=30)
    r = client.post("/predict/response-delay", json=bad); assert r.status_code == 422
    fields = {d["loc"][-1] for d in r.json()["detail"]}      # the website highlights these fields
    assert {"locality", "distance_km", "hour_of_day"} <= fields
    assert client.post("/predict/response-delay", json={}).status_code == 422


def test_every_form_option_is_accepted():
    """All dropdown values offered by the website must be known to the model."""
    for key, values in {"locality": list(main.Locality.__args__), "traffic_level": ["low", "medium", "high"],
                        "weather_condition": ["clear", "cloudy", "rainy", "foggy"], "partner_status": ["available", "limited", "busy"],
                        "hospital_capacity_status": ["available", "limited", "unavailable", "not_applicable"],
                        "day_of_week": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]}.items():
        for v in values:
            assert client.post("/predict/response-delay", json=dict(SITE, **{key: v})).status_code == 200, (key, v)


def test_legacy_endpoint_still_works():
    legacy = dict(SITE, distance_per_ambulance=5 / 3, is_peak_hour=0)
    j = client.post("/predict/delay", json=legacy).json()
    assert j["predicted_response_delay_min"] == j["predicted_delay_minutes"] > 0
    assert client.post("/predict/delay", json=SITE).status_code == 200      # engineered fields optional


def test_cors_for_the_website():
    r = client.options("/predict/response-delay", headers={"Origin": "https://medibridge.vercel.app", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "https://medibridge.vercel.app")


def test_severity_model_uses_archived_training_schema():
    training = pd.read_json(ROOT / "tests" / "fixtures" / "severity_sample.json", typ="series").to_frame().T
    sample = training.drop(columns=["patient_id", "triage_nurse_id", "disposition", "ed_los_hours", "triage_acuity"]).iloc[0].to_dict()
    r = client.post("/predict/severity", json=sample)
    assert r.status_code == 200
    assert 1 <= r.json()["predicted_acuity"] <= 5


def test_hospital_bed_model_predicts_each_department():
    r = client.post("/predict/beds")
    assert r.status_code == 200
    payload = r.json()
    assert payload["predictions"]
    assert all({
        "department",
        "last_record_date",
        "predicted_next_day_available_beds",
    } <= set(prediction) for prediction in payload["predictions"])
