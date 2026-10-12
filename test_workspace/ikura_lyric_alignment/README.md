# ikura lyric-assisted alignment experiment

Status: research-only. No production subtitle was modified.

## Inputs

- Media: `F:\live人声分离实验\ikura.mp4`
- Optional comparison vocal: V3-generated separated vocal, read-only
- Lyrics: `test_assets/lyrics_refs/ikura_dingo_killing_voice_5songs.txt`
- Window: `00:00:00-00:00:45`
- Missing lines: first three lines of `百花繚乱`

## Result

The generic-prompt source probe matched `0/3` lines. The lyric-prompt source
probe matched all `3/3` lines after word-timestamp splitting:

| Line | Time | Text score | Average word probability |
| --- | --- | ---: | ---: |
| 1 | 20.340-23.120 | 1.000 | 0.818 |
| 2 | 23.860-26.540 | 1.000 | 0.972 |
| 3 | 26.540-32.220 | 1.000 | 0.880 |

The lyric-prompt separated-vocal probe also matched `3/3`, with boundaries
within roughly 0.7 seconds of the source probe. The prompt did not emit the
target lines before the actual vocal onset; pre-onset output was unrelated and
low-confidence. This is evidence against simple prompt repetition.

The full preview replaces only `00:30.000-00:32.000 宇宙から`. That source cue
had `no_speech_prob=0.8989` and a best expected-lyric similarity of `0.25`.
The production SRT remained unchanged.

## Recommended architecture

1. Detect a lyric opening gap using later monotonic lyric anchors.
2. Rescan only the local source-audio window with the missing lines as context,
   `word_timestamps=True`, VAD disabled, and no previous-text conditioning.
3. Split combined Whisper segments using word timestamps.
4. Generate a preview only when all expected lines are consecutive, every text
   score is at least `0.90`, and every average word probability is at least
   `0.75`.
5. Replace an overlapping source fragment only in the preview, and only when
   raw diagnostics show `no_speech_prob >= 0.85` and lyric similarity `< 0.45`.
6. Use separated vocals only as a fallback; source audio was sufficient here.

Do not integrate automatic production replacement until this policy passes
additional songs and negative controls. Model reuse is required: cold-loading
`large-v2` solely for a local rescan is too expensive.
