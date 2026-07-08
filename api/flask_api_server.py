import sys
import os
from flask import Flask, request, jsonify, send_from_directory

# Add parent directory to path to import from search module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search.video_search import search_video_sections
from resources.resource_ingestion import ingest_resource, get_resource_by_id, update_resource
from search.resource_search import search_resources
from resources.video_processing import process_video_by_id

app = Flask(__name__)

# Get the project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(PROJECT_ROOT, 'ui')

ADMIN_PROTECTED_ROUTES = {
    ("POST", "/api/resources/ingest"),
    ("POST", "/api/videos/ingest"),
}


def _route_requires_admin_auth():
    """Return True for mutating admin-only HTTP routes."""
    if (request.method, request.path) in ADMIN_PROTECTED_ROUTES:
        return True
    if request.method == "PUT" and request.path.startswith("/api/resources/"):
        return True
    return False


@app.before_request
def require_admin_key():
    """
    When ADMIN_API_KEY is set, require X-Admin-Key on mutating routes.
    Unset locally so dev workflows (Flask on 5005, HTML forms) stay unchanged.
    """
    admin_key = os.environ.get("ADMIN_API_KEY", "").strip()
    if not admin_key or not _route_requires_admin_auth():
        return None

    provided = request.headers.get("X-Admin-Key", "").strip()
    if provided != admin_key:
        return jsonify({"error": "Unauthorized"}), 401
    return None

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
    """Serve the search HTML page at the root URL."""
    return send_from_directory(UI_DIR, 'search.html')


@app.route("/ui/<path:filename>")
def ui_static(filename):
    """Serve static assets from the ui directory (images, etc.)."""
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
    """Serve the resource ingestion form page."""
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
    """Serve the resource search page."""
    return send_from_directory(UI_DIR, 'resource_search.html')

@app.route("/resource-update")
@app.route("/resource_update.html")
def resource_update():
    """Serve the resource update page."""
    return send_from_directory(UI_DIR, 'resource_update.html')

if __name__ == "__main__":
    app.run(port=5005, debug=True)
