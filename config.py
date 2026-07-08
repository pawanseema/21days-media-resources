import os

AUDIT_CSV_FILENAME = "sahajyoga_recent5_audit.csv"


def _get_project_root():
    """Get the project root directory (where config.py is located)."""
    return os.path.dirname(os.path.abspath(__file__))


def _read_key_file(full_path, label):
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"⚠️ {label} file not found: {full_path}")
        return None


def load_yt_api_key(file_path="api_key.txt"):
    """Load YouTube API key from env (YOUTUBE_API_KEY) or local file."""
    env_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if env_key:
        return env_key

    project_root = _get_project_root()
    full_path = os.path.join(project_root, file_path)
    key = _read_key_file(full_path, "YouTube API key")
    if key is None:
        print("   Set YOUTUBE_API_KEY or create 'api_key.txt' with your YouTube API key.")
    return key


def load_openai_api_key(file_path="openai_api_key.txt"):
    """Load OpenAI API key from env (OPENAI_API_KEY) or local file."""
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    project_root = _get_project_root()
    full_path = os.path.join(project_root, file_path)
    key = _read_key_file(full_path, "OpenAI API key")
    if key is None:
        print("   Set OPENAI_API_KEY or create 'openai_api_key.txt' with your OpenAI API key.")
    return key


def get_chroma_dir():
    """Chroma persist directory: CHROMA_PERSIST_DIR env or resources/chroma_free_store."""
    override = os.environ.get("CHROMA_PERSIST_DIR", "").strip()
    if override:
        return override

    project_root = _get_project_root()
    return os.path.join(project_root, "resources", "chroma_free_store")


def get_audit_csv_path():
    """Audit CSV path: under CHROMA_PERSIST_DIR when set, else project root."""
    override = os.environ.get("CHROMA_PERSIST_DIR", "").strip()
    if override:
        return os.path.join(override, AUDIT_CSV_FILENAME)

    return os.path.join(_get_project_root(), AUDIT_CSV_FILENAME)
