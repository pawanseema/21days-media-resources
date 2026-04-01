#!/bin/bash

# Quick Test Script for Video Search System
# Usage: ./quick_test.sh

set -e  # Exit on error

echo "==================================="
echo "Video Search System - Quick Test"
echo "==================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the right directory
if [ ! -f "requirements.txt" ]; then
    echo -e "${RED}Error: requirements.txt not found. Please run from project root.${NC}"
    exit 1
fi

# Step 1: Check dependencies
echo -e "${YELLOW}[1/6] Checking dependencies...${NC}"
if python3 -c "import chromadb, flask, ollama" 2>/dev/null; then
    echo -e "${GREEN}✓ Core dependencies installed${NC}"
else
    echo -e "${RED}✗ Missing dependencies. Run: pip install -r requirements.txt${NC}"
    exit 1
fi

# Step 2: Check API key
echo -e "${YELLOW}[2/6] Checking YouTube API key...${NC}"
if [ -f "test21Days/api_key.txt" ] && [ -s "test21Days/api_key.txt" ]; then
    echo -e "${GREEN}✓ API key file exists${NC}"
else
    echo -e "${RED}✗ API key not found. Create test21Days/api_key.txt with your YouTube API key${NC}"
    exit 1
fi

# Step 3: Check Ollama
echo -e "${YELLOW}[3/6] Checking Ollama...${NC}"
if command -v ollama &> /dev/null; then
    if ollama list | grep -q "llama3"; then
        echo -e "${GREEN}✓ Ollama installed and llama3 model available${NC}"
    else
        echo -e "${YELLOW}⚠ Ollama installed but llama3 not found. Run: ollama pull llama3${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Ollama not found. Search will work but without LLM enrichment/reranking${NC}"
fi

# Step 4: Check if ChromaDB has data
echo -e "${YELLOW}[4/6] Checking ChromaDB data...${NC}"
if [ -d "test21Days/chroma_free_store" ] && [ "$(ls -A test21Days/chroma_free_store 2>/dev/null)" ]; then
    echo -e "${GREEN}✓ ChromaDB directory exists and has data${NC}"
    cd test21Days
    python3 browse_chromadb.py stats | head -5
    cd ..
else
    echo -e "${YELLOW}⚠ ChromaDB is empty. Running ingestion...${NC}"
    cd test21Days
    python3 video_processing.py
    cd ..
    echo -e "${GREEN}✓ Ingestion complete${NC}"
fi

# Step 5: Test search engine
echo -e "${YELLOW}[5/6] Testing search engine...${NC}"
python3 << 'PYEOF'
import sys
sys.path.append('.')
try:
    from search.video_search import search_video_sections
    results = search_video_sections("meditation", top_k=2)
    if results:
        print(f"✓ Search works! Found {len(results)} results")
        print(f"  Top result: {results[0]['video_title']}")
    else:
        print("⚠ Search works but returned no results")
except Exception as e:
    print(f"✗ Search failed: {e}")
    sys.exit(1)
PYEOF

# Step 6: Test API server (quick check)
echo -e "${YELLOW}[6/6] Testing API server startup...${NC}"
python3 << 'PYEOF'
import sys
sys.path.append('.')
try:
    from api.flask_api_server import app
    print("✓ API server imports successfully")
except Exception as e:
    print(f"✗ API server import failed: {e}")
    sys.exit(1)
PYEOF

echo ""
echo -e "${GREEN}==================================="
echo "All checks passed! System is ready."
echo "===================================${NC}"
echo ""
echo "Next steps:"
echo "1. Start API server: python api/flask_api_server.py"
echo "2. Test API: curl -X POST http://localhost:5005/search -H 'Content-Type: application/json' -d '{\"query\": \"meditation\"}'"
echo "3. Open frontend: open ui/search.html"
echo "4. Browse ChromaDB: python test21Days/browse_chromadb.py stats"
echo ""

