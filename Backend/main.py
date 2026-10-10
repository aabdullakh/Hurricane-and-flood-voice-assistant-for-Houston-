
from fastapi import FastAPI

app = FastAPI(title="Houston Flood Assistant")

@app.get("/health")
def health():
    return {"status": "running"}

@app.get("/flood-status")
def flood_status(location: str = "Houston"):
    return {
        "location": location,
        "status": "not_connected",
        "active_alerts": [],
        "nearest_gauges": [],
        "data_as_of": None,
        "source": []
    }
