"""
ChromaDB Browsing Utility for Sahajayoga Resource Collection

Provides methods to browse, search, and analyze the ChromaDB collection
storing resource/handout embeddings.
"""

import os
import sys
import chromadb
from typing import List, Dict, Optional, Any
from datetime import datetime

# Add project root to Python path for config import
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import get_chroma_dir

# -----------------------------
# CONFIGURATION (matches resource_ingestion.py)
# -----------------------------
CHROMA_DIR = get_chroma_dir()
COLLECTION_NAME = "sahajayoga_resources"

# Ensure persist directory exists
os.makedirs(CHROMA_DIR, exist_ok=True)

# -----------------------------
# INITIALIZATION
# -----------------------------
client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = client.get_or_create_collection(name=COLLECTION_NAME)


# -----------------------------
# UTILITY FUNCTIONS
# -----------------------------
def _format_entry(entry_id: str, document: str, metadata: Dict) -> Dict:
    """Format a ChromaDB entry into a clean dictionary."""
    return {
        "id": entry_id,
        "document": document,
        "metadata": metadata
    }


def _print_entry(entry: Dict, index: Optional[int] = None):
    """Pretty print a single resource entry (full display, no truncation).
    
    Args:
        entry: Entry dictionary to print
        index: Optional index number to display
    """
    prefix = f"[{index}] " if index is not None else ""
    metadata = entry["metadata"]
    print(f"\n{prefix}{'='*80}")
    print(f"ID: {entry['id']}")
    print(f"Title: {metadata.get('title', 'N/A')}")
    print(f"Topic: {metadata.get('topic', 'N/A')}")
    print(f"Tags: {metadata.get('tags', 'N/A')}")
    print(f"File Type: {metadata.get('file_type', 'N/A')}")
    print(f"Download URL: {metadata.get('download_url', 'N/A')}")
    print(f"Created: {metadata.get('created_at', 'N/A')}")
    print(f"Updated: {metadata.get('updated_at', 'N/A')}")
    
    # Description - show full
    description = metadata.get('description', 'N/A')
    print(f"\nDescription:\n{description}")
    
    # Document/Embedding Text - show full
    document = entry.get('document', '')
    print(f"\nDocument (embedding_text):\n{document}")


def _sort_by_created_at(entries: List[Dict]) -> List[Dict]:
    """Sort entries by created_at timestamp (oldest first)."""
    def get_created_at(entry):
        created_at = entry["metadata"].get("created_at", "")
        try:
            # Parse ISO format timestamp
            if created_at.endswith("Z"):
                created_at = created_at.replace("Z", "+00:00")
            return datetime.fromisoformat(created_at)
        except (ValueError, AttributeError):
            # If parsing fails, put at the end
            return datetime.max
    
    return sorted(entries, key=get_created_at)


# -----------------------------
# BROWSING METHODS
# -----------------------------
def list_all_resources(limit: Optional[int] = None) -> List[Dict]:
    """
    List all resources in the collection, sorted by creation date (oldest first).
    
    Args:
        limit: Maximum number of entries to return (None for all)
    
    Returns:
        List of resource entry dictionaries, sorted by created_at (oldest first)
    """
    results = collection.get(limit=limit if limit else None)
    
    entries = [
        _format_entry(entry_id, doc, meta)
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    
    # Sort by created_at (oldest first)
    entries = _sort_by_created_at(entries)
    
    print(f"\n📊 Found {len(entries)} resources in collection '{COLLECTION_NAME}':\n")
    for idx, entry in enumerate(entries, 1):
        _print_entry(entry, idx)
    
    return entries


def list_resources_by_topic(topic: str) -> List[Dict]:
    """
    List all resources for a specific topic (case-insensitive partial match).
    
    Args:
        topic: Topic name to filter by (case-insensitive, partial match)
    
    Returns:
        List of resource entry dictionaries matching the topic, sorted by created_at (oldest first)
    """
    # Get all resources (ChromaDB where clause does exact match, so we filter in Python)
    results = collection.get()
    
    # Filter by topic (case-insensitive partial match)
    topic_lower = topic.lower()
    matching_entries = []
    
    for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
        resource_topic = meta.get("topic", "").lower()
        if topic_lower in resource_topic:
            matching_entries.append(_format_entry(entry_id, doc, meta))
    
    # Sort by created_at (oldest first)
    entries = _sort_by_created_at(matching_entries)
    
    print(f"\n📚 Found {len(entries)} resources for topic '{topic}':\n")
    for idx, entry in enumerate(entries, 1):
        _print_entry(entry, idx)
    
    return entries


def get_resource_by_id(resource_id: str) -> Optional[Dict]:
    """
    Get a specific resource by its ChromaDB ID.
    
    Args:
        resource_id: ChromaDB resource ID (e.g., "resource_001")
    
    Returns:
        Resource entry dictionary if found, None otherwise
    """
    try:
        results = collection.get(ids=[resource_id])
        if not results["ids"]:
            print(f"⚠️  Resource with ID '{resource_id}' not found")
            return None
        
        entry = _format_entry(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0]
        )
        
        print(f"\n🔍 Resource found:\n")
        _print_entry(entry)
        
        return entry
    except Exception as e:
        print(f"❌ Error retrieving resource '{resource_id}': {e}")
        return None


def get_collection_stats() -> Dict[str, Any]:
    """
    Get statistics about the resource collection.
    
    Returns:
        Dictionary with collection statistics
    """
    # Get all entries without printing
    results = collection.get()
    all_entries = [
        _format_entry(entry_id, doc, meta)
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    
    # Count by file type
    file_type_counts = {}
    topics = set()
    topic_counts = {}
    all_tags = []
    
    for entry in all_entries:
        metadata = entry["metadata"]
        
        # File type distribution
        file_type = metadata.get("file_type", "unknown")
        file_type_counts[file_type] = file_type_counts.get(file_type, 0) + 1
        
        # Topic distribution
        topic = metadata.get("topic", "")
        if topic:
            topics.add(topic)
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        
        # Collect all tags
        tags_str = metadata.get("tags", "")
        if tags_str:
            # Tags are stored as comma-separated string
            tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]
            all_tags.extend(tags)
    
    # Count tag frequency
    from collections import Counter
    tag_counts = Counter(all_tags)
    
    # Get date range
    created_dates = []
    for entry in all_entries:
        created_at = entry["metadata"].get("created_at", "")
        if created_at:
            try:
                if created_at.endswith("Z"):
                    created_at = created_at.replace("Z", "+00:00")
                created_dates.append(datetime.fromisoformat(created_at))
            except (ValueError, AttributeError):
                pass
    
    oldest = min(created_dates) if created_dates else None
    newest = max(created_dates) if created_dates else None
    
    stats = {
        "total_resources": len(all_entries),
        "unique_topics": len(topics),
        "file_type_counts": file_type_counts,
        "topic_distribution": topic_counts,
        "most_common_tags": dict(tag_counts.most_common(10)),
        "oldest_resource": oldest.isoformat().replace("+00:00", "Z") if oldest else None,
        "newest_resource": newest.isoformat().replace("+00:00", "Z") if newest else None,
        "collection_name": COLLECTION_NAME,
        "persist_directory": CHROMA_DIR
    }
    
    print(f"\n📈 Collection Statistics for '{COLLECTION_NAME}':\n")
    print(f"{'='*80}")
    print(f"Total Resources: {stats['total_resources']}")
    print(f"Unique Topics: {stats['unique_topics']}")
    print(f"\nResources by File Type:")
    for file_type, count in sorted(stats['file_type_counts'].items()):
        print(f"  - {file_type}: {count}")
    print(f"\nTopic Distribution:")
    for topic, count in sorted(stats['topic_distribution'].items(), key=lambda x: -x[1]):
        print(f"  - {topic}: {count}")
    print(f"\nMost Common Tags:")
    for tag, count in stats['most_common_tags'].items():
        print(f"  - {tag}: {count}")
    if stats['oldest_resource']:
        print(f"\nDate Range:")
        print(f"  - Oldest: {stats['oldest_resource']}")
        print(f"  - Newest: {stats['newest_resource']}")
    print(f"{'='*80}\n")
    
    return stats


def clear_collection(confirm: bool = False) -> bool:
    """
    Clear all resources from the collection.
    
    Args:
        confirm: If True, skip confirmation prompt and delete immediately
    
    Returns:
        True if deletion was successful, False if cancelled
    """
    # Get current count
    results = collection.get()
    count = len(results["ids"])
    
    if count == 0:
        print("ℹ️  Collection is already empty")
        return True
    
    print(f"\n⚠️  WARNING: This will delete ALL {count} resources from the collection '{COLLECTION_NAME}'")
    print("   This action cannot be undone!\n")
    
    if not confirm:
        try:
            response = input("Are you sure you want to proceed? Type 'yes' to confirm: ").strip().lower()
            if response != "yes":
                print("❌ Deletion cancelled")
                return False
        except (KeyboardInterrupt, EOFError):
            print("\n❌ Deletion cancelled")
            return False
    
    try:
        # Delete all entries by their IDs
        collection.delete(ids=results["ids"])
        
        print(f"✅ Successfully deleted {count} resources from collection '{COLLECTION_NAME}'")
        
        return True
    except Exception as e:
        print(f"❌ Error deleting resources: {e}")
        return False


# -----------------------------
# CLI / TESTING
# -----------------------------
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Resource ChromaDB Browsing Utility")
        print("\nUsage:")
        print("  python browse_resources.py stats                    # Show collection statistics")
        print("  python browse_resources.py all [limit]             # List all resources (oldest first)")
        print("  python browse_resources.py topic <topic>           # List resources by topic")
        print("  python browse_resources.py get <resource_id>        # Get specific resource")
        print("  python browse_resources.py clear [--yes]           # Clear all resources (DESTRUCTIVE)")
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    try:
        if command == "stats":
            get_collection_stats()
        
        elif command == "all":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
            list_all_resources(limit=limit)
        
        elif command == "topic":
            if len(sys.argv) < 3:
                print("❌ Error: topic required")
                sys.exit(1)
            list_resources_by_topic(sys.argv[2])
        
        elif command == "get":
            if len(sys.argv) < 3:
                print("❌ Error: resource_id required")
                sys.exit(1)
            get_resource_by_id(sys.argv[2])
        
        elif command == "clear":
            confirm = "--yes" in sys.argv or "-y" in sys.argv
            success = clear_collection(confirm=confirm)
            sys.exit(0 if success else 1)
        
        else:
            print(f"❌ Unknown command: {command}")
            sys.exit(1)
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

