"""
ChromaDB Browsing Utility for Sahajayoga Video Collection

Provides methods to browse, search, and analyze the ChromaDB collection
storing video and timestamp-level embeddings.
"""

import os
import sys
import csv
import chromadb
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from typing import List, Dict, Optional, Any

# Add project root to Python path for config import
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import get_chroma_dir, load_openai_api_key

# -----------------------------
# CONFIGURATION (matches video_processing.py)
# -----------------------------
CHROMA_DIR = get_chroma_dir()
COLLECTION_NAME = "sahajayoga_21_days_videos"
AUDIT_CSV_PATH = os.path.join(_project_root, "sahajyoga_recent5_audit.csv")

# Ensure persist directory exists
os.makedirs(CHROMA_DIR, exist_ok=True)

# -----------------------------
# OpenAI Client Setup
# -----------------------------
OPENAI_API_KEY = load_openai_api_key()
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_embedding(text):
    """Get embedding from OpenAI API with retry/backoff."""
    if not openai_client:
        raise ValueError("OpenAI client not initialized. Please check your OpenAI API key.")
    response = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=text
    )
    return response.data[0].embedding

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


def _print_entry(entry: Dict, index: Optional[int] = None, full: bool = False):
    """Pretty print a single entry.
    
    Args:
        entry: Entry dictionary to print
        index: Optional index number to display
        full: If True, show complete data without truncation
    """
    prefix = f"[{index}] " if index is not None else ""
    metadata = entry["metadata"]
    print(f"\n{prefix}{'='*80}")
    print(f"ID: {entry['id']}")
    print(f"Type: {metadata.get('type', 'N/A')}")
    print(f"Video ID: {metadata.get('video_id', 'N/A')}")
    print(f"Video Title: {metadata.get('video_title', 'N/A')}")
    print(f"Playlist ID: {metadata.get('playlist_id', 'N/A')}")
    if metadata.get('timestamp'):
        print(f"Timestamp: {metadata.get('timestamp', 'N/A')}")
    print(f"Section Title: {metadata.get('section_title', 'N/A')}")
    print(f"Chakra: {metadata.get('chakra', 'N/A')}")
    
    # Quote - show full or truncated
    quote = metadata.get('quote', '')
    if quote:
        if full:
            print(f"\nQuote:\n{quote}")
        else:
            print(f"Quote: {quote[:100]}...")
    
    # Summary - show full or truncated
    summary = metadata.get('section_summary', 'N/A')
    if full:
        print(f"\nSummary:\n{summary}")
    else:
        print(f"\nSummary:\n{summary[:200]}...")
    
    # Hashtags
    hashtags = metadata.get('hashtags', '')
    if hashtags:
        print(f"\nHashtags: {hashtags}")
    
    # Video URL
    video_url = metadata.get('video_url', '')
    if video_url:
        print(f"Video URL: {video_url}")
    
    # Published At
    published_at = metadata.get('published_at', '')
    if published_at:
        print(f"Published At: {published_at}")
    
    # Document/Embedding Text - show full or truncated
    document = entry.get('document', '')
    if full:
        print(f"\nDocument (embedding_text):\n{document}")
    else:
        print(f"\nDocument:\n{document[:300]}...")


# -----------------------------
# BROWSING METHODS
# -----------------------------
def list_all_entries(limit: Optional[int] = None, verbose: bool = False) -> List[Dict]:
    """
    List all entries in the collection.
    
    Args:
        limit: Maximum number of entries to return (None for all)
        verbose: If True, print formatted entries
    
    Returns:
        List of entry dictionaries
    """
    results = collection.get(limit=limit)
    
    entries = [
        _format_entry(entry_id, doc, meta)
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    
    if verbose:
        print(f"\n📊 Found {len(entries)} entries in collection '{COLLECTION_NAME}':\n")
        for idx, entry in enumerate(entries, 1):
            _print_entry(entry, idx)
    
    return entries


def list_entries_by_video_id(video_id: str, verbose: bool = False, full: bool = False) -> List[Dict]:
    """
    List all entries for a specific video ID.
    
    Args:
        video_id: YouTube video ID to filter by
        verbose: If True, print formatted entries
        full: If True, show complete data without truncation (only used if verbose=True)
    
    Returns:
        List of entry dictionaries matching the video_id
    """
    results = collection.get(
        where={"video_id": video_id}
    )
    
    entries = [
        _format_entry(entry_id, doc, meta)
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    
    if verbose:
        print(f"\n📹 Found {len(entries)} entries for video ID '{video_id}':\n")
        for idx, entry in enumerate(entries, 1):
            _print_entry(entry, idx, full=full)
    
    return entries


def list_entries_by_playlist_id(playlist_id: str, verbose: bool = False) -> List[Dict]:
    """
    List all entries for a specific playlist ID.
    
    Args:
        playlist_id: YouTube playlist ID to filter by
        verbose: If True, print formatted entries
    
    Returns:
        List of entry dictionaries matching the playlist_id
    """
    results = collection.get(
        where={"playlist_id": playlist_id}
    )
    
    entries = [
        _format_entry(entry_id, doc, meta)
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    
    if verbose:
        print(f"\n📚 Found {len(entries)} entries for playlist ID '{playlist_id}':\n")
        for idx, entry in enumerate(entries, 1):
            _print_entry(entry, idx)
    
    return entries


def list_entries_by_type(entry_type: str, verbose: bool = False) -> List[Dict]:
    """
    List all entries of a specific type.
    
    Args:
        entry_type: Either "video_context" or "timestamp_section"
        verbose: If True, print formatted entries
    
    Returns:
        List of entry dictionaries matching the type
    """
    if entry_type not in ["video_context", "timestamp_section"]:
        raise ValueError(f"entry_type must be 'video_context' or 'timestamp_section', got '{entry_type}'")
    
    results = collection.get(
        where={"type": entry_type}
    )
    
    entries = [
        _format_entry(entry_id, doc, meta)
        for entry_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]
    
    if verbose:
        print(f"\n🏷️  Found {len(entries)} entries of type '{entry_type}':\n")
        for idx, entry in enumerate(entries, 1):
            _print_entry(entry, idx)
    
    return entries


def list_video_summaries(verbose: bool = False) -> List[Dict]:
    """
    List all video-level entries (video summaries).
    
    Args:
        verbose: If True, print formatted entries
    
    Returns:
        List of video-level entry dictionaries
    """
    return list_entries_by_type("video_context", verbose=verbose)


def list_timestamp_sections(video_id: Optional[str] = None, verbose: bool = False) -> List[Dict]:
    """
    List all timestamp section entries, optionally filtered by video_id.
    
    Args:
        video_id: Optional video ID to filter by
        verbose: If True, print formatted entries
    
    Returns:
        List of timestamp section entry dictionaries
    """
    if video_id:
        all_entries = list_entries_by_video_id(video_id, verbose=False)
        entries = [e for e in all_entries if e["metadata"].get("type") == "timestamp_section"]
    else:
        entries = list_entries_by_type("timestamp_section", verbose=False)
    
    if verbose:
        print(f"\n⏱️  Found {len(entries)} timestamp sections" + 
              (f" for video ID '{video_id}'" if video_id else "") + ":\n")
        for idx, entry in enumerate(entries, 1):
            _print_entry(entry, idx)
    
    return entries


def get_entry_by_id(entry_id: str, verbose: bool = False) -> Optional[Dict]:
    """
    Get a specific entry by its ChromaDB ID.
    
    Args:
        entry_id: ChromaDB entry ID
        verbose: If True, print formatted entry
    
    Returns:
        Entry dictionary if found, None otherwise
    """
    try:
        results = collection.get(ids=[entry_id])
        if not results["ids"]:
            if verbose:
                print(f"⚠️  Entry with ID '{entry_id}' not found")
            return None
        
        entry = _format_entry(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0]
        )
        
        if verbose:
            print(f"\n🔍 Entry found:\n")
            _print_entry(entry)
        
        return entry
    except Exception as e:
        if verbose:
            print(f"❌ Error retrieving entry '{entry_id}': {e}")
        return None


def search_by_text(query_text: str, limit: int = 10, verbose: bool = False) -> List[Dict]:
    """
    Perform semantic search using query text.
    
    Args:
        query_text: Natural language query text
        limit: Maximum number of results to return
        verbose: If True, print formatted results with distances
    
    Returns:
        List of entry dictionaries with similarity scores
    """
    query_embedding = get_embedding(query_text)
    
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=limit
    )
    
    entries = []
    for idx, entry_id in enumerate(results["ids"][0]):
        entry = _format_entry(
            entry_id,
            results["documents"][0][idx],
            results["metadatas"][0][idx]
        )
        # Add distance/similarity score
        if "distances" in results and results["distances"][0]:
            entry["distance"] = results["distances"][0][idx]
        entries.append(entry)
    
    if verbose:
        print(f"\n🔎 Semantic search for '{query_text}' returned {len(entries)} results:\n")
        for idx, entry in enumerate(entries, 1):
            if "distance" in entry:
                print(f"[{idx}] Distance: {entry['distance']:.4f}")
            _print_entry(entry, idx if "distance" not in entry else None)
    
    return entries


def get_collection_stats(verbose: bool = True) -> Dict[str, Any]:
    """
    Get statistics about the collection.
    
    Args:
        verbose: If True, print formatted statistics
    
    Returns:
        Dictionary with collection statistics
    """
    all_entries = list_all_entries(verbose=False)
    
    # Count by type
    type_counts = {}
    video_ids = set()
    playlist_ids = set()
    chakra_counts = {}
    
    for entry in all_entries:
        metadata = entry["metadata"]
        entry_type = metadata.get("type", "unknown")
        type_counts[entry_type] = type_counts.get(entry_type, 0) + 1
        
        if "video_id" in metadata:
            video_ids.add(metadata["video_id"])
        if "playlist_id" in metadata:
            playlist_ids.add(metadata["playlist_id"])
        if "chakra" in metadata:
            chakra = metadata["chakra"]
            chakra_counts[chakra] = chakra_counts.get(chakra, 0) + 1
    
    stats = {
        "total_entries": len(all_entries),
        "type_counts": type_counts,
        "unique_videos": len(video_ids),
        "unique_playlists": len(playlist_ids),
        "chakra_distribution": chakra_counts,
        "collection_name": COLLECTION_NAME,
        "persist_directory": CHROMA_DIR
    }
    
    if verbose:
        print(f"\n📈 Collection Statistics for '{COLLECTION_NAME}':\n")
        print(f"{'='*80}")
        print(f"Total Entries: {stats['total_entries']}")
        print(f"Unique Videos: {stats['unique_videos']}")
        print(f"Unique Playlists: {stats['unique_playlists']}")
        print(f"\nEntries by Type:")
        for entry_type, count in stats['type_counts'].items():
            print(f"  - {entry_type}: {count}")
        print(f"\nChakra Distribution:")
        for chakra, count in sorted(stats['chakra_distribution'].items(), key=lambda x: -x[1]):
            print(f"  - {chakra}: {count}")
        print(f"{'='*80}\n")
    
    return stats


def clear_collection(confirm: bool = False, verbose: bool = True) -> bool:
    """
    Clear all entries from the collection.
    
    Args:
        confirm: If True, skip confirmation prompt and delete immediately
        verbose: If True, print status messages
    
    Returns:
        True if deletion was successful, False if cancelled
    """
    # Get current count
    results = collection.get()
    count = len(results["ids"])
    
    if count == 0:
        if verbose:
            print("ℹ️  Collection is already empty")
        return True
    
    if verbose:
        print(f"\n⚠️  WARNING: This will delete ALL {count} entries from the collection '{COLLECTION_NAME}'")
        print("   This action cannot be undone!\n")
    
    if not confirm:
        try:
            response = input("Are you sure you want to proceed? Type 'yes' to confirm: ").strip().lower()
            if response != "yes":
                if verbose:
                    print("❌ Deletion cancelled")
                return False
        except (KeyboardInterrupt, EOFError):
            if verbose:
                print("\n❌ Deletion cancelled")
            return False
    
    try:
        # Delete all entries by their IDs
        collection.delete(ids=results["ids"])
        
        if verbose:
            print(f"✅ Successfully deleted {count} entries from collection '{COLLECTION_NAME}'")
        
        return True
    except Exception as e:
        if verbose:
            print(f"❌ Error deleting entries: {e}")
        return False


def _delete_video_from_audit_csv(video_id: str, dry_run: bool = True, verbose: bool = True) -> Dict[str, Any]:
    """
    Delete (or preview deletion of) audit CSV rows matching a video_id.

    Args:
        video_id: YouTube video ID
        dry_run: If True, only report counts without modifying file
        verbose: If True, print status messages

    Returns:
        Dict with file existence and row counts
    """
    result = {
        "file_exists": False,
        "total_rows": 0,
        "matching_rows": 0,
        "remaining_rows": 0,
        "path": AUDIT_CSV_PATH
    }

    if not os.path.exists(AUDIT_CSV_PATH):
        if verbose:
            print(f"ℹ️  Audit CSV not found at: {AUDIT_CSV_PATH}")
        return result

    result["file_exists"] = True

    with open(AUDIT_CSV_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if not fieldnames:
        if verbose:
            print(f"⚠️  Audit CSV has no header: {AUDIT_CSV_PATH}")
        return result

    if "video_id" not in fieldnames:
        if verbose:
            print("⚠️  Audit CSV does not contain 'video_id' column. Skipping CSV cleanup.")
        return result

    matching_rows = [r for r in rows if r.get("video_id") == video_id]
    remaining_rows = [r for r in rows if r.get("video_id") != video_id]

    result["total_rows"] = len(rows)
    result["matching_rows"] = len(matching_rows)
    result["remaining_rows"] = len(remaining_rows)

    if dry_run:
        if verbose:
            print(
                f"🧪 Dry-run CSV: would remove {result['matching_rows']} row(s) "
                f"for video_id '{video_id}' from {AUDIT_CSV_PATH}"
            )
        return result

    if result["matching_rows"] == 0:
        if verbose:
            print("ℹ️  No matching rows found in audit CSV.")
        return result

    with open(AUDIT_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(remaining_rows)

    if verbose:
        print(
            f"✅ Audit CSV updated: removed {result['matching_rows']} row(s), "
            f"remaining {result['remaining_rows']}"
        )
    return result


def delete_entries_by_video_id(
    video_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    verbose: bool = True
) -> bool:
    """
    Delete all Chroma entries and audit CSV rows for a specific video_id.

    Safety model:
    - Default dry_run=True (no deletion)
    - Actual deletion requires dry_run=False and confirm=True
    """
    entries = list_entries_by_video_id(video_id, verbose=False)
    entry_ids = [e["id"] for e in entries]

    type_counts: Dict[str, int] = {}
    for e in entries:
        t = e["metadata"].get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    if verbose:
        print(f"\n🎯 Target video_id: {video_id}")
        print(f"📦 Matching Chroma entries: {len(entry_ids)}")
        if type_counts:
            print("   Type breakdown:")
            for t, c in sorted(type_counts.items()):
                print(f"   - {t}: {c}")
        if entry_ids:
            sample = entry_ids[:5]
            print(f"   Sample IDs: {sample}" + (" ..." if len(entry_ids) > 5 else ""))

    csv_result = _delete_video_from_audit_csv(video_id, dry_run=dry_run, verbose=verbose)

    if dry_run:
        if verbose:
            print("\n🧪 Dry-run mode only: no Chroma entries were deleted.")
            print("   Re-run with --yes to perform deletion.")
        return True

    if not confirm:
        if verbose:
            print("❌ Deletion cancelled. Use --yes to confirm deletion.")
        return False

    # Perform actual deletion
    if entry_ids:
        collection.delete(ids=entry_ids)
        if verbose:
            print(f"✅ Deleted {len(entry_ids)} Chroma entry(ies) for video_id '{video_id}'")
    else:
        if verbose:
            print("ℹ️  No matching Chroma entries to delete.")

    # CSV is already updated when dry_run=False
    if verbose and csv_result["file_exists"] and csv_result["matching_rows"] == 0:
        print("ℹ️  No matching audit CSV rows to delete.")

    return True


# -----------------------------
# CLI / TESTING
# -----------------------------
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Video ChromaDB Browsing Utility")
        print("\nUsage:")
        print("  python browse_videos.py stats                    # Show collection statistics")
        print("  python browse_videos.py all [limit]              # List all entries")
        print("  python browse_videos.py video <video_id> [--full]  # List entries for video (--full for complete data)")
        print("  python browse_videos.py playlist <playlist_id>   # List entries for playlist")
        print("  python browse_videos.py type <type>              # List entries by type")
        print("  python browse_videos.py summaries                # List video summaries")
        print("  python browse_videos.py timestamps [video_id]    # List timestamp sections")
        print("  python browse_videos.py get <entry_id>           # Get specific entry")
        print("  python browse_videos.py search <query> [limit]   # Semantic search")
        print("  python browse_videos.py delete-video <video_id> [--dry-run] [--yes]  # Delete one video's entries + audit rows")
        print("  python browse_videos.py clear [--yes]            # Clear all entries (DESTRUCTIVE)")
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    try:
        if command == "stats":
            get_collection_stats(verbose=True)
        
        elif command == "all":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
            list_all_entries(limit=limit, verbose=True)
        
        elif command == "video":
            if len(sys.argv) < 3:
                print("❌ Error: video_id required")
                sys.exit(1)
            video_id = sys.argv[2]
            full_mode = "--full" in sys.argv or "-f" in sys.argv
            list_entries_by_video_id(video_id, verbose=True, full=full_mode)
        
        elif command == "playlist":
            if len(sys.argv) < 3:
                print("❌ Error: playlist_id required")
                sys.exit(1)
            list_entries_by_playlist_id(sys.argv[2], verbose=True)
        
        elif command == "type":
            if len(sys.argv) < 3:
                print("❌ Error: type required (video_context or timestamp_section)")
                sys.exit(1)
            list_entries_by_type(sys.argv[2], verbose=True)
        
        elif command == "summaries":
            list_video_summaries(verbose=True)
        
        elif command == "timestamps":
            video_id = sys.argv[2] if len(sys.argv) > 2 else None
            list_timestamp_sections(video_id=video_id, verbose=True)
        
        elif command == "get":
            if len(sys.argv) < 3:
                print("❌ Error: entry_id required")
                sys.exit(1)
            get_entry_by_id(sys.argv[2], verbose=True)
        
        elif command == "search":
            if len(sys.argv) < 3:
                print("❌ Error: search query required")
                sys.exit(1)
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
            search_by_text(sys.argv[2], limit=limit, verbose=True)
        
        elif command == "clear":
            confirm = "--yes" in sys.argv or "-y" in sys.argv
            success = clear_collection(confirm=confirm, verbose=True)
            sys.exit(0 if success else 1)

        elif command == "delete-video":
            if len(sys.argv) < 3:
                print("❌ Error: video_id required")
                print("   Usage: python browse_videos.py delete-video <video_id> [--dry-run] [--yes]")
                sys.exit(1)

            video_id = sys.argv[2]
            confirm = "--yes" in sys.argv or "-y" in sys.argv

            # Safety default: dry-run unless --yes is provided.
            # --dry-run is accepted explicitly and always keeps non-destructive behavior.
            explicit_dry_run = "--dry-run" in sys.argv or "--dryrun" in sys.argv
            dry_run = True if explicit_dry_run else (not confirm)

            success = delete_entries_by_video_id(
                video_id=video_id,
                dry_run=dry_run,
                confirm=confirm,
                verbose=True
            )
            sys.exit(0 if success else 1)
        
        else:
            print(f"❌ Unknown command: {command}")
            sys.exit(1)
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

