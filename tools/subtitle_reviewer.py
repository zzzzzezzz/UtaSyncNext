from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import webbrowser
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
)


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


@dataclass
class ReviewServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    url: str

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def is_alive(self) -> bool:
        return self.thread.is_alive()


def parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(path: Path | None) -> list[Cue]:
    if path is None:
        return []

    content = path.read_text(encoding="utf-8-sig", errors="replace")
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = [line.rstrip() for line in block.splitlines()]
        timing_index = next(
            (index for index, line in enumerate(lines) if TIMING_RE.search(line)),
            None,
        )
        if timing_index is None:
            continue

        match = TIMING_RE.search(lines[timing_index])
        if match is None:
            continue
        text = "\n".join(line.strip() for line in lines[timing_index + 1 :]).strip()
        cues.append(
            Cue(
                index=len(cues) + 1,
                start=parse_timestamp(match.group("start")),
                end=parse_timestamp(match.group("end")),
                text=text,
            )
        )
    return cues


def split_bilingual_cues(cues: list[Cue]) -> tuple[list[Cue], list[Cue]] | None:
    candidates: list[tuple[Cue, str, str]] = []
    for cue in cues:
        lines = [line.strip() for line in cue.text.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        source_text = lines[0]
        translated_text = "\n".join(lines[1:])
        if not re.search(r"[\u3040-\u30ff]", source_text):
            continue
        if not re.search(r"[\u3400-\u9fff]", translated_text):
            continue
        candidates.append((cue, source_text, translated_text))

    required = max(2, (len(cues) * 3 + 4) // 5)
    if len(candidates) < required:
        return None

    source_cues = [
        Cue(index=index, start=cue.start, end=cue.end, text=source_text)
        for index, (cue, source_text, _) in enumerate(candidates, start=1)
    ]
    translated_cues = [
        Cue(index=index, start=cue.start, end=cue.end, text=translated_text)
        for index, (cue, _, translated_text) in enumerate(candidates, start=1)
    ]
    return source_cues, translated_cues


def build_payload(media: Path, source_srt: Path | None, translated_srt: Path | None) -> dict:
    source_cues = parse_srt(source_srt)
    translated_cues = parse_srt(translated_srt)
    derived_bilingual = False
    if source_cues and not translated_cues:
        split = split_bilingual_cues(source_cues)
        if split is not None:
            source_cues, translated_cues = split
            derived_bilingual = True
    elif translated_cues and not source_cues:
        split = split_bilingual_cues(translated_cues)
        if split is not None:
            source_cues, translated_cues = split
            derived_bilingual = True

    return {
        "media_name": media.name,
        "source_name": source_srt.name if source_srt else "",
        "translated_name": translated_srt.name if translated_srt else "",
        "source_cues": [asdict(cue) for cue in source_cues],
        "translated_cues": [asdict(cue) for cue in translated_cues],
        "derived_bilingual": derived_bilingual,
    }


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UtaSync 字幕听轴校对</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090b0d;
      --surface: #12161a;
      --surface-2: #191f25;
      --line: #2b333b;
      --text: #f4f7f9;
      --muted: #9ba8b3;
      --source: #ffffff;
      --translated: #55d6be;
      --active: #17372f;
      --focus: #4ca6d8;
    }
    * { box-sizing: border-box; letter-spacing: 0; }
    html, body { height: 100%; margin: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
      font-size: 15px;
      overflow: hidden;
    }
    button, input { font: inherit; }
    header {
      height: 96px;
      padding: 14px 20px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
      display: grid;
      grid-template-columns: minmax(180px, 300px) minmax(360px, 1fr) auto;
      gap: 18px;
      align-items: center;
    }
    .identity { min-width: 0; }
    h1 { margin: 0 0 5px; font-size: 18px; }
    .filename {
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    audio { width: 100%; height: 42px; }
    .transport { display: flex; gap: 6px; align-items: center; }
    button {
      min-width: 46px;
      height: 34px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--surface-2);
      color: var(--text);
      cursor: pointer;
    }
    button:hover { border-color: var(--focus); }
    main {
      height: calc(100% - 96px);
      display: grid;
      grid-template-rows: 178px minmax(0, 1fr);
    }
    .now {
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      display: grid;
      grid-template-columns: 92px minmax(0, 1fr);
      gap: 10px 18px;
      align-content: center;
    }
    .now-label { color: var(--muted); text-align: right; padding-top: 3px; }
    .now-text {
      min-width: 0;
      font-size: 21px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    #nowSource { color: var(--source); }
    #nowTranslated { color: var(--translated); }
    .workspace { min-height: 0; display: grid; grid-template-rows: 54px minmax(0, 1fr); }
    .toolbar {
      padding: 10px 20px;
      display: flex;
      align-items: center;
      gap: 12px;
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }
    #clock { min-width: 116px; font-variant-numeric: tabular-nums; }
    #search {
      width: min(420px, 42vw);
      height: 34px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--bg);
      color: var(--text);
    }
    .follow { display: flex; align-items: center; gap: 7px; color: var(--muted); }
    .table-wrap { min-height: 0; overflow: auto; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    col.time { width: 112px; }
    col.source, col.translated { width: calc((100% - 112px) / 2); }
    thead { position: sticky; top: 0; z-index: 2; background: var(--surface-2); }
    th {
      padding: 10px 12px;
      text-align: left;
      color: var(--muted);
      border-bottom: 1px solid var(--line);
      font-weight: 600;
    }
    tbody tr { cursor: pointer; }
    tbody tr:hover { background: #131a20; }
    tbody tr.active { background: var(--active); box-shadow: inset 3px 0 var(--translated); }
    td {
      padding: 10px 12px;
      border-bottom: 1px solid #20262c;
      vertical-align: top;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    td:first-child {
      color: var(--muted);
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    td:nth-child(3) { color: var(--translated); }
    .empty { color: var(--muted); }
    @media (max-width: 820px) {
      header { height: 142px; grid-template-columns: 1fr auto; }
      .identity, audio { grid-column: 1 / -1; }
      main { height: calc(100% - 142px); grid-template-rows: 210px minmax(0, 1fr); }
      .now { grid-template-columns: 64px minmax(0, 1fr); padding: 14px; }
      .now-text { font-size: 17px; }
      col.time { width: 96px; }
      col.source, col.translated { width: calc((100% - 96px) / 2); }
    }
  </style>
</head>
<body>
  <header>
    <div class="identity">
      <h1>字幕听轴校对</h1>
      <div class="filename" id="filename">正在读取...</div>
    </div>
    <audio id="audio" controls preload="metadata"></audio>
    <div class="transport">
      <button type="button" id="back" title="后退 5 秒">-5s</button>
      <button type="button" id="previous" title="上一条字幕">上一条</button>
      <button type="button" id="next" title="下一条字幕">下一条</button>
      <button type="button" id="forward" title="前进 5 秒">+5s</button>
    </div>
  </header>
  <main>
    <section class="now" aria-live="polite">
      <div class="now-label">原文</div>
      <div class="now-text" id="nowSource"></div>
      <div class="now-label">中文</div>
      <div class="now-text" id="nowTranslated"></div>
    </section>
    <section class="workspace">
      <div class="toolbar">
        <strong id="clock">00:00 / 00:00</strong>
        <input id="search" type="search" placeholder="搜索字幕内容">
        <label class="follow"><input id="follow" type="checkbox" checked>跟随播放位置</label>
      </div>
      <div class="table-wrap" id="tableWrap">
        <table>
          <colgroup><col class="time"><col class="source"><col class="translated"></colgroup>
          <thead><tr><th>时间</th><th>原文</th><th>中文</th></tr></thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const audio = document.querySelector('#audio');
    const rowsElement = document.querySelector('#rows');
    const tableWrap = document.querySelector('#tableWrap');
    const search = document.querySelector('#search');
    const follow = document.querySelector('#follow');
    let sourceCues = [];
    let translatedCues = [];
    let rows = [];
    let activeRow = null;

    function formatTime(value, includeMillis = false) {
      if (!Number.isFinite(value)) return '00:00';
      const total = Math.max(0, value);
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const seconds = Math.floor(total % 60);
      const base = hours > 0
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
      return includeMillis ? `${base}.${String(Math.floor((total % 1) * 1000)).padStart(3, '0')}` : base;
    }

    function cueAt(cues, time) {
      return cues.find(cue => cue.start <= time + 0.04 && cue.end >= time - 0.04) || null;
    }

    function bestMatch(cues, anchor, index) {
      const sameIndex = cues[index];
      if (sameIndex && Math.abs(sameIndex.start - anchor.start) < 0.08 && Math.abs(sameIndex.end - anchor.end) < 0.08) {
        return sameIndex;
      }
      let best = null;
      let bestOverlap = 0;
      for (const cue of cues) {
        if (cue.start > anchor.end + 1) break;
        const overlap = Math.max(0, Math.min(anchor.end, cue.end) - Math.max(anchor.start, cue.start));
        if (overlap > bestOverlap) {
          best = cue;
          bestOverlap = overlap;
        }
      }
      return best;
    }

    function translatedText(cue, sourceText) {
      if (!cue?.text) return '';
      const lines = cue.text.split('\n').map(line => line.trim()).filter(Boolean);
      if (lines.length < 2) return cue.text;
      const normalizedSource = (sourceText || '').replace(/\s+/g, '');
      const normalizedFirst = lines[0].replace(/\s+/g, '');
      const firstLooksJapanese = /[\u3040-\u30ff]/.test(lines[0]);
      if (firstLooksJapanese || normalizedFirst === normalizedSource) {
        return lines.slice(1).join('\n');
      }
      return cue.text;
    }

    function makeRows() {
      const anchors = sourceCues.length ? sourceCues : translatedCues;
      rows = anchors.map((anchor, index) => {
        const match = sourceCues.length ? bestMatch(translatedCues, anchor, index) : null;
        return {
          start: anchor.start,
          end: anchor.end,
          source: sourceCues.length ? anchor.text : '',
          translated: sourceCues.length ? translatedText(match, anchor.text) : anchor.text,
        };
      });
    }

    function renderRows(query = '') {
      const normalized = query.trim().toLocaleLowerCase();
      rowsElement.replaceChildren();
      rows.forEach((row, index) => {
        if (normalized && !`${row.source}\n${row.translated}`.toLocaleLowerCase().includes(normalized)) return;
        const tr = document.createElement('tr');
        tr.dataset.index = String(index);
        tr.innerHTML = `<td>${formatTime(row.start, true)}</td><td></td><td></td>`;
        tr.children[1].textContent = row.source;
        tr.children[2].textContent = row.translated;
        tr.addEventListener('click', () => {
          audio.currentTime = row.start + 0.01;
          updateNow(true);
        });
        rowsElement.appendChild(tr);
      });
      activeRow = null;
      updateNow(false);
    }

    function setCurrentText(element, cue) {
      element.textContent = cue?.text || '（当前没有字幕）';
      element.classList.toggle('empty', !cue?.text);
    }

    function updateNow(forceScroll = false) {
      const time = audio.currentTime || 0;
      const sourceCue = cueAt(sourceCues, time);
      const translatedCue = cueAt(translatedCues, time);
      setCurrentText(document.querySelector('#nowSource'), sourceCue);
      setCurrentText(
        document.querySelector('#nowTranslated'),
        translatedCue ? {...translatedCue, text: translatedText(translatedCue, sourceCue?.text || '')} : null,
      );
      document.querySelector('#clock').textContent = `${formatTime(time)} / ${formatTime(audio.duration)}`;

      const index = rows.findIndex(row => row.start <= time + 0.04 && row.end >= time - 0.04);
      if (index === activeRow && !forceScroll) return;
      rowsElement.querySelector('tr.active')?.classList.remove('active');
      activeRow = index;
      const rowElement = rowsElement.querySelector(`tr[data-index="${index}"]`);
      rowElement?.classList.add('active');
      if (rowElement && follow.checked && (forceScroll || !audio.paused)) {
        rowElement.scrollIntoView({block: 'center', behavior: forceScroll ? 'auto' : 'smooth'});
      }
    }

    function jumpToRow(direction) {
      const current = audio.currentTime || 0;
      let index;
      if (direction > 0) {
        index = rows.findIndex(row => row.start > current + 0.08);
        if (index < 0) index = rows.length - 1;
      } else {
        index = rows.findLastIndex(row => row.start < current - 0.35);
        if (index < 0) index = 0;
      }
      if (rows[index]) {
        audio.currentTime = rows[index].start + 0.01;
        updateNow(true);
      }
    }

    async function start() {
      const data = await fetch('/data').then(response => response.json());
      sourceCues = data.source_cues;
      translatedCues = data.translated_cues;
      document.querySelector('#filename').textContent = data.media_name;
      document.title = `${data.media_name} - 字幕听轴校对`;
      audio.src = '/media';
      makeRows();
      renderRows();
    }

    audio.addEventListener('timeupdate', () => updateNow(false));
    audio.addEventListener('loadedmetadata', () => updateNow(false));
    search.addEventListener('input', () => renderRows(search.value));
    document.querySelector('#back').addEventListener('click', () => { audio.currentTime = Math.max(0, audio.currentTime - 5); });
    document.querySelector('#forward').addEventListener('click', () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 5); });
    document.querySelector('#previous').addEventListener('click', () => jumpToRow(-1));
    document.querySelector('#next').addEventListener('click', () => jumpToRow(1));
    start().catch(error => {
      document.querySelector('#filename').textContent = `读取失败：${error.message}`;
    });
  </script>
</body>
</html>
"""


class ReviewHandler(BaseHTTPRequestHandler):
    media_path: Path
    payload: bytes

    def do_HEAD(self) -> None:
        self._route(head_only=True)

    def do_GET(self) -> None:
        self._route(head_only=False)

    def _route(self, *, head_only: bool) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8", head_only)
        elif path == "/data":
            self._send_bytes(self.payload, "application/json; charset=utf-8", head_only)
        elif path == "/media":
            self._send_media(head_only=head_only)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _send_bytes(self, content: bytes, content_type: str, head_only: bool) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _send_media(self, *, head_only: bool) -> None:
        size = self.media_path.stat().st_size
        start = 0
        end = size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range", "")
        match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
            if not match.group(1) and match.group(2):
                length = min(int(match.group(2)), size)
                start = size - length
                end = size - 1
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(self.media_path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only:
            return

        with self.media_path.open("rb") as media:
            media.seek(start)
            remaining = content_length
            try:
                while remaining > 0:
                    chunk = media.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # Browsers cancel old range requests whenever the user seeks.
                return

    def log_message(self, format: str, *args: object) -> None:
        return


def existing_file(value: str | None, label: str, *, required: bool) -> Path | None:
    if not value:
        if required:
            raise SystemExit(f"缺少{label}路径。")
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label}不存在：{path}")
    return path


def create_review_server(
    media: Path,
    source_srt: Path | None,
    translated_srt: Path | None,
    *,
    port: int = 0,
) -> tuple[ThreadingHTTPServer, str]:
    payload = json.dumps(
        build_payload(media, source_srt, translated_srt),
        ensure_ascii=False,
    ).encode("utf-8")
    handler_type = type(
        "BoundReviewHandler",
        (ReviewHandler,),
        {"media_path": media, "payload": payload},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_type)
    return server, f"http://127.0.0.1:{server.server_port}/"


def start_review_server(
    media: Path,
    source_srt: Path | None,
    translated_srt: Path | None,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> ReviewServerHandle:
    server, url = create_review_server(media, source_srt, translated_srt, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="subtitle-review-server")
    thread.start()
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    return ReviewServerHandle(server=server, thread=thread, url=url)


def main() -> int:
    parser = argparse.ArgumentParser(description="直接用音频和 SRT 校对字幕时间轴，无需合成视频。")
    parser.add_argument("--media", required=True, help="音频或视频文件")
    parser.add_argument("--source-srt", help="原文 SRT")
    parser.add_argument("--translated-srt", help="翻译 SRT")
    parser.add_argument("--port", type=int, default=0, help="本地端口；默认自动选择")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    media = existing_file(args.media, "媒体文件", required=True)
    source_srt = existing_file(args.source_srt, "原文字幕", required=False)
    translated_srt = existing_file(args.translated_srt, "翻译字幕", required=False)
    if source_srt is None and translated_srt is None:
        raise SystemExit("至少需要提供一份 SRT 字幕。")
    assert media is not None

    server, url = create_review_server(media, source_srt, translated_srt, port=args.port)
    print(f"字幕校对器已启动：{url}", flush=True)
    print("关闭此窗口即可停止校对器。", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
