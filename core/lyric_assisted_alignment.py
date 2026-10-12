from __future__ import annotations

import difflib
import unicodedata


def segment_records(segments: object, *, offset_seconds: float) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for segment in segments:
        words = [
            {
                "start": round(offset_seconds + float(word.start), 3),
                "end": round(offset_seconds + float(word.end), 3),
                "word": str(word.word).strip(),
                "probability": round(float(word.probability), 4),
            }
            for word in (getattr(segment, "words", None) or [])
        ]
        records.append(
            {
                "start": round(offset_seconds + float(segment.start), 3),
                "end": round(offset_seconds + float(segment.end), 3),
                "text": str(segment.text).strip(),
                "avg_logprob": round(float(getattr(segment, "avg_logprob", 0.0)), 4),
                "no_speech_prob": round(float(getattr(segment, "no_speech_prob", 0.0)), 4),
                "words": words,
            }
        )
    return records


def match_expected_lyric_lines(
    segments: list[dict[str, object]],
    expected_lines: list[str],
) -> list[dict[str, object]]:
    words = [
        dict(word)
        for segment in segments
        for word in (segment.get("words") or [])
        if isinstance(word, dict) and normalize_alignment_text(str(word.get("word") or ""))
    ]
    result: list[dict[str, object]] = []
    search_from = 0
    for line in expected_lines:
        expected_length = len(normalize_alignment_text(line))
        best: tuple[float, int, int, str] = (0.0, -1, -1, "")
        for start in range(search_from, len(words)):
            combined = ""
            for end in range(start, min(len(words), start + 28)):
                combined += str(words[end].get("word") or "")
                normalized_length = len(normalize_alignment_text(combined))
                if normalized_length > max(expected_length + 5, int(expected_length * 1.5)):
                    break
                score = alignment_similarity(combined, line)
                if score > best[0]:
                    best = (score, start, end, combined)
        score, start, end, detected = best
        accepted = score >= 0.72 and start >= 0
        selected_words = words[start : end + 1] if accepted else []
        probabilities = [float(word.get("probability") or 0.0) for word in selected_words]
        result.append(
            {
                "expected": line,
                "detected": detected,
                "score": round(score, 4),
                "start": float(words[start]["start"]) if accepted else None,
                "end": float(words[end]["end"]) if accepted else None,
                "accepted": accepted,
                "evidence": "word_timestamps",
                "average_word_probability": (
                    round(sum(probabilities) / len(probabilities), 4) if probabilities else 0.0
                ),
            }
        )
        if accepted:
            search_from = end + 1
    return result


def strict_alignment_rejection_reason(
    matches: list[dict[str, object]],
    expected_lines: list[str],
) -> str:
    accepted = [match for match in matches if bool(match.get("accepted"))]
    if len(expected_lines) < 3:
        return "fewer than three expected lyric lines"
    if len(accepted) != len(expected_lines):
        return "not all expected lyric lines were matched"
    if min(float(match.get("score") or 0.0) for match in accepted) < 0.90:
        return "text match is below the strict threshold"
    if min(float(match.get("average_word_probability") or 0.0) for match in accepted) < 0.75:
        return "word probability is below the strict threshold"
    for previous, current in zip(accepted, accepted[1:]):
        if float(current["start"]) < float(previous["end"]):
            return "aligned lyric lines overlap"
    return ""


def render_aligned_lyrics_srt(matches: list[dict[str, object]]) -> str:
    accepted = [match for match in matches if bool(match.get("accepted"))]
    blocks = [
        f"{index}\n{_timestamp(float(match['start']))} --> {_timestamp(float(match['end']))}\n{match['expected']}"
        for index, match in enumerate(accepted, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def alignment_similarity(left: str, right: str) -> float:
    left_normalized = normalize_alignment_text(left)
    right_normalized = normalize_alignment_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return difflib.SequenceMatcher(None, left_normalized, right_normalized, autojunk=False).ratio()


def normalize_alignment_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds * 1000)))
    hours, total = divmod(total, 3_600_000)
    minutes, total = divmod(total, 60_000)
    secs, milliseconds = divmod(total, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
