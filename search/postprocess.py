def convert_ts(timestamp_str: str) -> int:
    """
    Convert timestamp string (e.g., "1:23:45" or "5:30") to seconds.
    
    Args:
        timestamp_str: Timestamp string in format "H:MM:SS" or "M:SS"
    
    Returns:
        Total seconds as integer
    """
    if not timestamp_str:
        return 0
    
    parts = timestamp_str.split(":")
    try:
        if len(parts) == 3:  # H:MM:SS format
            hours, minutes, seconds = map(int, parts)
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:  # M:SS format
            minutes, seconds = map(int, parts)
            return minutes * 60 + seconds
        else:
            return 0
    except (ValueError, AttributeError):
        return 0


def fused_rank_score(embedding_distance, keyword_overlap, llm_rank_position):
    """
    Combines three ranking signals:
    - embedding similarity (distance)
    - keyword overlap ratio
    - LLM rank (0 = best)
    """
    emb_score = max(0, 1 - embedding_distance)
    llm_score = 1 / (1 + llm_rank_position)

    final = (0.55 * emb_score) + (0.25 * keyword_overlap) + (0.20 * llm_score)
    return round(final, 4)

def explain_ranking(user_query, item, distance, keyword_overlap, llm_position):
    return {
        "explanation": (
            f"Matched because: "
            f"• semantic similarity = {round(1-distance,3)} "
            f"• keyword overlap = {round(keyword_overlap,3)} "
            f"• LLM relevance rank = {llm_position} "
            f"• section covers: {item['meta'].get('section_title','')}"
        )
    }

def merge_adjacent_sections(results):
    merged = []
    buffer = []

    for r in results:
        if not buffer:
            buffer.append(r)
            continue

        prev_ts = buffer[-1]["timestamp"]
        curr_ts = r["timestamp"]

        # group if within 6 minutes
        if abs(convert_ts(curr_ts) - convert_ts(prev_ts)) <= 360:
            buffer.append(r)
        else:
            merged.append(buffer)
            buffer = [r]

    if buffer:
        merged.append(buffer)

    return merged

def to_ui_card(result):
    return {
        "title": result["section_title"],
        "video": result["video_title"],
        "timestamp": result["timestamp"],
        "summary": result["summary"],
        "chakra": result["chakra"],
        "quote": result["quote"],
        "url": result["url"],
        "confidence": result["confidence"],
        "explanation": result["explanation"],
    }

