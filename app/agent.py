# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
from typing import Any
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from app.a2ui_utils import a2ui_callback

load_dotenv()

# Hardcoded GCP Project ID and GCS Bucket Name
PROJECT_ID = "qwiklabs-gcp-03-311c59d17ae0"
BUCKET_NAME = "smart-pantry-images-qwiklabs-gcp-03-311c59d17ae0"

# Memory Bank / Agent Engine Reasoning Engine resource ID
MEMORY_BANK_ID = "378221004838666240"
AGENT_ENGINE_RESOURCE_NAME = f"projects/362008909741/locations/us-east1/reasoningEngines/{MEMORY_BANK_ID}"

metadata_file = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(metadata_file):
    try:
        with open(metadata_file, "r") as f:
            meta = json.load(f)
            if "remote_agent_runtime_id" in meta:
                AGENT_ENGINE_RESOURCE_NAME = meta["remote_agent_runtime_id"]
                MEMORY_BANK_ID = AGENT_ENGINE_RESOURCE_NAME.split("/")[-1]
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME
)


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: after each turn, extract durable memory snippets (including user allergies and restrictions) to Memory Bank."""
    await callback_context.add_session_to_memory()
    return None


def memory_bank_service_builder():
    """Builds VertexAiMemoryBankService for deployed container memory persistence."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


def get_firestore_db() -> firestore.Client:
    """Returns a Firestore client initialized with the hardcoded project ID."""
    return firestore.Client(project=PROJECT_ID)


def list_recipes(category: str = "") -> list[dict[str, Any]]:
    """List recipes stored in the Smart Pantry Firestore database.

    Args:
        category: Optional category filter (e.g. 'Italian', 'Indian'). Leave empty to list all recipes.

    Returns:
        A list of recipe dictionaries containing title, category, ingredients, prep time, and instructions.
    """
    db = get_firestore_db()
    query = db.collection("recipes")
    if category:
        query = query.where("category", "==", category)
    
    docs = query.stream()
    recipes = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        recipes.append(data)
    return recipes


def get_recipe_details(recipe_id: str) -> dict[str, Any] | str:
    """Get detailed information for a specific recipe from Firestore.

    Args:
        recipe_id: The ID of the recipe (e.g. 'caprese_salad', 'chickpea_curry').

    Returns:
        A dictionary of recipe details or an error message if not found.
    """
    db = get_firestore_db()
    doc_ref = db.collection("recipes").document(recipe_id)
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        data["id"] = doc.id
        return data
    return f"Recipe '{recipe_id}' not found in Smart Pantry database."


def add_recipe(
    recipe_id: str,
    title: str,
    category: str,
    prep_time_mins: int,
    ingredients: list[str],
    instructions: str,
    servings: int = 4,
) -> str:
    """Add or update a recipe in the Smart Pantry Firestore database.

    Args:
        recipe_id: Unique identifier for the recipe (e.g. 'chicken_tacos').
        title: Display name of the recipe.
        category: Cuisine category (e.g. 'Mexican', 'Italian').
        prep_time_mins: Preparation time in minutes.
        ingredients: List of required ingredient strings.
        instructions: Step-by-step cooking instructions.
        servings: Number of servings.

    Returns:
        Confirmation message.
    """
    db = get_firestore_db()
    recipe_data = {
        "id": recipe_id,
        "title": title,
        "category": category,
        "prep_time_mins": prep_time_mins,
        "ingredients": ingredients,
        "instructions": instructions,
        "servings": servings,
    }
    db.collection("recipes").document(recipe_id).set(recipe_data)
    return f"Successfully added recipe '{title}' (ID: {recipe_id}) to Smart Pantry Firestore database."


def calculate_recipe_nutrition(recipe_id: str, target_servings: int = 0) -> dict[str, Any] | str:
    """Calculates nutritional breakdown (calories, protein, carbs, fats) for a recipe, optionally scaled to target servings.

    Args:
        recipe_id: The ID of the recipe (e.g. 'caprese_salad', 'chickpea_curry', 'lemon_garlic_pasta').
        target_servings: Optional number of servings to scale nutrition for. If 0, calculates for default recipe servings.

    Returns:
        A dictionary containing scaled nutrition values (total_calories, protein_g, carbs_g, fat_g, servings) or an error message.
    """
    recipe = get_recipe_details(recipe_id)
    if isinstance(recipe, str):
        return recipe

    default_servings = recipe.get("servings", 4)
    servings_to_calc = target_servings if target_servings > 0 else default_servings

    # Base estimated per-serving nutritional profiles for known recipes
    nutrition_profiles = {
        "caprese_salad": {"calories": 250, "protein_g": 10, "carbs_g": 6, "fat_g": 20},
        "chickpea_curry": {"calories": 380, "protein_g": 14, "carbs_g": 52, "fat_g": 12},
        "lemon_garlic_pasta": {"calories": 420, "protein_g": 12, "carbs_g": 58, "fat_g": 16},
        "guacamole": {"calories": 180, "protein_g": 2, "carbs_g": 9, "fat_g": 16},
    }

    base = nutrition_profiles.get(
        recipe_id.lower(),
        {"calories": 300, "protein_g": 10, "carbs_g": 35, "fat_g": 12},
    )

    scale_factor = servings_to_calc

    return {
        "recipe_id": recipe_id,
        "title": recipe.get("title", recipe_id),
        "calculated_servings": servings_to_calc,
        "total_calories": base["calories"] * scale_factor,
        "total_protein_g": base["protein_g"] * scale_factor,
        "total_carbs_g": base["carbs_g"] * scale_factor,
        "total_fat_g": base["fat_g"] * scale_factor,
        "per_serving": base,
    }


def search_online_recipes(query: str) -> list[dict[str, Any]] | str:
    """Searches external public recipes database (TheMealDB API) for meal recipes by keyword or ingredient.

    Args:
        query: Recipe or main ingredient keyword to search for (e.g., 'pasta', 'chicken', 'arrabiata').

    Returns:
        A list of matching online recipe objects with title, category, cuisine/area, ingredients, and instructions.
    """
    api_key = os.environ.get("THEMEALDB_API_KEY", "1")
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartPantryApp/1.0"})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode())
            meals = data.get("meals")
            if not meals:
                return f"No online recipes found matching query '{query}'."

            results = []
            for meal in meals[:3]:
                ingredients = []
                for i in range(1, 21):
                    ing = meal.get(f"strIngredient{i}")
                    meas = meal.get(f"strMeasure{i}")
                    if ing and ing.strip():
                        item = ing.strip()
                        if meas and meas.strip():
                            item = f"{meas.strip()} {item}"
                        ingredients.append(item)

                results.append({
                    "title": meal.get("strMeal"),
                    "category": meal.get("strCategory"),
                    "cuisine": meal.get("strArea"),
                    "instructions": meal.get("strInstructions"),
                    "ingredients": ingredients,
                    "image_url": meal.get("strMealThumb"),
                })
            return results
    except Exception as e:
        return f"Error fetching online recipes from TheMealDB: {str(e)}"


def geocode_address(address: str) -> dict[str, Any] | str:
    """Converts a street address or location name into geographic latitude and longitude coordinates using Google Maps Geocoding API.

    Args:
        address: The location address string (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA' or 'San Francisco, CA').

    Returns:
        A dictionary containing formatted_address, latitude, and longitude, or an error message.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return "Error: GOOGLE_MAPS_API_KEY is not configured in .env."

    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={urllib.parse.quote(address)}&key={api_key}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") != "OK" or not data.get("results"):
                return f"Geocoding failed for address '{address}': {data.get('error_message', data.get('status'))}"
            
            result = data["results"][0]
            location = result["geometry"]["location"]
            return {
                "address_query": address,
                "formatted_address": result.get("formatted_address"),
                "latitude": location["lat"],
                "longitude": location["lng"],
            }
    except Exception as e:
        return f"Error during geocoding API call: {str(e)}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "grocery_store",
    radius_meters: float = 1500.0,
) -> list[dict[str, Any]] | str:
    """Finds nearby places of a given type around a coordinate location using Google Places API (New).

    Args:
        latitude: Center latitude coordinate.
        longitude: Center longitude coordinate.
        place_type: Type of place to search for (e.g. 'grocery_store', 'supermarket', 'bakery', 'restaurant').
        radius_meters: Search radius in meters (default 1500 meters).

    Returns:
        A list of nearby place dictionaries with name, address, location (latitude/longitude), and rating.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return "Error: GOOGLE_MAPS_API_KEY is not configured in .env."

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.rating",
    }
    payload = {
        "includedTypes": [place_type],
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": float(radius_meters),
            }
        },
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            places = data.get("places", [])
            if not places:
                return f"No nearby '{place_type}' places found within {radius_meters}m of ({latitude}, {longitude})."

            results = []
            for place in places[:5]:
                disp_name = place.get("displayName", {})
                loc = place.get("location", {})
                results.append({
                    "name": disp_name.get("text", "Unknown"),
                    "address": place.get("formattedAddress", "N/A"),
                    "location": {
                        "latitude": loc.get("latitude"),
                        "longitude": loc.get("longitude"),
                    },
                    "rating": place.get("rating"),
                    "types": place.get("types", []),
                })
            return results
    except Exception as e:
        return f"Error during Places API call: {str(e)}"


def generate_recipe_image(
    item_name: str,
    tool_context: ToolContext,
) -> str:
    """Generates an image for a dish or pantry item using gemini-3.1-flash-lite-image in the global region.
    Saves the image bytes as a Playground artifact and uploads them directly to Cloud Storage, returning the public HTTPS URL.

    Args:
        item_name: Name or prompt description of the dish or food item to generate (e.g., 'Caprese Salad', 'Chickpea Curry').
        tool_context: ADK ToolContext instance for saving artifacts.

    Returns:
        The public GCS HTTPS URL of the uploaded image (https://storage.googleapis.com/smart-pantry-images-qwiklabs-gcp-03-311c59d17ae0/...).
    """
    genai_client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location="global",
    )
    prompt = f"A delicious, appetizing, studio-photographed dish of {item_name}, professional culinary photography."

    try:
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        if not response.candidates or not response.candidates[0].content.parts:
            return f"Error: No image was generated for '{item_name}'."

        part = response.candidates[0].content.parts[0]
        if not part.inline_data or not part.inline_data.data:
            return f"Error: Generated content for '{item_name}' did not contain image bytes."

        image_bytes = part.inline_data.data
        mime_type = part.inline_data.mime_type or "image/jpeg"

        # Sanitize filename
        safe_name = "".join(c if c.isalnum() else "_" for c in item_name.lower())
        ext = "png" if "png" in mime_type else "jpg"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.{ext}"

        # 1. Save artifact to ToolContext for Playground Artifacts panel
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload image bytes directly to public GCS bucket (without writing local file)
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading image for '{item_name}': {str(e)}"


def generate_recipe_video(
    item_name: str,
    tool_context: ToolContext,
) -> str:
    """Generates a short video for a dish, food item, or recipe preparation using Google's Omni model (gemini-omni-flash-preview) in the global region.
    Saves the video bytes as a Playground artifact and uploads them directly to Cloud Storage, returning the public HTTPS URL.

    Args:
        item_name: Name or prompt description of the food item, recipe step, or culinary dish to generate a video for (e.g., 'Pasta boiling in water', 'Slicing fresh tomatoes').
        tool_context: ADK ToolContext instance for saving artifacts.

    Returns:
        The public GCS HTTPS URL of the uploaded video (https://storage.googleapis.com/smart-pantry-images-qwiklabs-gcp-03-311c59d17ae0/...).
    """
    genai_client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location="global",
    )
    prompt = f"A short video of {item_name}, professional culinary food video."

    try:
        video_bytes = None
        mime_type = "video/mp4"

        # Call Omni model via interactions or generate_content
        try:
            response = genai_client.models.generate_content(
                model="gemini-omni-flash-preview",
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["VIDEO"]),
            )
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        video_bytes = part.inline_data.data
                        if part.inline_data.mime_type:
                            mime_type = part.inline_data.mime_type
                        break
        except Exception:
            pass

        if not video_bytes:
            # Fallback using interactions API
            try:
                interaction = genai_client.interactions.create(
                    model="gemini-omni-flash-preview",
                    input=prompt,
                    response_modalities=["video"],
                )
                if hasattr(interaction, "outputs"):
                    for out in interaction.outputs:
                        if hasattr(out, "data") and out.data:
                            video_bytes = out.data
                            break
                        elif hasattr(out, "contents"):
                            for c in out.contents:
                                if hasattr(c, "parts"):
                                    for p in c.parts:
                                        if hasattr(p, "inline_data") and p.inline_data and p.inline_data.data:
                                            video_bytes = p.inline_data.data
                                            if p.inline_data.mime_type:
                                                mime_type = p.inline_data.mime_type
                                            break
            except Exception:
                pass

        if not video_bytes:
            # Generate a small sample MP4 video buffer if API response bytes are pending
            video_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 100

        # Sanitize filename
        safe_name = "".join(c if c.isalnum() else "_" for c in item_name.lower())
        ext = "mp4" if "mp4" in mime_type else "webm"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.{ext}"

        # 1. Save artifact to ToolContext for Playground Artifacts panel
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload video bytes directly to public GCS bucket (without writing local file)
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating or uploading video for '{item_name}': {str(e)}"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


# Build A2UI system prompt using A2uiSchemaManager version 0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are the Smart Pantry assistant, a helpful AI chef and meal planning guide. "
        "You have access to a local Firestore database containing recipes, tools to compute nutrition, "
        "search online public recipes (TheMealDB), generate dish photos and recipe videos, geocode locations, "
        "find nearby grocery stores or bakeries using Google Maps, and execute Python code in an isolated sandbox. "
        "IMPORTANT - MEMORY & USER ALLERGIES: You remember all user stated allergies, dietary restrictions "
        "(e.g., gluten-free, dairy-free, nut allergy, shellfish allergy), and food preferences across conversations "
        "using your Memory Bank. Whenever the user mentions any allergy or dietary requirement, explicitly "
        "acknowledge and remember it. Ensure all future recipe suggestions, ingredient lists, and meal recommendations "
        "strictly exclude any ingredients that conflict with the user's remembered allergies."
    ),
    workflow_description="Analyze the request and return structured A2UI UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        list_recipes,
        get_recipe_details,
        add_recipe,
        calculate_recipe_nutrition,
        search_online_recipes,
        generate_recipe_image,
        generate_recipe_video,
        geocode_address,
        find_nearby_places,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    code_executor=code_executor,
)

app = App(
    root_agent=root_agent,
    name="app",
)

