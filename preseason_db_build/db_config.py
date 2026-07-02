"""
Engine for db scripts
Author - Jason Druckenmiller
Created - 7/2/2026
Updated - 7/2/2026
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

print("Database engine successfully configured.")
