#!/usr/bin/env python3
"""
Perfetto Trace Analyzer Server Launcher

Usage:
    python3 run_server.py

This script properly launches the server with correct module imports.
"""

import sys
import os

# Add the backend directory to Python path
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

# Import and run the server
if __name__ == "__main__":
    from perfetto_trace_analyzer.server import app
    import uvicorn

    print("🚀 Starting Perfetto Trace Analyzer Server...")
    print("📊 Backend API: http://localhost:8000")
    print("🔍 Perfetto UI: http://localhost:8000/perfetto/")
    print("📁 Upload endpoint: http://localhost:8000/api/upload")

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")