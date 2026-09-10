"""
core/utils.py
=============
Shared utility functions reused across all automation modules.
"""

import os
import csv
import hashlib
import datetime


# ---------------------------------------------------------------------------
# File utilities
# ---------------------------------------------------------------------------

def file_content_hash(path: str) -> str:
    """Return the MD5 hex-digest of a file's contents."""
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def compute_file_sha256(path: str) -> str:
    """Return the SHA-256 hex-digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()



def remove_empty_dirs(root_dir: str) -> None:
    """Recursively remove empty directories under *root_dir* (bottom-up).
    Useful as a post-run cleanup for stray empty folders left by skipped rows
    or failed downloads."""
    if not root_dir or not os.path.isdir(root_dir):
        return
    for current_dir, subdirs, files in os.walk(root_dir, topdown=False):
        if current_dir == root_dir:
            continue
        try:
            if not os.listdir(current_dir):
                os.rmdir(current_dir)
        except OSError:
            pass


def build_output_path(
    template: str,
    base_download_dir: str,
    student_id: str,
    segment: str,
    category: str,
    filename: str,
) -> str:
    """
    Build destination directory path based on a customizable folder hierarchy template.

    Supported placeholders:
      - {studentId} : Record / Student ID (e.g. '10245')
      - {segment}   : Segment widget title (e.g. 'Summary', 'Actions')
      - {category}  : Row category (e.g. 'Transcripts', 'Files')
      - {ext}       : File extension without dot (e.g. 'pdf')
      - {date}      : Current date ISO format ('YYYY-MM-DD')

    Default template: '{studentId}/{segment}/{category}'
    """
    if not template or not template.strip():
        template = "{studentId}/{segment}/{category}"

    sid_clean = sanitize_filename(student_id.strip())
    seg_clean = sanitize_filename(segment.strip()) if segment and segment != "Uncategorized" else ""
    cat_clean = sanitize_filename(category.strip()) if category and category != "Uncategorized" else ""
    ext_clean = os.path.splitext(filename)[1].lstrip('.').lower() or "file"
    today_str = datetime.date.today().isoformat()

    # Replace placeholders
    rendered = (
        template.replace("{studentId}", sid_clean)
                .replace("{student_id}", sid_clean)
                .replace("{segment}", seg_clean)
                .replace("{category}", cat_clean)
                .replace("{ext}", ext_clean)
                .replace("{date}", today_str)
    )

    # Split into folder parts, filtering out empty ones (e.g., if segment is blank)
    parts = [p.strip() for p in rendered.replace('\\', '/').split('/') if p.strip()]

    target_dir = os.path.join(base_download_dir, *parts) if parts else base_download_dir
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def sanitize_filename(name: str) -> str:
    """Strip characters that are invalid in Windows file/folder names."""
    if not name:
        return "Uncategorized"
    name = name.replace('\n', ' ').replace('\r', ' ')
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    name = ' '.join(name.split())
    if len(name) > 100:
        name = name[:100]
    return name.strip() or "Uncategorized"


def sanitize_upload_filename(filename: str) -> str:
    """Normalize filenames that have multiple dots (e.g. ``a.b.pdf`` → ``a_b.pdf``).
    Keeps the final extension, replaces dots in the base name with underscores."""
    if filename.count('.') > 1:
        base, ext = filename.rsplit('.', 1)
        base = base.replace('.', '_')
        return f"{base}.{ext}"
    return filename


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

def format_timedelta(td: datetime.timedelta) -> str:
    """Format a timedelta as ``HH:MM:SS``."""
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_bytes(num_bytes: float) -> str:
    """Format byte counts into human-readable strings (e.g. '12.4 MB', '450.2 KB')."""
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(num_bytes) < 1024.0 or unit == 'TB':
            if unit in ('B',):
                return f"{int(num_bytes)} {unit}"
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} TB"


def format_speed_bps(bytes_per_sec: float) -> str:
    """Format transfer throughput in B/s, KB/s, or MB/s."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "- B/s"
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{int(bytes_per_sec)} B/s"


def format_time_remaining(seconds: float) -> str:
    """Format remaining seconds as user-friendly duration string (e.g. '2m 15s', '< 5s')."""
    if seconds is None or seconds < 0:
        return "Calculating..."
    sec = int(round(seconds))
    if sec < 5:
        return "< 5s"
    elif sec < 60:
        return f"{sec}s"
    else:
        mins, s = divmod(sec, 60)
        if mins < 60:
            return f"{mins}m {s:02d}s"
        else:
            hrs, mins = divmod(mins, 60)
            return f"{hrs}h {mins:02d}m"



# ---------------------------------------------------------------------------
# Credentials & CSV loading
# ---------------------------------------------------------------------------

def load_credentials(path: str, required_keys: list[str] | None = None) -> dict | None:
    """Parse a ``key=value`` credentials text file.

    Lines starting with ``#`` are ignored.  Returns a dict of ``{key: value}``
    (all keys lower-cased), or ``None`` if the file is unreadable or any
    *required_keys* are missing.

    Additional typed keys populated automatically (used by FileExporter):
      - ``extra_extensions``: set of extra file extensions from the csv list.
      - ``exclude_link_text_list``: set of lowercase link texts to exclude.
      - ``min_columns``: int minimum columns per table row (default 2).
    """
    data: dict = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    key, val = line.split('=', 1)
                    data[key.strip().lower()] = val.strip()
    except Exception:
        return None

    if required_keys and not all(k in data for k in required_keys):
        return None

    # Parse typed fields used by FileExporter
    def _csv_set(key: str) -> set:
        val = data.get(key, '')
        return {v.strip().lower() for v in val.split(',') if v.strip()}

    data['extra_extensions'] = _csv_set('extra_extensions')
    data['exclude_link_text_list'] = _csv_set('exclude_link_text')
    try:
        data['min_columns'] = int(data.get('min_columns') or 2)
    except (ValueError, TypeError):
        data['min_columns'] = 2

    # Optional (used by FileUploader): comma-separated file extensions, no
    # dots, that should be skipped without even attempting an upload, e.g.
    # "unsupported_extensions=exe,dll,bat". Files the target system itself
    # rejects as unsupported are also detected automatically at upload time
    # -- this setting just lets obviously-bad types be skipped instantly.
    data['unsupported_extensions_list'] = _csv_set('unsupported_extensions')

    return data


def read_links_csv(path: str) -> list[dict]:
    """Parse a student-links CSV file.

    Accepts both comma and semicolon delimiters.  Expects columns whose
    normalised headers (lower-cased, spaces/underscores stripped) match:
    * ID column: ``studentid``, ``id``, or ``student``
    * Link column: ``link``, ``url``, or ``studentlink``

    Returns a list of ``{'studentId': ..., 'link': ...}`` dicts.
    """
    students: list[dict] = []
    try:
        with open(path, mode='r', encoding='utf-8-sig') as f:
            sample = f.read(2048)
            f.seek(0)
            delimiter = ';' if ';' in sample and ',' not in sample else ','
            reader = csv.reader(f, delimiter=delimiter)
            raw_headers = next(reader, [])
            headers = [h.strip().lower().replace(' ', '').replace('_', '') for h in raw_headers]

            id_idx = link_idx = None
            for idx, h in enumerate(headers):
                if h in ('studentid', 'id', 'student'):
                    id_idx = idx
                if h in ('link', 'url', 'studentlink'):
                    link_idx = idx

            if id_idx is None or link_idx is None:
                return []

            for row in reader:
                if row and len(row) > max(id_idx, link_idx):
                    students.append({
                        'studentId': row[id_idx].strip(),
                        'link': row[link_idx].strip(),
                    })
    except Exception:
        return []
    return students


def filter_valid_students(students: list[dict]) -> list[dict]:
    """Remove header artefacts, row-index numbers, and blank IDs."""
    result = []
    for s in students:
        raw = s['studentId'].strip()
        sid = raw.split('.')[0] if '.' in raw else raw
        if (not sid
                or len(sid) < 3
                or sid.lower() in ('studentid', 'id', 'student')
                or sid == '1'):
            continue
        result.append({'studentId': sid, 'link': s['link']})
    return result


# ---------------------------------------------------------------------------
# Folder helpers (used by uploaders)
# ---------------------------------------------------------------------------

def list_category_folders(student_root_folder: str) -> list[dict]:
    """Return a sorted list of category entries under *student_root_folder*.

    Each entry is ``{'name': str, 'path': str, 'files': [str, ...],
    'is_root': bool}``.

    Two kinds of entries are produced:

    * **Sub-folder entries** – immediate child directories become named
      categories (``is_root=False``).  The uploader will create / navigate
      into a matching folder in Orbund before uploading.
    * **Root-level entry** – files sitting *directly* in
      *student_root_folder* (not inside any sub-folder) are collected into a
      single entry with ``is_root=True`` and an empty name.  The uploader
      will upload these files straight into the target parent location in
      Orbund without creating any sub-folder.  This entry is appended after
      all sub-folder entries so that named categories are processed first.
    """
    result: list[dict] = []
    if not os.path.isdir(student_root_folder):
        return result

    root_files: list[str] = []

    for entry in sorted(os.listdir(student_root_folder)):
        full_path = os.path.join(student_root_folder, entry)
        if os.path.isdir(full_path):
            files = sorted(
                fn for fn in os.listdir(full_path)
                if os.path.isfile(os.path.join(full_path, fn))
            )
            result.append({'name': entry, 'path': full_path, 'files': files, 'is_root': False})
        elif os.path.isfile(full_path):
            root_files.append(entry)

    if root_files:
        result.append({
            'name':    '',
            'path':    student_root_folder,
            'files':   root_files,
            'is_root': True,
        })

    return result


# ---------------------------------------------------------------------------
# XPath helpers
# ---------------------------------------------------------------------------

def xpath_literal(value: str) -> str:
    """Build a safe XPath string literal for arbitrary text (handles embedded quotes)."""
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts = value.split('"')
    pieces = [f'"{p}"' for p in parts]
    return "concat(" + ", '\"', ".join(pieces) + ")"


def describe_exception(e: Exception) -> str:
    """Return a compact one-line summary of an exception."""
    msg = str(e).strip()
    first_line = msg.splitlines()[0].strip() if msg else ''
    if first_line:
        return f"{type(e).__name__}: {first_line}"
    return f"{type(e).__name__} (no message)"


# ---------------------------------------------------------------------------
# Run-scoped log file naming
# ---------------------------------------------------------------------------
# Every module used to write its debug .log / execution .csv to one fixed
# filename that every run appended to forever, making it impossible to tell
# one run's results apart from another's without diffing timestamps inside
# the file. These two helpers give each run's log files a unique, sortable
# name (a start-of-run timestamp baked into the filename) while keeping
# read/verify code working against old, pre-existing fixed-name log files
# via a fallback lookup.

def new_run_id() -> str:
    """Return a filesystem-safe timestamp identifying a single run, e.g.
    '20260814_153012'. Generate this ONCE at the start of a run and reuse it
    for every log file that run writes, so multiple writes within the same
    run keep landing in the same file while separate runs never collide."""
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


def find_latest_run_log(directory: str, prefix: str, suffix: str) -> str | None:
    """Find the most recently modified run-scoped log file in *directory*
    matching '{prefix}_*{suffix}' (e.g. prefix='automation_execution_log',
    suffix='.csv' matches 'automation_execution_log_20260814_153012.csv').

    Falls back to the old fixed-name file '{prefix}{suffix}' if no
    timestamped file exists yet (backward compatibility with logs written
    before per-run naming existed). Returns None if neither is found."""
    import glob
    directory = directory or '.'
    candidates = glob.glob(os.path.join(directory, f'{prefix}_*{suffix}'))
    if candidates:
        return max(candidates, key=os.path.getmtime)
    legacy = os.path.join(directory, f'{prefix}{suffix}')
    return legacy if os.path.isfile(legacy) else None


def is_network_error(e: Exception | None) -> bool:
    """Return True if an exception represents a network disconnection, DNS failure, or socket outage."""
    if e is None:
        return False
    msg = str(e).lower()
    net_indicators = [
        "err_internet_disconnected",
        "err_name_not_resolved",
        "err_connection_refused",
        "err_connection_timed_out",
        "err_connection_reset",
        "err_address_unreachable",
        "err_network_changed",
        "maxretryerror",
        "newconnectionerror",
        "connection refused",
        "failed to establish a new connection",
        "remotedisconnected",
        "connectionreseterror",
        "connectionrefusederror",
        "network is unreachable",
        "no route to host",
        "temporary failure in name resolution",
        "getaddrinfo failed",
        "disconnected",
    ]
    return any(ind in msg for ind in net_indicators)
