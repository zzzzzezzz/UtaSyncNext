from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass


_TIMELINE_RE = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3}\s+-->")
_METADATA_RE = re.compile(
    r"^(?:词|詞|曲|作词|作詞|作曲|编曲|編曲|制作人|歌手|artist|lyrics|music|producer)\s*[:：]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LyricsSelection:
    text: str
    selected_titles: tuple[str, ...]
    total_sections: int

    @property
    def matched(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True)
class LyricsSection:
    title: str
    text: str
    lyric_lines: tuple[str, ...]
    match_text: str


def select_lyrics_context(reference_lyrics: str, batch_srt: str, previous_srt: str = "") -> LyricsSelection:
    """Return only high-confidence song sections relevant to the current rough transcript."""
    source = str(reference_lyrics or "").strip()
    if not source:
        return LyricsSelection("", (), 0)

    sections = parse_lyrics_sections(source)
    if len(sections) <= 1:
        title = sections[0].title if sections else ""
        return LyricsSelection(source, (title,) if title else (), len(sections))

    query = _normalise_for_match(_srt_payload(batch_srt) + "\n" + _srt_payload(previous_srt))
    if len(query) < 5:
        return LyricsSelection("", (), len(sections))

    ranked: list[tuple[int, LyricsSection]] = []
    for section in sections:
        match = difflib.SequenceMatcher(None, query, section.match_text, autojunk=False).find_longest_match()
        ranked.append((match.size, section))
    ranked.sort(key=lambda item: item[0], reverse=True)

    best_score, best = ranked[0]
    min_match = 7 if len(query) >= 20 else 5
    if best_score < min_match:
        return LyricsSelection("", (), len(sections))

    selected = [best]
    if len(ranked) > 1:
        second_score, second = ranked[1]
        if second_score >= min_match and second_score * 10 >= best_score * 7:
            selected.append(second)

    return LyricsSelection(
        text="\n\n".join(section.text for section in selected),
        selected_titles=tuple(section.title for section in selected),
        total_sections=len(sections),
    )


def parse_lyrics_sections(reference_lyrics: str) -> list[LyricsSection]:
    raw_groups = [group.strip() for group in re.split(r"\n\s*\n", reference_lyrics) if group.strip()]
    groups: list[list[str]] = []
    for group in raw_groups:
        lines = [line.strip() for line in group.splitlines() if line.strip()]
        if not lines:
            continue
        if _looks_like_title(lines[0]) or not groups:
            groups.append(lines)
        else:
            # Users often leave visual spacing between title, credits and lyrics.
            # A new song starts only when the next group has a title-like first line.
            groups[-1].extend(lines)

    sections: list[LyricsSection] = []
    for lines in groups:
        title = lines[0] if _looks_like_title(lines[0]) else ""
        lyric_lines = [line for line in lines[1 if title else 0 :] if not _METADATA_RE.match(line)]
        match_text = _normalise_for_match("\n".join(lyric_lines))
        if len(match_text) < 5:
            match_text = _normalise_for_match("\n".join(lines))
        sections.append(
            LyricsSection(
                title=title,
                text="\n".join(lines),
                lyric_lines=tuple(lyric_lines),
                match_text=match_text,
            )
        )
    return sections


def _looks_like_title(line: str) -> bool:
    return bool(re.search(r"\s[-–—]\s", line) or ("(" in line and ")" in line))


def _srt_payload(content: str) -> str:
    result: list[str] = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or line.isdigit() or _TIMELINE_RE.match(line):
            continue
        result.append(line)
    return "\n".join(result)


def _normalise_for_match(value: str) -> str:
    normalised = unicodedata.normalize("NFKC", value).lower()
    return "".join(char for char in normalised if char.isalnum() or "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff")
