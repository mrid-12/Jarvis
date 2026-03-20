import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

client = genai.Client()

with open("models.txt", "w") as f:
    f.write("Listing supported models:\n")
    for model in client.models.list():
        f.write(f"Name: {model.name}, Display Name: {model.display_name}\n")
