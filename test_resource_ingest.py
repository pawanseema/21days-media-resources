#!/usr/bin/env python3
"""Test script for resource ingestion API"""

import requests
import json

API_URL = "http://localhost:5005/api/resources/ingest"

# Test data
test_resource = {
    "title": "Chakra Meditation Guide",
    "description": "Comprehensive guide to chakra meditation techniques for beginners. Learn about the seven chakras and how to balance them through meditation.",
    "topic": "Chakras",
    "tags": ["chakras", "meditation", "beginner"],
    "download_url": "http://example.com/handouts/chakra-guide.pdf",
    "file_type": "pdf"
}

print("Testing resource ingestion API...")
print("=" * 60)

try:
    response = requests.post(API_URL, json=test_resource)
    
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    if response.status_code == 201:
        print("\n✅ Resource ingested successfully!")
    else:
        print(f"\n❌ Error: {response.json().get('error', 'Unknown error')}")
        
except requests.exceptions.ConnectionError:
    print("❌ Error: Could not connect to API server.")
    print("   Make sure the Flask server is running on port 5005")
except Exception as e:
    print(f"❌ Error: {e}")

