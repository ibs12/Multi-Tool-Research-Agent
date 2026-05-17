"""
server.py
---------
Starts the FastAPI server with uvicorn.

Usage:
    python server.py                  # default: 0.0.0.0:8000
    python server.py --port 8080
    python server.py --reload         # hot-reload for development
"""

from __future__ import annotations

import argparse
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Financial Research Agent API Server")
    parser.add_argument("--host", default=os.getenv("API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("API_PORT", 8000)))
    parser.add_argument("--reload", action="store_true", help="Hot-reload on code changes")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    print(f"\n  Financial Research Agent API")
    print(f"  Running on http://{args.host}:{args.port}")
    print(f"  Swagger UI: http://localhost:{args.port}/docs")
    print(f"  Press Ctrl+C to stop\n")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()