try:
    import faiss
except ImportError:
    raise ImportError(
        "faiss is not installed. Please install it using:\n"
        "  pip install faiss-cpu  # For CPU-only usage\n"
        "  or\n"
        "  pip install faiss-gpu  # For GPU support"
    )
import numpy as np

def build_faiss_index(all_embeddings):
    d = len(all_embeddings[0])
    index = faiss.IndexFlatL2(d)
    index.add(np.array(all_embeddings).astype('float32'))
    return index

def faiss_search(index, query_embedding, top_k=10):
    D, I = index.search(np.array([query_embedding]).astype('float32'), top_k)
    return D[0], I[0]
