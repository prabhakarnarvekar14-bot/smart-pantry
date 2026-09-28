import os
from google.cloud import firestore

# Hardcoded GCP Project ID (Do NOT use GOOGLE_CLOUD_PROJECT env var or google.auth.default())
PROJECT_ID = "qwiklabs-gcp-03-311c59d17ae0"

def seed_database():
    print(f"Connecting to Firestore with project ID: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)
    
    recipes_ref = db.collection("recipes")
    
    sample_recipes = [
        {
            "id": "caprese_salad",
            "title": "Caprese Salad",
            "category": "Italian",
            "prep_time_mins": 10,
            "ingredients": ["tomatoes", "fresh mozzarella", "basil", "olive oil", "balsamic glaze"],
            "instructions": "Slice tomatoes and mozzarella. Layer with basil leaves. Drizzle with olive oil and balsamic glaze.",
            "servings": 2,
        },
        {
            "id": "chickpea_curry",
            "title": "Quick Chickpea Curry",
            "category": "Indian",
            "prep_time_mins": 25,
            "ingredients": ["chickpeas", "canned tomatoes", "coconut milk", "curry powder", "garlic", "onion"],
            "instructions": "Sauté garlic and onion. Stir in curry powder, tomatoes, and chickpeas. Simmer with coconut milk for 15 minutes.",
            "servings": 4,
        },
        {
            "id": "lemon_garlic_pasta",
            "title": "Lemon Garlic Butter Pasta",
            "category": "Italian",
            "prep_time_mins": 15,
            "ingredients": ["spaghetti", "garlic", "butter", "lemon juice", "parmesan cheese", "parsley"],
            "instructions": "Cook spaghetti. Melt butter with minced garlic. Toss pasta with garlic butter, lemon juice, and parmesan.",
            "servings": 3,
        }
    ]
    
    for recipe in sample_recipes:
        doc_id = recipe["id"]
        recipes_ref.document(doc_id).set(recipe)
        print(f"Seeded recipe: {doc_id} - {recipe['title']}")

    print("Firestore database seeding completed successfully!")

if __name__ == "__main__":
    seed_database()
