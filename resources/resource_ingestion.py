"""
Resource ingestion service for meditation handouts.

Handles ingestion of handout resources into ChromaDB with proper
separation from video resources using a different collection.
"""

import os
import sys
import re
from datetime import datetime, timezone
from typing import Dict, Optional, List, Tuple
import chromadb
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

# Add project root to Python path for config import
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import load_openai_api_key, get_chroma_dir

# -----------------------------
# CONFIGURATION
# -----------------------------
CHROMA_DIR = get_chroma_dir()
COLLECTION_NAME = "sahajayoga_resources"

# Ensure persist directory exists
os.makedirs(CHROMA_DIR, exist_ok=True)

# -----------------------------
# OpenAI Client Setup
# -----------------------------
OPENAI_API_KEY = load_openai_api_key()
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# -----------------------------
# ChromaDB Client and Collection
# -----------------------------
client = chromadb.PersistentClient(path=CHROMA_DIR)

# Get or create collection (same embedding dimension as videos: 3072)
try:
    collection = client.get_collection(name=COLLECTION_NAME)
except Exception:
    # Collection doesn't exist, create it
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_embedding(text: str) -> List[float]:
    """Get embedding from OpenAI API with retry/backoff."""
    if not openai_client:
        raise ValueError("OpenAI client not initialized. Please check your OpenAI API key.")
    response = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=text
    )
    return response.data[0].embedding


def build_embedding_text(resource: Dict) -> str:
    """Build the textual context for embeddings from resource data."""
    tags_str = ", ".join(resource.get("tags", [])) if resource.get("tags") else ""
    return (
        f"Title: {resource['title']}\n"
        f"Description: {resource['description']}\n"
        f"Topic: {resource['topic']}\n"
        f"Tags: {tags_str}"
    )


def generate_resource_id() -> str:
    """
    Generate a sequential resource ID (resource_001, resource_002, etc.).
    
    Returns:
        Next available resource ID in sequence
    """
    try:
        # Get all existing IDs
        all_results = collection.get(include=["metadatas"])
        existing_ids = all_results.get("ids", [])
        
        if not existing_ids:
            return "resource_001"
        
        # Extract numeric parts and find max
        max_num = 0
        for resource_id in existing_ids:
            # Extract number from resource_XXX format
            match = re.search(r'resource_(\d+)', resource_id)
            if match:
                num = int(match.group(1))
                max_num = max(max_num, num)
        
        # Generate next ID
        next_num = max_num + 1
        return f"resource_{next_num:03d}"  # Format as 001, 002, etc.
    
    except Exception as e:
        # If anything fails, start from 001
        print(f"Warning: Error generating resource ID: {e}. Starting from resource_001")
        return "resource_001"


def check_duplicate_url(download_url: str) -> Optional[str]:
    """
    Check if a resource with the given download_url already exists.
    
    Args:
        download_url: URL to check for duplicates
        
    Returns:
        Existing resource ID if duplicate found, None otherwise
    """
    try:
        existing = collection.get(
            where={"download_url": download_url},
            limit=1
        )
        if existing["ids"]:
            return existing["ids"][0]
        return None
    except Exception as e:
        # If query fails, assume no duplicate (will be caught during add)
        print(f"Warning: Error checking duplicate URL: {e}")
        return None


def validate_resource_data(data: Dict) -> Tuple[bool, Optional[str]]:
    """
    Validate resource data before ingestion.
    
    Args:
        data: Resource data dictionary
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = ["title", "description", "topic", "tags", "download_url", "file_type"]
    
    # Check required fields
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: '{field}'"
        
        if field == "tags":
            if not isinstance(data[field], list):
                return False, f"Field 'tags' must be a list"
        else:
            if not isinstance(data[field], str) or not data[field].strip():
                return False, f"Field '{field}' must be a non-empty string"
    
    # Validate URL format (basic check)
    url = data["download_url"].strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "Field 'download_url' must be a valid HTTP or HTTPS URL"
    
    # Validate string lengths
    if len(data["title"]) > 200:
        return False, "Field 'title' exceeds maximum length of 200 characters"
    
    if len(data["description"]) > 2000:
        return False, "Field 'description' exceeds maximum length of 2000 characters"
    
    if len(data["topic"]) > 100:
        return False, "Field 'topic' exceeds maximum length of 100 characters"
    
    # Validate tags
    if len(data["tags"]) > 50:
        return False, "Field 'tags' cannot have more than 50 items"
    
    for tag in data["tags"]:
        if not isinstance(tag, str):
            return False, "All items in 'tags' must be strings"
        if len(tag) > 50:
            return False, "Each tag cannot exceed 50 characters"
    
    return True, None


def ingest_resource(resource_data: Dict) -> Dict:
    """
    Ingest a single resource into ChromaDB.
    
    Args:
        resource_data: Dictionary containing resource fields:
            - title: str (required)
            - description: str (required)
            - topic: str (required)
            - tags: List[str] (required, can be empty)
            - download_url: str (required, must be unique)
            - file_type: str (required)
    
    Returns:
        Dictionary with status and resource_id
        
    Raises:
        ValueError: If validation fails or duplicate URL found
        Exception: If ingestion fails
    """
    # Validate input
    is_valid, error_msg = validate_resource_data(resource_data)
    if not is_valid:
        raise ValueError(error_msg)
    
    # Check for duplicate URL
    download_url = resource_data["download_url"].strip()
    existing_id = check_duplicate_url(download_url)
    if existing_id:
        raise ValueError(f"Resource with download_url already exists: {existing_id}")
    
    # Generate resource ID
    resource_id = generate_resource_id()
    
    # Prepare resource with system fields
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    resource = {
        "id": resource_id,
        "title": resource_data["title"].strip(),
        "description": resource_data["description"].strip(),
        "topic": resource_data["topic"].strip(),
        "tags": ", ".join([tag.strip() for tag in resource_data["tags"]]),  # Store as comma-separated string
        "download_url": download_url,
        "file_type": resource_data["file_type"].strip(),
        "created_at": now,
        "updated_at": now
    }
    
    # Build embedding text
    embedding_text = build_embedding_text({
        "title": resource["title"],
        "description": resource["description"],
        "topic": resource["topic"],
        "tags": resource_data["tags"]  # Use original list for embedding
    })
    
    # Generate embedding
    embedding = get_embedding(embedding_text)
    
    # Add to ChromaDB
    collection.add(
        documents=[embedding_text],
        embeddings=[embedding],
        metadatas=[{k: v for k, v in resource.items() if k != "id"}],  # Exclude id from metadata (it's the ChromaDB ID)
        ids=[resource_id]
    )
    
    return {
        "status": "success",
        "resource_id": resource_id,
        "message": "Resource ingested successfully",
        "created_at": now
    }


def get_resource_by_id(resource_id: str) -> Dict:
    """
    Get a resource by its ID from ChromaDB.
    
    Args:
        resource_id: The resource ID to retrieve
    
    Returns:
        Dictionary containing resource data with parsed tags as list
    
    Raises:
        ValueError: If resource not found
    """
    try:
        results = collection.get(
            ids=[resource_id],
            include=["documents", "metadatas"]
        )
        
        if not results["ids"] or len(results["ids"]) == 0:
            raise ValueError(f"Resource with ID '{resource_id}' not found")
        
        metadata = results["metadatas"][0]
        document = results["documents"][0] if results["documents"] else ""
        
        # Parse tags from comma-separated string back to list
        tags_str = metadata.get("tags", "")
        tags_list = []
        if tags_str:
            if isinstance(tags_str, list):
                tags_list = tags_str
            elif isinstance(tags_str, str):
                tags_list = [tag.strip() for tag in tags_str.split(",") if tag.strip()]
        
        return {
            "resource_id": resource_id,
            "title": metadata.get("title", ""),
            "description": metadata.get("description", ""),
            "topic": metadata.get("topic", ""),
            "tags": tags_list,
            "download_url": metadata.get("download_url", ""),
            "file_type": metadata.get("file_type", ""),
            "created_at": metadata.get("created_at", ""),
            "updated_at": metadata.get("updated_at", ""),
            "document": document  # Embedding text for reference
        }
    except Exception as e:
        if "not found" in str(e).lower():
            raise ValueError(f"Resource with ID '{resource_id}' not found")
        raise


def update_resource(resource_id: str, resource_data: Dict) -> Dict:
    """
    Update an existing resource in ChromaDB.
    
    This function deletes the old resource and recreates it with updated data
    and a new embedding. The resource_id remains the same.
    
    Args:
        resource_id: The resource ID to update
        resource_data: Dictionary containing updated resource fields:
            - title: str (required)
            - description: str (required)
            - topic: str (required)
            - tags: List[str] (required, can be empty)
            - download_url: str (required)
            - file_type: str (required)
    
    Returns:
        Dictionary with status and resource_id
        
    Raises:
        ValueError: If validation fails, resource not found, or duplicate URL
        Exception: If update fails
    """
    # Check if resource exists
    try:
        existing_resource = get_resource_by_id(resource_id)
    except ValueError:
        raise ValueError(f"Resource with ID '{resource_id}' not found")
    
    # Validate input
    is_valid, error_msg = validate_resource_data(resource_data)
    if not is_valid:
        raise ValueError(error_msg)
    
    # Check for duplicate URL (if download_url changed)
    download_url = resource_data["download_url"].strip()
    existing_id = check_duplicate_url(download_url)
    if existing_id and existing_id != resource_id:
        raise ValueError(f"Resource with download_url already exists: {existing_id}")
    
    # Get original created_at to preserve it
    original_created_at = existing_resource.get("created_at", "")
    
    # Prepare updated resource with system fields
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    resource = {
        "id": resource_id,  # Keep the same ID
        "title": resource_data["title"].strip(),
        "description": resource_data["description"].strip(),
        "topic": resource_data["topic"].strip(),
        "tags": ", ".join([tag.strip() for tag in resource_data["tags"]]),  # Store as comma-separated string
        "download_url": download_url,
        "file_type": resource_data["file_type"].strip(),
        "created_at": original_created_at,  # Preserve original creation date
        "updated_at": now  # Update the updated_at timestamp
    }
    
    # Build embedding text
    embedding_text = build_embedding_text({
        "title": resource["title"],
        "description": resource["description"],
        "topic": resource["topic"],
        "tags": resource_data["tags"]  # Use original list for embedding
    })
    
    # Generate new embedding
    embedding = get_embedding(embedding_text)
    
    # Delete old entry and add new one with same ID
    try:
        collection.delete(ids=[resource_id])
        collection.add(
            documents=[embedding_text],
            embeddings=[embedding],
            metadatas=[{k: v for k, v in resource.items() if k != "id"}],  # Exclude id from metadata
            ids=[resource_id]  # Use the same ID
        )
    except Exception as e:
        # If add fails after delete, try to restore (best effort)
        # In production, you might want more sophisticated error handling
        raise Exception(f"Failed to update resource: {str(e)}")
    
    return {
        "status": "success",
        "resource_id": resource_id,
        "message": "Resource updated successfully",
        "updated_at": now
    }

