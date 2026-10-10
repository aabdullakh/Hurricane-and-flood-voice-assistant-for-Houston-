import asyncio
import httpx
import re
from app.config import settings
from app import geo
FEMA_URL = (
    "https://gis.fema.gov/arcgis/rest/services/"
    "NSS/OpenShelters/FeatureServer/0/query"
)


async def get_open_shelters(client, lat, lon):
    params = {
        "where": "1=1",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": 50,
        "units": "esriSRUnit_StatuteMile",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }

    response = await client.get(
        FEMA_URL, params=params, timeout=15
    )
    response.raise_for_status()

    data = response.json()
    print("Raw FEMA results:", len(data.get("features", [])))

    for feature in data.get("features", [])[:5]:
        info = feature["attributes"]
        print(
            "Shelter:", info.get("shelter_name"),
            "| Status:", info.get("shelter_status")
        )

    if "error" in data:
        raise ValueError(data["error"])

    return [
        feature["attributes"]
        for feature in data.get("features", [])
    ]


async def resolve_location(client, location):
    location = location.strip()

    if not location or location.lower() in {
        "near me", "my location", "here"
    }:
        return None

    # Handle five-digit U.S. ZIP codes
    if re.fullmatch(r"\d{5}", location):
        return await geo.geocode_zip(client, location)

    # Convert city or address into coordinates
    response = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "q": location,
            "format": "json",
            "countrycodes": "us",
            "limit": 1
        },
        headers={"User-Agent": settings.nws_user_agent},
        timeout=12
    )

    response.raise_for_status()
    results = response.json()

    if not results:
        return None

    return (
        float(results[0]["lat"]),
        float(results[0]["lon"])
    )

async def main():
    async with httpx.AsyncClient() as client:
        shelters = await get_open_shelters(
            client, 30.76500876, -85.55600048
        )

        print("Open shelters found:", len(shelters))

        for shelter in shelters[:5]:
            print(
                shelter.get("shelter_name"),
                shelter.get("address"),
                shelter.get("city"),
            )


if __name__ == "__main__":
    asyncio.run(main())