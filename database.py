# database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv

# Load .env file if it exists (only for local development)
load_dotenv()

# Database URL - checks these in order:
# 1. Environment variable (Render, Docker, other hosting)
# 2. .env file (local development)
# 3. SQLite fallback (no setup needed)
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

# Create sessionmaker
SessionLocal = sessionmaker(bind=engine)

# Create base
Base = declarative_base()

# Database dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()