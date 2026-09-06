"""
Engine for db scripts
Author - Jason Druckenmiller
Created - 7/2/2026
Updated - 9/6/2026
"""


import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

#Fetch database URL
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No DATABASE_URL found. Check your .env file.")

# Create Engine
engine = create_engine(DATABASE_URL)

# Print the target so it's obvious which database a pipeline run will write to
# (e.g. local vs. Render when DATABASE_URL is overridden on the command line).
print(f"DB engine -> {engine.url.host or 'local'} / {engine.url.database}")
