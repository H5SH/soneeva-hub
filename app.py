"""Run karbon-hub: `uv run python app.py`. PORT defaults to 8080; reloads in development."""

import os

import uvicorn
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=os.environ.get("ENVIRONMENT") == "development",
    )
