import os

def _get_project_root():
    """Get the project root directory (where config.py is located)"""
    return os.path.dirname(os.path.abspath(__file__))

def load_yt_api_key(file_path="api_key.txt"):
    """Load YouTube API key from local file in project root"""
    project_root = _get_project_root()
    full_path = os.path.join(project_root, file_path)
    
    try:
        with open(full_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"⚠️ API key file not found: {full_path}")
        print("   Please create a file named 'api_key.txt' with your YouTube API key.")
        return None

def load_openai_api_key(file_path="openai_api_key.txt"):
    """Load OpenAI API key from local file in project root"""
    project_root = _get_project_root()
    full_path = os.path.join(project_root, file_path)
    
    try:
        with open(full_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"⚠️ OpenAI API key file not found: {full_path}")
        print("   Please create a file named 'openai_api_key.txt' with your OpenAI API key.")
        return None

def get_chroma_dir():
    """Get the ChromaDB directory path (standard location: resources/chroma_free_store)"""
    project_root = _get_project_root()
    return os.path.join(project_root, "resources", "chroma_free_store")