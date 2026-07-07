#!/bin/bash

# Quick Test Script for Video Search System
# Usage: ./quick_test.sh   (from project root)

set -e

echo "==================================="
echo "Video Search System - Quick Test"
echo "==================================="
echo ""

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ ! -f "requirements.txt" ]; then
    echo -e "${RED}Error: requirements.txt not found. Please run from project root.${NC}"
    exit 1
fi

# Step 1: Check dependencies
echo -e "${YELLOW}[1/6] Checking dependencies...${NC}"
if python3 -c "import chromadb, flask, openai, tenacity" 2>/dev/null; then
    echo -e "${GREEN}✓ Core dependencies installed${NC}"
else
    echo -e "${RED}✗ Missing dependencies. Run: pip3 install -r requirements.txt${NC}"
    exit 1
fi

# Step 2: Check API keys (project root)
echo -e "${YELLOW}[2/6] Checking API keys...${NC}"
missing_keys=0
if [ -f "api_key.txt" ] && [ -s "api_key.txt" ]; then
    echo -e "${GREEN}✓ YouTube API key (api_key.txt)${NC}"
else
    echo -e "${RED}✗ Missing api_key.txt at project root${NC}"
    missing_keys=1
fi
if [ -f "openai_api_key.txt" ] && [ -s "openai_api_key.txt" ]; then
    echo -e "${GREEN}✓ OpenAI API key (openai_api_key.txt)${NC}"
else
    echo -e "${RED}✗ Missing openai_api_key.txt at project root${NC}"
    missing_keys=1
fi
if [ "$missing_keys" -ne 0 ]; then
    exit 1
fi

# Step 3: Check ChromaDB data
echo -e "${YELLOW}[3/6] Checking ChromaDB data...${NC}"
if [ -d "resources/chroma_free_store" ] && [ "$(ls -A resources/chroma_free_store 2>/dev/null)" ]; then
    echo -e "${GREEN}✓ ChromaDB directory exists and has data${NC}"
    python3 resources/browse_videos.py stats | head -5
else
    echo -e "${YELLOW}⚠ ChromaDB is empty. Run: python3 resources/video_processing.py${NC}"
fi

# Step 4: Test search engine
echo -e "${YELLOW}[4/6] Testing search engine...${NC}"
python3 << 'PYEOF'
import sys
sys.path.insert(0, ".")
try:
    from search.video_search import search_video_sections
    results = search_video_sections("meditation", top_k=2)
    if results:
        print(f"✓ Search works! Found {len(results)} results")
        print(f"  Top result: {results[0]['video_title']}")
    else:
        print("⚠ Search works but returned no results (Chroma may be empty)")
except Exception as e:
    print(f"✗ Search failed: {e}")
    sys.exit(1)
PYEOF

# Step 5: Test API server import
echo -e "${YELLOW}[5/6] Testing API server import...${NC}"
python3 << 'PYEOF'
import sys
sys.path.insert(0, ".")
try:
    from api.flask_api_server import app
    print("✓ API server imports successfully")
except Exception as e:
    print(f"✗ API server import failed: {e}")
    sys.exit(1)
PYEOF

# Step 6: Health endpoint (optional — only if server already running)
echo -e "${YELLOW}[6/6] Checking API health (if server is running)...${NC}"
if curl -sf http://localhost:5005/health >/dev/null 2>&1; then
    echo -e "${GREEN}✓ Flask server responding on port 5005${NC}"
else
    echo -e "${YELLOW}⚠ Server not running (start with: python3 api/flask_api_server.py)${NC}"
fi

echo ""
echo -e "${GREEN}==================================="
echo "All checks passed! System is ready."
echo -e "===================================${NC}"
echo ""
echo "Next steps:"
echo "1. Start API server: python3 api/flask_api_server.py"
echo "2. Test API: curl -X POST http://localhost:5005/search -H 'Content-Type: application/json' -d '{\"query\": \"meditation\"}'"
echo "3. Open frontend: open http://localhost:5005/"
echo "4. Browse ChromaDB: python3 resources/browse_videos.py stats"
echo ""
