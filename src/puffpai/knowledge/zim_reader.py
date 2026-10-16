"""
PuffPAI Custom ZIM Reader

Reads ZIM archive files directly from the publicly documented ZIM format
specification (https://wiki.openzim.org/wiki/ZIM_file_format).

This is 100% original code -- no GPL Kiwix code is used.
The ZIM format itself is a documented open standard.

Patent-relevant: Custom ZIM Knowledge Pack System for edge AI devices.

ZIM Format Summary (from public spec):
- Header at offset 0 (80 bytes for ZIMv5, 80 bytes for ZIMv6)
- URL pointer list: sorted array of 8-byte offsets into directory entries
- Title pointer list: sorted by title
- Directory entries: URL, title, namespace, MIME type, cluster/blob info
- Clusters: LZMA or Zstandard compressed blobs
- MIME type list: null-terminated strings at mimeListPos

Supported compression: uncompressed (type 0/1), LZMA/xz (type 4),
Zstandard (type 5).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import struct
import io
import os
import re
from dataclasses import dataclass, field
from typing import Optional, Iterator

# Ceiling on a single decompressed ZIM cluster. Applies to every compression
# branch, because a pack can now arrive from a remote host and its clusters are
# untrusted input. Sized to match the zstd cap this file already carried.
_MAX_CLUSTER_BYTES = 64 * 1024 * 1024


class ZIMTruncatedError(ValueError):
    """Raised when a ZIM file is incomplete (e.g. an interrupted download).

    The header's internal offsets point past the end of the file, so the archive
    cannot be read at all. The only fix is to re-download the pack.
    """

    def __init__(self, filepath: str, actual_size: int, expected_size: int):
        self.filepath = filepath
        self.actual_size = actual_size
        self.expected_size = expected_size
        self.missing_bytes = max(expected_size - actual_size, 0)
        pct = (actual_size / expected_size * 100) if expected_size else 0.0
        super().__init__(
            f"Incomplete ZIM archive: {os.path.basename(filepath)}\n"
            f"  on disk : {actual_size:,} bytes ({actual_size / 1024**3:.1f} GiB)\n"
            f"  expected: {expected_size:,} bytes ({expected_size / 1024**3:.1f} GiB)\n"
            f"  missing : {self.missing_bytes:,} bytes "
            f"({self.missing_bytes / 1024**3:.1f} GiB) -- file is {pct:.0f}% complete\n"
            f"  This pack is an interrupted download and must be re-fetched."
        )


class ZIMClusterTooLargeError(ValueError):
    """Raised when a cluster asks for more memory than the ceiling allows.

    Both halves of a cluster read are file-supplied: the offset table that says
    how many bytes to read, and the compressed payload that says how far it
    expands. A hostile pack can push either one -- an offset whose "next
    cluster" sits terabytes ahead, or a decompression bomb in the payload --
    into an allocation large enough to swap or OOM-kill the box. On an
    appliance that takes down the whole build, not just the indexer, and it
    happens before any ``except`` here could run.

    Refusing up front turns that into one legible failure that
    :mod:`puffpai.knowledge.auto_index` records against the pack, so a bad pack
    is marked unindexable and the scan moves on to the next one.
    """

    def __init__(self, filepath: str, cluster_num: int,
                 requested: int, cap: int):
        self.filepath = filepath
        self.cluster_num = cluster_num
        self.requested = requested
        self.cap = cap
        wants = (f"{requested:,} bytes" if requested >= 0
                 else "more than the ceiling (exact size never measured)")
        super().__init__(
            f"Oversized ZIM cluster: {os.path.basename(filepath)}\n"
            f"  cluster : #{cluster_num}\n"
            f"  wants   : {wants}\n"
            f"  ceiling : {cap:,} bytes ({cap / 1024**2:.0f} MiB)\n"
            f"  This pack is malformed or hostile and will not be indexed."
        )


@dataclass(frozen=True)
class ZIMHeader:
    """Parsed ZIM file header."""
    magic: int
    major_version: int
    minor_version: int
    uuid: bytes
    entry_count: int
    cluster_count: int
    url_ptr_pos: int
    title_ptr_pos: int
    cluster_ptr_pos: int
    mime_list_pos: int
    main_page: int
    layout_page: int
    checksum_pos: int


@dataclass
class DirectoryEntry:
    """A single article/resource entry in the ZIM archive."""
    mime_type_idx: int
    mime_type: str
    namespace: str
    url: str
    title: str
    cluster_number: int = 0
    blob_number: int = 0
    is_redirect: bool = False
    redirect_index: int = 0


# Tags whose start or end is a paragraph break in the plain text.
_BLOCK_TAG_RE = re.compile(
    r'<\s*/?\s*(?:p|div|br|hr|h[1-6]|li|ul|ol|dl|dt|dd|tr|table|thead|tbody'
    r'|blockquote|pre|section|article|header|footer|figure|figcaption'
    r'|caption|details|summary)\b[^>]*>', re.IGNORECASE)


def _paragraph_break(match: 're.Match[str]') -> str:
    """One newline stays a line break; two or more become one blank line."""
    return '\n\n' if match.group(0).count('\n') > 1 else '\n'


@dataclass
class Article:
    """A retrieved article with its content."""
    url: str
    title: str
    mime_type: str
    content: bytes
    namespace: str

    @property
    def text(self) -> str:
        """Return content decoded as UTF-8 text."""
        return self.content.decode('utf-8', errors='replace')

    @property
    def plain_text(self) -> str:
        """Strip HTML tags and return plain text, one paragraph per block.

        Block-level tags (paragraphs, headings, list items, table rows, line
        breaks) become paragraph breaks, so a long article keeps its shape
        and can be cut into passages at natural places. Everything used to be
        squashed onto one line, which made every article one giant passage
        whose opening was all a search ever saw.
        """
        text = self.text
        # Remove script/style blocks
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text,
                       flags=re.DOTALL | re.IGNORECASE)
        # Block-level tags end a paragraph.
        text = _BLOCK_TAG_RE.sub('\n\n', text)
        # Remove the other tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Collapse spaces inside lines, then runs of blank lines.
        text = re.sub(r'[^\S\n]+', ' ', text)
        text = re.sub(r' *\n[\n ]*', _paragraph_break, text).strip()
        # Decode common HTML entities
        for entity, char in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                              ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' ')]:
            text = text.replace(entity, char)
        return text


class ZIMReader:
    """Read articles from ZIM archive files.

    This is a pure-Python reader built from the public ZIM format spec.
    No GPL code is used.

    Usage:
        reader = ZIMReader("wikipedia_en.zim")
        article = reader.get_article_by_url("A/Python_(programming_language)")
        print(article.plain_text[:500])

        for entry in reader.search("python programming"):
            print(entry.title, entry.url)
    """

    # ZIM magic number: 72173914 (0x44D495A)
    ZIM_MAGIC = 72173914

    def __init__(self, filepath: str):
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"ZIM file not found: {filepath}")

        self._filepath = filepath
        self._fh = open(filepath, 'rb')
        self._header = self._read_header()
        self._validate_complete()
        self._mime_types = self._read_mime_list()
        self._url_ptrs: Optional[list] = None  # lazy
        self._cluster_ptrs: Optional[list] = None  # lazy
        self._cluster_cache: dict = {}  # cluster_num -> decompressed blobs
        self._max_cache_clusters = 32

    def close(self):
        """Close the underlying file handle."""
        if self._fh and not self._fh.closed:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    # ── Header Parsing ──────────────────────────────────────────────────────

    def _read_header(self) -> ZIMHeader:
        """Parse the 80-byte ZIM header."""
        self._fh.seek(0)
        data = self._fh.read(80)
        if len(data) < 80:
            raise ValueError("File too small to be a valid ZIM archive")

        (magic, major, minor) = struct.unpack_from('<IHH', data, 0)
        if magic != self.ZIM_MAGIC:
            raise ValueError(
                f"Not a ZIM file (magic={magic:#x}, expected {self.ZIM_MAGIC:#x})")

        uuid = data[8:24]
        (entry_count, cluster_count) = struct.unpack_from('<II', data, 24)
        (url_ptr_pos, title_ptr_pos) = struct.unpack_from('<QQ', data, 32)
        (cluster_ptr_pos, mime_list_pos) = struct.unpack_from('<QQ', data, 48)
        (main_page, layout_page) = struct.unpack_from('<II', data, 64)
        (checksum_pos,) = struct.unpack_from('<Q', data, 72)

        return ZIMHeader(
            magic=magic, major_version=major, minor_version=minor,
            uuid=uuid, entry_count=entry_count, cluster_count=cluster_count,
            url_ptr_pos=url_ptr_pos, title_ptr_pos=title_ptr_pos,
            cluster_ptr_pos=cluster_ptr_pos, mime_list_pos=mime_list_pos,
            main_page=main_page, layout_page=layout_page,
            checksum_pos=checksum_pos,
        )

    def _validate_complete(self):
        """Fail fast if the archive is truncated.

        A partial download still carries a valid 80-byte header, so the file looks
        fine until a pointer list is dereferenced -- at which point struct.unpack
        raises something cryptic from deep in the call stack. Checking the header's
        offsets against the real file size turns that into one clear error at open
        time, which is what a caller can actually act on.
        """
        actual = os.path.getsize(self._filepath)
        h = self._header

        # Header fields may carry UINT64_MAX as a "not present" sentinel -- notably
        # title_ptr_pos, which is deprecated in ZIM v6 and absent from roughly half
        # of modern packs. Adding entry_count to a sentinel produces a
        # garbage "expected size" and flags healthy archives, so only offsets in a
        # plausible range are trusted, and title_ptr_pos is ignored outright.
        SANE_MAX = 2 ** 63
        candidates = []
        if 0 < h.checksum_pos < SANE_MAX:
            candidates.append(h.checksum_pos + 16)  # 16-byte MD5 at the tail
        if 0 < h.url_ptr_pos < SANE_MAX:
            candidates.append(h.url_ptr_pos + h.entry_count * 8)
        if 0 < h.cluster_ptr_pos < SANE_MAX:
            candidates.append(h.cluster_ptr_pos + h.cluster_count * 8)

        if not candidates:
            return  # nothing trustworthy to check against; let reads fail naturally

        expected = max(candidates)
        if expected > actual:
            raise ZIMTruncatedError(self._filepath, actual, expected)

    # ── MIME Type List ──────────────────────────────────────────────────────

    def _read_mime_list(self) -> list:
        """Read the null-terminated MIME type list."""
        self._fh.seek(self._header.mime_list_pos)
        mime_types = []
        buf = b''
        while True:
            byte = self._fh.read(1)
            if not byte:
                break
            if byte == b'\x00':
                if not buf:
                    break  # double null = end of list
                mime_types.append(buf.decode('utf-8', errors='replace'))
                buf = b''
            else:
                buf += byte
        return mime_types

    # ── URL Pointer List (lazy loaded) ──────────────────────────────────────

    def _ensure_url_ptrs(self):
        """Load the URL pointer array on first access."""
        if self._url_ptrs is not None:
            return
        self._fh.seek(self._header.url_ptr_pos)
        count = self._header.entry_count
        data = self._fh.read(count * 8)
        self._url_ptrs = list(struct.unpack_from(f'<{count}Q', data))

    # ── Cluster Pointer List (lazy loaded) ──────────────────────────────────

    def _ensure_cluster_ptrs(self):
        """Load the cluster pointer array on first access."""
        if self._cluster_ptrs is not None:
            return
        self._fh.seek(self._header.cluster_ptr_pos)
        count = self._header.cluster_count
        data = self._fh.read(count * 8)
        self._cluster_ptrs = list(struct.unpack_from(f'<{count}Q', data))

    # ── Directory Entry Parsing ─────────────────────────────────────────────

    def _read_directory_entry(self, index: int) -> DirectoryEntry:
        """Read a single directory entry by its index."""
        self._ensure_url_ptrs()
        assert self._url_ptrs is not None  # guaranteed by _ensure_url_ptrs
        if index < 0 or index >= len(self._url_ptrs):
            raise IndexError(f"Entry index {index} out of range")

        offset = self._url_ptrs[index]
        self._fh.seek(offset)

        # First 2 bytes: MIME type index (little-endian unsigned short)
        mime_idx = struct.unpack('<H', self._fh.read(2))[0]

        # 1 byte: parameter length (unused, skip)
        _param_len = struct.unpack('B', self._fh.read(1))[0]

        # 1 byte: namespace (single ASCII char)
        namespace = self._fh.read(1).decode('ascii', errors='replace')

        # 4 bytes: revision (unused in modern ZIM, skip)
        _revision = struct.unpack('<I', self._fh.read(4))[0]

        is_redirect = (mime_idx == 0xFFFF)

        if is_redirect:
            # Redirect entry: 4 bytes redirect index
            redirect_idx = struct.unpack('<I', self._fh.read(4))[0]
            url = self._read_null_terminated_string()
            title = self._read_null_terminated_string()
            return DirectoryEntry(
                mime_type_idx=mime_idx, mime_type='redirect',
                namespace=namespace, url=url, title=title or url,
                is_redirect=True, redirect_index=redirect_idx,
            )
        else:
            # Content entry: 4 bytes cluster, 4 bytes blob
            cluster_num = struct.unpack('<I', self._fh.read(4))[0]
            blob_num = struct.unpack('<I', self._fh.read(4))[0]
            url = self._read_null_terminated_string()
            title = self._read_null_terminated_string()

            mime = (self._mime_types[mime_idx]
                    if mime_idx < len(self._mime_types)
                    else 'application/octet-stream')

            return DirectoryEntry(
                mime_type_idx=mime_idx, mime_type=mime,
                namespace=namespace, url=url, title=title or url,
                cluster_number=cluster_num, blob_number=blob_num,
            )

    def _read_null_terminated_string(self) -> str:
        """Read a null-terminated UTF-8 string from current position."""
        buf = b''
        while True:
            byte = self._fh.read(1)
            if not byte or byte == b'\x00':
                break
            buf += byte
        return buf.decode('utf-8', errors='replace')

    # ── Cluster Decompression ───────────────────────────────────────────────

    def _read_cluster(self, cluster_num: int) -> list:
        """Read and decompress a cluster, returning list of blob bytes.

        Cluster format:
        - 1 byte: compression type (0/1=none, 4=LZMA/xz, 5=zstd)
        - For extended clusters (bit 4 set): 1 byte flags, then 8-byte offsets
        - Otherwise: 4-byte offsets
        - Blob data follows the offset list
        """
        if cluster_num in self._cluster_cache:
            return self._cluster_cache[cluster_num]

        self._ensure_cluster_ptrs()
        assert self._cluster_ptrs is not None  # guaranteed by _ensure_cluster_ptrs
        if cluster_num < 0 or cluster_num >= len(self._cluster_ptrs):
            raise IndexError(f"Cluster {cluster_num} out of range")

        cluster_offset = self._cluster_ptrs[cluster_num]

        # Determine cluster end (next cluster start or checksum pos)
        if cluster_num + 1 < len(self._cluster_ptrs):
            cluster_end = self._cluster_ptrs[cluster_num + 1]
        else:
            cluster_end = self._header.checksum_pos

        # Both `cluster_offset` and `cluster_end` come out of the file's own
        # pointer table, so this size is attacker-chosen on a downloaded pack.
        # Ceiling it before the read, or one bogus offset becomes an arbitrary
        # allocation. The negative case matters just as much as the huge one:
        # `read(-1)` reads the entire file rather than nothing.
        cluster_size = cluster_end - cluster_offset
        if cluster_size > _MAX_CLUSTER_BYTES:
            raise ZIMClusterTooLargeError(
                self._filepath, cluster_num, cluster_size, _MAX_CLUSTER_BYTES)
        if cluster_size <= 0:
            return []

        self._fh.seek(cluster_offset)
        raw = self._fh.read(cluster_size)

        if not raw:
            return []

        # First byte: compression info
        comp_byte = raw[0]
        comp_type = comp_byte & 0x0F
        is_extended = bool(comp_byte & 0x10)

        # Decompress if needed
        payload = raw[1:]
        if comp_type == 4:
            # LZMA / xz compression, BOUNDED.
            #
            # lzma.decompress() allocates whatever the stream asks for, so a
            # crafted cluster is a decompression bomb: the box swaps or is
            # OOM-killed before any `except` here can run, which on an
            # appliance takes down the whole build and not just the indexer.
            # This became reachable when packs started arriving from remote
            # hosts (source_fetch -> zim_dir -> auto_index -> ingest_zim), and
            # nothing gates it behind owner action - the next startup scan
            # does it. The zstd branch below was already capped; this is the
            # same ceiling, applied to the path that lacked one.
            try:
                import lzma
                decompressor = lzma.LZMADecompressor()
                payload = decompressor.decompress(
                    payload, max_length=_MAX_CLUSTER_BYTES)
                # `eof` is False when input remains, i.e. the stream had more to
                # give than the ceiling allows. The bomb stops here, still
                # unexpanded -- max_length is a hard allocation bound, not a
                # check after the fact.
                overflowed = not decompressor.eof
            except Exception:
                return []
            if overflowed:
                # Outside the try on purpose: raised inside, the handler above
                # would swallow it back into a silent empty cluster.
                raise ZIMClusterTooLargeError(
                    self._filepath, cluster_num, -1, _MAX_CLUSTER_BYTES)
        elif comp_type == 5:
            # Zstandard compression
            oversized = False
            # Bound outside the try: the fallback path below can set
            # `oversized` without ever reaching the declared-size lookup.
            declared = -1
            try:
                import zstandard as zstd
                # Check the frame's declared content size first. It is the
                # crafted-bomb case and worth naming precisely, because
                # max_output_size below reports it as a ZstdError that is
                # indistinguishable from ordinary corruption. A frame that
                # declares no size returns -1 and falls through to the cap.
                frame_size = getattr(zstd, "frame_content_size", None)
                if frame_size is not None:
                    declared = frame_size(payload)
                if declared > _MAX_CLUSTER_BYTES:
                    oversized = True
                else:
                    dctx = zstd.ZstdDecompressor()
                    payload = dctx.decompress(
                        payload, max_output_size=_MAX_CLUSTER_BYTES)
            except ImportError:
                # Try the zstd package as fallback
                try:
                    import zstd as zstd_alt  # type: ignore[no-redef]
                    # This package exposes no output ceiling, so the payload has
                    # already expanded by the time it can be measured -- the
                    # check keeps an oversized cluster out of the cache and the
                    # index, but cannot bound the allocation itself. That limit
                    # is why `zstandard` is the pinned requirement in
                    # requirements.txt and this is only a fallback.
                    payload = zstd_alt.decompress(payload)
                    oversized = len(payload) > _MAX_CLUSTER_BYTES
                except ImportError:
                    raise ImportError(
                        "Zstandard decompression requires the 'zstandard' package. "
                        "Install with: pip install zstandard"
                    )
            except Exception:
                return []
            if oversized:
                raise ZIMClusterTooLargeError(
                    self._filepath, cluster_num, declared, _MAX_CLUSTER_BYTES)
        elif comp_type not in (0, 1):
            # Unknown compression
            return []

        # Parse blob offsets from decompressed payload
        offset_size = 8 if is_extended else 4
        fmt = '<Q' if is_extended else '<I'

        if len(payload) < offset_size:
            return []

        # First offset tells us how many blobs there are
        first_offset = struct.unpack_from(fmt, payload, 0)[0]
        if first_offset == 0 or first_offset % offset_size != 0:
            return []

        blob_count = first_offset // offset_size
        offsets = []
        for i in range(blob_count):
            pos = i * offset_size
            if pos + offset_size > len(payload):
                break
            offsets.append(struct.unpack_from(fmt, payload, pos)[0])

        # Add end-of-payload as final boundary
        offsets.append(len(payload))

        # Extract blobs
        blobs = []
        for i in range(len(offsets) - 1):
            start = offsets[i]
            end = offsets[i + 1]
            if start <= end <= len(payload):
                blobs.append(payload[start:end])
            else:
                blobs.append(b'')

        # Cache management (LRU-style eviction)
        if len(self._cluster_cache) >= self._max_cache_clusters:
            oldest = next(iter(self._cluster_cache))
            del self._cluster_cache[oldest]
        self._cluster_cache[cluster_num] = blobs

        return blobs

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def entry_count(self) -> int:
        return self._header.entry_count

    @property
    def filepath(self) -> str:
        return self._filepath

    def get_entry(self, index: int) -> DirectoryEntry:
        """Get a directory entry by index."""
        return self._read_directory_entry(index)

    def get_article(self, index: int, follow_redirects: bool = True) -> Optional[Article]:
        """Retrieve an article by its entry index.

        Args:
            index: Directory entry index.
            follow_redirects: If True, resolve redirect chains (max 5 hops).

        Returns:
            Article object or None if not found.
        """
        seen = set()
        current = index

        for _ in range(5):  # max redirect hops
            if current in seen:
                return None  # circular redirect
            seen.add(current)

            entry = self._read_directory_entry(current)

            if entry.is_redirect and follow_redirects:
                current = entry.redirect_index
                continue

            if entry.is_redirect:
                return None

            # Read content from cluster
            try:
                blobs = self._read_cluster(entry.cluster_number)
                if entry.blob_number < len(blobs):
                    content = blobs[entry.blob_number]
                else:
                    content = b''
            except ZIMClusterTooLargeError:
                # Deliberately not swallowed. Every other cluster failure is one
                # unreadable article, which the blanket handler below degrades
                # to empty content so the rest of the pack still indexes. An
                # oversized cluster is a property of the pack itself, so it has
                # to reach auto_index -- swallowed, the pack would just yield
                # zero documents and be filed as "may be corrupt" instead of
                # naming the refused allocation.
                raise
            except (IndexError, Exception):
                content = b''

            return Article(
                url=entry.url, title=entry.title,
                mime_type=entry.mime_type, content=content,
                namespace=entry.namespace,
            )

        return None  # too many redirects

    def get_article_by_url(self, full_url: str) -> Optional[Article]:
        """Find and return an article by its full URL (e.g. 'A/Main_Page').

        Uses binary search on the URL pointer list for efficiency.
        """
        self._ensure_url_ptrs()

        # Binary search through sorted URL entries
        lo, hi = 0, self._header.entry_count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            entry = self._read_directory_entry(mid)
            entry_url = f"{entry.namespace}/{entry.url}"
            if entry_url == full_url:
                return self.get_article(mid)
            elif entry_url < full_url:
                lo = mid + 1
            else:
                hi = mid - 1

        return None

    def search(self, query: str, namespace: str = 'A',
               max_results: int = 20) -> Iterator[DirectoryEntry]:
        """Search article titles for a query string.

        This is a simple substring search over titles. For full-text search,
        index the ZIM content into ChromaDB using the ingestion pipeline.

        Args:
            query: Search string (case-insensitive).
            namespace: ZIM namespace to search ('A' = articles).
            max_results: Maximum results to return.

        Yields:
            DirectoryEntry objects matching the query.
        """
        query_lower = query.lower()
        count = 0

        for i in range(self._header.entry_count):
            if count >= max_results:
                break

            entry = self._read_directory_entry(i)

            if entry.namespace != namespace:
                continue
            if entry.is_redirect:
                continue
            if not entry.mime_type.startswith('text/html'):
                continue

            if query_lower in entry.title.lower():
                yield entry
                count += 1

    def iter_articles(self, namespace: str = 'A',
                      mime_filter: str = 'text/html') -> Iterator[DirectoryEntry]:
        """Iterate over all content entries in the archive.

        Args:
            namespace: ZIM namespace ('A' = articles, 'I' = images, etc.).
            mime_filter: Only yield entries matching this MIME prefix.

        Yields:
            DirectoryEntry objects.
        """
        for i in range(self._header.entry_count):
            entry = self._read_directory_entry(i)
            if namespace and entry.namespace != namespace:
                continue
            if entry.is_redirect:
                continue
            if mime_filter and not entry.mime_type.startswith(mime_filter):
                continue
            yield entry

    def __repr__(self) -> str:
        return (
            f"ZIMReader('{os.path.basename(self._filepath)}', "
            f"entries={self._header.entry_count}, "
            f"clusters={self._header.cluster_count})"
        )
