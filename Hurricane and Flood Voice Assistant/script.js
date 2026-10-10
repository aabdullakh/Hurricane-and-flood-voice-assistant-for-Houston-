
// Create the map
const map = L.map("map").setView([51.505, -0.09], 13);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  referrerPolicy: "strict-origin-when-cross-origin",
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// Add a marker at the starting point
const marker = L.marker([51.505, -0.09])
  .addTo(map)
  .bindPopup("Click the map to check conditions");

// Turn common weather codes into readable descriptions
function describeWeather(code) {
  const descriptions = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
  };

  return descriptions[code] ?? `Weather code ${code}`;
}

function showLines(lines) {
  const output = document.querySelector("#conditions-output");

  output.replaceChildren(
    ...lines.map((line) => {
      const paragraph = document.createElement("p");
      paragraph.textContent = line;
      return paragraph;
    })
  );
}

function distanceKm(lat1, lon1, lat2, lon2) {
  const radians = (degrees) => (degrees * Math.PI) / 180;
  const earthRadiusKm = 6371;

  const dLat = radians(lat2 - lat1);
  const dLon = radians(lon2 - lon1);

  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(radians(lat1)) *
      Math.cos(radians(lat2)) *
      Math.sin(dLon / 2) ** 2;

  return 2 * earthRadiusKm * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

async function findNearestEmergencyShelter(lat, lng) {
  const query = `
    [out:json][timeout:15];
    nwr(around:10000,${lat},${lng})["emergency:social_facility"="shelter"];
    out center tags;
  `;

  const response = await fetch(
    `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`
  );

  if (!response.ok) {
    throw new Error("Shelter search failed");
  }

  const data = await response.json();

  const shelters = (data.elements ?? [])
    .map((item) => {
      const point =
        item.type === "node"
          ? { lat: item.lat, lon: item.lon }
          : item.center;

      return point ? { item, point } : null;
    })
    .filter(Boolean);

  if (shelters.length === 0) {
    return "No mapped emergency shelter found within 10 km. Check your local official emergency guidance; this does not mean no shelter exists.";
  }

  const nearest = shelters.reduce((best, candidate) => {
    const distance = distanceKm(
      lat,
      lng,
      candidate.point.lat,
      candidate.point.lon
    );

    return !best || distance < best.distance
      ? { ...candidate, distance }
      : best;
  }, null);

  const name = nearest.item.tags?.name || "Unnamed mapped emergency shelter";

  return `Nearest mapped emergency shelter: ${name}, ${nearest.distance.toFixed(
    1
  )} km away in a straight line. Confirm it is open and designated for this emergency.`;
}



async function selectLocation(lat, lng, label = "Selected point") {
  marker.setLatLng([lat, lng]).bindPopup(label).openPopup();

  showLines(["Loading conditions…"]);

  const weatherParams = new URLSearchParams({
    latitude: lat,
    longitude: lng,
    current:
      "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
    timezone: "auto",
  });

  const floodParams = new URLSearchParams({
    latitude: lat,
    longitude: lng,
    daily: "river_discharge",
    forecast_days: "7",
    timezone: "GMT",
  });

  try {
    const shelterPromise = findNearestEmergencyShelter(lat, lng).catch((error) => {
    console.error(error);
    return "Could not load shelter data. Check your local official emergency guidance.";
    });

    const [weatherResponse, floodResponse, shelterText] = await Promise.all([
    fetch(`https://api.open-meteo.com/v1/forecast?${weatherParams}`),
    fetch(`https://flood-api.open-meteo.com/v1/flood?${floodParams}`),
    shelterPromise,
    ]);


    if (!weatherResponse.ok || !floodResponse.ok) {
      throw new Error("The data service returned an error.");
    }

    const weather = await weatherResponse.json();
    const flood = await floodResponse.json();
    const current = weather.current;
    const daily = flood.daily;

    const lines = [
      `Selected coordinates: ${lat.toFixed(4)}, ${lng.toFixed(4)}`,
      `Weather at ${current.time}: ${describeWeather(current.weather_code)}`,
      `Temperature: ${current.temperature_2m} ${weather.current_units.temperature_2m}`,
      `Humidity: ${current.relative_humidity_2m}${weather.current_units.relative_humidity_2m}`,
      `Precipitation: ${current.precipitation} ${weather.current_units.precipitation}`,
      `Wind: ${current.wind_speed_10m} ${weather.current_units.wind_speed_10m}`,
      "Modeled river discharge forecast (not a flood-depth or warning):",
    ];

    if (daily?.time?.length && daily?.river_discharge?.length) {
      daily.time.forEach((date, index) => {
        const value = daily.river_discharge[index];
        const unit = flood.daily_units.river_discharge;
        lines.push(
          `${date}: ${value == null ? "No data" : `${value} ${unit}`}`
        );
      });
    } else {
      lines.push("No river-discharge forecast is available for this point.");
    }
    lines.push(shelterText);

    showLines(lines);
  } catch (error) {
    console.error(error);
    showLines([
      "Could not load the conditions. Check your internet connection and try again.",
    ]);
  }
}

map.on("click", (event) => {
  selectLocation(event.latlng.lat, event.latlng.lng);
});

// Location search (OpenStreetMap Nominatim geocoder)
const searchForm = document.querySelector("#search-form");
const searchInput = document.querySelector("#search-input");
const searchResults = document.querySelector("#search-results");

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = searchInput.value.trim();
  if (!query) return;
  searchResults.replaceChildren();
  searchResults.textContent = "Searching…";
  try {
    const params = new URLSearchParams({ q: query, format: "json", limit: "5" });
    const response = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, {
      headers: { "Accept-Language": navigator.language || "en" },
    });
    if (!response.ok) throw new Error("Search failed");
    const places = await response.json();
    searchResults.textContent = "";
    if (places.length === 0) {
      searchResults.textContent = "No matching places found.";
      return;
    }
    const pick = (place) => {
      const lat = parseFloat(place.lat);
      const lng = parseFloat(place.lon);
      map.setView([lat, lng], 13);
      searchResults.replaceChildren();
      selectLocation(lat, lng, place.display_name);
    };
    if (places.length === 1) return pick(places[0]);
    const list = document.createElement("ul");
    places.forEach((place) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = place.display_name;
      button.addEventListener("click", () => pick(place));
      item.appendChild(button);
      list.appendChild(item);
    });
    searchResults.appendChild(list);
  } catch (error) {
    console.error(error);
    searchResults.textContent = "Could not search right now. Please try again.";
  }
});

app.use(express.json());

app.post("/api/voice-assistant", async (req, res) => {
  try {
    const reply = await yourAiFunction(req.body.prompt);
    res.json({ reply });
  } catch {
    res.status(500).json({ error: "Assistant request failed" });
  }
});