import sys
import os
from flask import Flask, request, jsonify, redirect, send_from_directory

# Add parent directory to path to import from search module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search.video_search import search_video_sections, recommend_related, list_video_chapters
from resources.resource_ingestion import ingest_resource, get_resource_by_id, update_resource
from search.resource_search import search_resources
from resources.video_processing import process_video_by_id
from api.live_sessions import (
    is_transient_youtube_error,
    resolve_next_session,
    resolve_recent_recordings,
)
from api.year_recordings import resolve_year_recordings
from api.wisdom_topics import load_wisdom_topics

app = Flask(__name__)

# Get the project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(PROJECT_ROOT, 'ui')

# Flutter web (Chrome) calls this API cross-origin; without CORS the browser
# blocks reading 200 responses even though Flask logs success.
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, X-Admin-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
    return response


@app.before_request
def handle_cors_preflight():
    if request.method == "OPTIONS":
        return ("", 204)
    return None


ADMIN_PROTECTED_ROUTES = {
    ("POST", "/api/resources/ingest"),
    ("POST", "/api/videos/ingest"),
}

# HTML admin tools for handout ingest/update (local only when Chroma writes disabled).
RESOURCE_ADMIN_HTML = {
    "resource_form.html",
    "resource_update.html",
}


def _env_flag(name, default=False):
    """Parse a boolean-ish environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _youtube_error_response(exc):
    """Map YouTube timeouts / SSL blips to 503 so clients can retry."""
    transient = is_transient_youtube_error(exc)
    return jsonify({
        "error": "Service temporarily unavailable" if transient else "Internal server error",
        "message": str(exc),
    }), 503 if transient else 500


def _chroma_writes_enabled():
    """
    When false (Cloud Run default), block routes that mutate Chroma.
    Local ingest stays available by leaving ENABLE_CHROMA_WRITES unset/true.
    """
    return _env_flag("ENABLE_CHROMA_WRITES", default=True)


def _route_requires_admin_auth():
    """Return True for mutating admin-only HTTP routes."""
    if (request.method, request.path) in ADMIN_PROTECTED_ROUTES:
        return True
    if request.method == "PUT" and request.path.startswith("/api/resources/"):
        return True
    return False


def _route_mutates_chroma():
    """Return True for HTTP routes that write to Chroma."""
    return _route_requires_admin_auth()


def _chroma_writes_forbidden_response():
    return jsonify({
        "error": "Forbidden",
    }), 403


@app.before_request
def require_admin_key():
    """
    Block Chroma-mutating routes when ENABLE_CHROMA_WRITES is false.

    When writes are enabled and ADMIN_API_KEY is set, require X-Admin-Key on
    those same routes. Unset ADMIN_API_KEY locally so HTML forms keep working.
    """
    if _route_mutates_chroma() and not _chroma_writes_enabled():
        return _chroma_writes_forbidden_response()

    admin_key = os.environ.get("ADMIN_API_KEY", "").strip()
    if not admin_key or not _route_requires_admin_auth():
        return None

    provided = request.headers.get("X-Admin-Key", "").strip()
    if provided != admin_key:
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.route("/api/ui-config", methods=["GET"])
def api_ui_config():
    """
    Public UI flags for the frontend.

    SHOW_RESULT_DEBUG: when true, video cards show timestamp, confidence, hashtags;
    resource cards show file type and confidence. Default true when unset (local
    dev); Cloud Run deploy sets SHOW_RESULT_DEBUG=false.

    ENABLE_MORE_LIKE_THIS: when true, UI may show More like this and related API
    is enabled. Default true (rollout on); set false to disable.

    ENABLE_CHROMA_WRITES: when true, resource/video ingest-update APIs and admin
    HTML forms are allowed. Cloud Run sets false (read-only Chroma); local default true.
    """
    return jsonify({
        "showResultDebug": _env_flag("SHOW_RESULT_DEBUG", default=True),
        "enableMoreLikeThis": _env_flag("ENABLE_MORE_LIKE_THIS", default=True),
        "enableChromaWrites": _chroma_writes_enabled(),
    }), 200


@app.route("/api/videos/related", methods=["POST"])
def api_videos_related():
    """
    Return topic-similar timestamp sections for a seed clip.

    Disabled unless ENABLE_MORE_LIKE_THIS is true.
    Body: { "video_id", "timestamp", "top_k"? } or { "id": "<chroma_id>", "top_k"? }
    """
    if not _env_flag("ENABLE_MORE_LIKE_THIS", default=True):
        return jsonify({
            "error": "More like this is disabled",
            "enableMoreLikeThis": False,
        }), 404

    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json() or {}
        chroma_id = str(data.get("id") or data.get("chroma_id") or "").strip() or None
        video_id = str(data.get("video_id") or "").strip() or None
        timestamp = str(data.get("timestamp") or "").strip() or None
        top_k = data.get("top_k", 5)
        if not isinstance(top_k, int) or top_k < 1:
            top_k = 5

        if not chroma_id and not (video_id and timestamp):
            return jsonify({
                "error": "Provide id (chroma id), or both video_id and timestamp",
            }), 400

        payload = recommend_related(
            video_id=video_id,
            timestamp=timestamp,
            chroma_id=chroma_id,
            top_k=top_k,
        )
        results = payload.get("results") if isinstance(payload, dict) else payload
        seed = payload.get("seed") if isinstance(payload, dict) else None
        return jsonify({
            "seed": seed,
            "results": results or [],
            "count": len(results or []),
        }), 200

    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            return jsonify({"error": msg}), 404
        return jsonify({"error": msg}), 400
    except Exception as e:
        print(f"Error in videos related endpoint: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "message": str(e),
        }), 500


@app.route("/api/videos/<video_id>/chapters", methods=["GET"])
def api_video_chapters(video_id):
    """
    Timestamp chapters for one video from Chroma (timestamp_section rows).

    Returns empty chapters[] when the video has not been ingested. No YouTube calls.
    """
    try:
        return jsonify(list_video_chapters(video_id)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error in video chapters endpoint: {e}", flush=True)
        return jsonify({
            "error": "Internal server error",
            "message": str(e),
        }), 500


@app.route("/search", methods=["POST"])
def api_search():
    """
    Search endpoint that accepts a query and returns video section results.
    
    Expected JSON payload:
    {
        "query": "your search query here",
        "top_k": 3  # optional, defaults to 3
    }
    """
    try:
        # Validate request has JSON data
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        
        # Validate query exists
        if not data or "query" not in data:
            return jsonify({"error": "Missing required field: 'query'"}), 400
        
        query = data.get("query", "").strip()
        if not query:
            return jsonify({"error": "Query cannot be empty"}), 400
        
        # Get optional top_k parameter
        top_k = data.get("top_k", 3)
        if not isinstance(top_k, int) or top_k < 1:
            top_k = 3
        
        # Perform search
        results = search_video_sections(query, top_k=top_k)
        
        return jsonify({
            "query": query,
            "results": results,
            "count": len(results)
        })
    
    except Exception as e:
        # Log error in production (use proper logging)
        print(f"Error in search endpoint: {e}", flush=True)
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy"}), 200


@app.route("/api/live/sessions", methods=["GET"])
def api_live_sessions():
    """
    Live or next upcoming YouTube meditation session for the 21Days app.

    Checks configured channels for an active live stream; otherwise returns the
    soonest upcoming scheduled live within 72h (wall time). session is null when
    neither is found. Zoom URL and channel metadata come from config/live_sessions.json
    (override path with LIVE_SESSIONS_CONFIG).
    """
    try:
        return jsonify(resolve_next_session()), 200
    except Exception as e:
        print(f"Error in live sessions endpoint: {e}", flush=True)
        return _youtube_error_response(e)


@app.route("/api/live/recent", methods=["GET"])
def api_live_recent():
    """
    Latest completed livestream per configured channel (within ~72h).

    At most one item per channel. Used by the mobile Live tab recent section.
    """
    try:
        return jsonify(resolve_recent_recordings()), 200
    except Exception as e:
        print(f"Error in live recent endpoint: {e}", flush=True)
        return _youtube_error_response(e)


@app.route("/api/recordings", methods=["GET"])
def api_recordings():
    """
    Latest year playlist sliced into configured sessions (oldest videos first).
    """
    try:
        return jsonify(resolve_year_recordings()), 200
    except Exception as e:
        print(f"Error in recordings endpoint: {e}", flush=True)
        return _youtube_error_response(e)


@app.route("/api/wisdom/topics", methods=["GET"])
def api_wisdom_topics():
    """
    Static Sahaja Yoga topics for the Wisdom tab (web + mobile).

    Source: config/wisdom_topics.json (override with WISDOM_TOPICS_CONFIG).
    """
    try:
        return jsonify(load_wisdom_topics()), 200
    except FileNotFoundError:
        return jsonify({"error": "Wisdom topics not configured"}), 404
    except Exception as e:
        print(f"Error in wisdom topics endpoint: {e}", flush=True)
        return jsonify({
            "error": "Internal server error",
            "message": str(e),
        }), 500


@app.route("/api/resources/ingest", methods=["POST"])
def api_ingest_resource():
    """
    Ingest a single resource (handout) into ChromaDB.
    
    Expected JSON payload:
    {
        "title": "Resource title",
        "description": "Resource description",
        "topic": "Main topic",
        "tags": ["tag1", "tag2"],
        "download_url": "https://example.com/resource.pdf",
        "file_type": "pdf"
    }
    
    Returns:
        201 Created: Resource ingested successfully
        400 Bad Request: Validation error
        409 Conflict: Duplicate download_url
        500 Internal Server Error: Server error
    """
    try:
        # Validate request has JSON data
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Request body cannot be empty"}), 400
        
        # Ingest resource
        result = ingest_resource(data)
        
        return jsonify(result), 201
    
    except ValueError as e:
        # Validation error or duplicate URL
        error_msg = str(e)
        if "already exists" in error_msg:
            # Extract existing resource ID if available
            existing_id = error_msg.split(": ")[-1] if ": " in error_msg else None
            response = {
                "error": "Resource with download_url already exists",
                "download_url": data.get("download_url") if data else None
            }
            if existing_id:
                response["existing_resource_id"] = existing_id
            return jsonify(response), 409
        else:
            # Validation error
            return jsonify({"error": error_msg}), 400
    
    except Exception as e:
        # Log error in production (use proper logging)
        print(f"Error in resource ingestion endpoint: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500


@app.route("/api/videos/ingest", methods=["POST"])
def api_ingest_video():
    """
    Ingest a specific YouTube video into Chroma by video_id.

    Expected JSON payload:
    {
        "video_id": "1BTlbtXVMRg",
        "overwrite": false  # optional, default false
    }
    """
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.get_json()
        if not data or "video_id" not in data:
            return jsonify({"error": "Missing required field: 'video_id'"}), 400

        video_id = str(data.get("video_id", "")).strip()
        if not video_id:
            return jsonify({"error": "video_id cannot be empty"}), 400

        overwrite = bool(data.get("overwrite", False))
        result = process_video_by_id(video_id=video_id, overwrite=overwrite)
        return jsonify(result), 201

    except ValueError as e:
        msg = str(e)
        if "already exists in Chroma" in msg:
            return jsonify({"error": msg}), 409
        return jsonify({"error": msg}), 400
    except Exception as e:
        print(f"Error in video ingestion endpoint: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500

@app.route("/")
def index():
    """Serve the 4-tab web app (hash routes: #/live, #/explore, …)."""
    return send_from_directory(UI_DIR, 'app.html')


@app.route("/ui/<path:filename>")
def ui_static(filename):
    """Serve static assets from the ui directory (images, etc.)."""
    base = os.path.basename(filename)
    if base in RESOURCE_ADMIN_HTML and not _chroma_writes_enabled():
        return _chroma_writes_forbidden_response()
    return send_from_directory(UI_DIR, filename)

@app.route("/api/resources/search", methods=["POST"])
def api_search_resources():
    """
    Search endpoint for resources (handouts).
    
    Expected JSON payload:
    {
        "query": "your search query here",
        "top_k": 5  # optional, defaults to 5
    }
    """
    try:
        # Validate request has JSON data
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        
        # Validate query exists
        if not data or "query" not in data:
            return jsonify({"error": "Missing required field: 'query'"}), 400
        
        query = data.get("query", "").strip()
        if not query:
            return jsonify({"error": "Query cannot be empty"}), 400
        
        # Get optional top_k parameter
        top_k = data.get("top_k", 5)
        if not isinstance(top_k, int) or top_k < 1:
            top_k = 5
        
        # Perform search
        results = search_resources(query, top_k=top_k)
        
        return jsonify({
            "query": query,
            "results": results,
            "count": len(results)
        })
    
    except Exception as e:
        # Log error in production (use proper logging)
        print(f"Error in resource search endpoint: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500

@app.route("/resource-form")
@app.route("/resource_form.html")
def resource_form():
    """Serve the resource ingestion form page (local / writes-enabled only)."""
    if not _chroma_writes_enabled():
        return _chroma_writes_forbidden_response()
    return send_from_directory(UI_DIR, 'resource_form.html')

@app.route("/api/resources/<resource_id>", methods=["GET"])
def api_get_resource(resource_id):
    """
    Get a resource by its ID.
    
    Returns:
        200 OK: Resource data
        404 Not Found: Resource not found
        500 Internal Server Error: Server error
    """
    try:
        resource = get_resource_by_id(resource_id)
        return jsonify(resource), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    
    except Exception as e:
        print(f"Error in get resource endpoint: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500


@app.route("/api/resources/<resource_id>", methods=["PUT"])
def api_update_resource(resource_id):
    """
    Update an existing resource.
    
    Expected JSON payload:
    {
        "title": "Updated title",
        "description": "Updated description",
        "topic": "Updated topic",
        "tags": ["tag1", "tag2"],
        "download_url": "https://example.com/resource.pdf",
        "file_type": "pdf"
    }
    
    Returns:
        200 OK: Resource updated successfully
        400 Bad Request: Validation error
        404 Not Found: Resource not found
        409 Conflict: Duplicate download_url
        500 Internal Server Error: Server error
    """
    try:
        # Validate request has JSON data
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Request body cannot be empty"}), 400
        
        # Update resource
        result = update_resource(resource_id, data)
        
        return jsonify(result), 200
    
    except ValueError as e:
        # Validation error, resource not found, or duplicate URL
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return jsonify({"error": error_msg}), 404
        elif "already exists" in error_msg:
            # Extract existing resource ID if available
            existing_id = error_msg.split(": ")[-1] if ": " in error_msg else None
            response = {
                "error": "Resource with download_url already exists",
                "download_url": data.get("download_url") if data else None
            }
            if existing_id:
                response["existing_resource_id"] = existing_id
            return jsonify(response), 409
        else:
            # Validation error
            return jsonify({"error": error_msg}), 400
    
    except Exception as e:
        # Log error in production (use proper logging)
        print(f"Error in resource update endpoint: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500


@app.route("/resources")
@app.route("/resource-search")
@app.route("/resource_search.html")
def resource_search():
    """Handout search now lives on the Explore tab."""
    return redirect("/#/explore")

@app.route("/resource-update")
@app.route("/resource_update.html")
def resource_update():
    """Serve the resource update page (local / writes-enabled only)."""
    if not _chroma_writes_enabled():
        return _chroma_writes_forbidden_response()
    return send_from_directory(UI_DIR, 'resource_update.html')

if __name__ == "__main__":
    app.run(port=5005, debug=True)
