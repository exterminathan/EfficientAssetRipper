"""Built-in file search fallback for when Everything isn't available.

Mirrors the public surface of ``core.everything.EverythingSDK`` so the rest of
the app can keep calling the same methods. The walker is lazy: nothing is
indexed until the first query, and the index is cached for the process lifetime
keyed by absolute folder path. Switching to a different folder rebuilds.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class WalkSearcher:
    """Drop-in replacement for ``EverythingSDK`` backed by ``os.walk``.

    Significantly slower than Everything on first query (a typical unpacked
    UE5 game folder has 50k–500k files; the initial walk runs in a few
    seconds to half a minute). Subsequent queries hit the in-memory index.
    """

    def __init__(self) -> None:
        self._index_lock = threading.Lock()
        self._indexed_folder: str = ""
        # Lower-cased extension (incl. leading dot) → list of Paths
        self._by_ext: dict[str, list[Path]] = {}
        # Lower-cased file basename → list of Paths
        self._by_name: dict[str, list[Path]] = {}
        # Special index for ``.props.txt`` files (compound extension)
        self._props_files: list[Path] = []

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _ensure_indexed(self, folder: str) -> None:
        """Walk *folder* once. Re-walks on folder change or if not yet built."""
        target = os.path.abspath(folder) if folder else ""
        with self._index_lock:
            if target == self._indexed_folder and self._by_ext:
                return
            self._by_ext = {}
            self._by_name = {}
            self._props_files = []
            self._indexed_folder = target
            if not target or not os.path.isdir(target):
                return
            log.info("WalkSearcher: indexing %s (this may take a moment)…", target)
            count = 0
            for root, _dirs, files in os.walk(target):
                for fname in files:
                    full = Path(root) / fname
                    lower = fname.lower()
                    self._by_name.setdefault(lower, []).append(full)
                    if lower.endswith(".props.txt"):
                        self._props_files.append(full)
                        # Also record under .txt so generic ext lookup still works.
                        self._by_ext.setdefault(".txt", []).append(full)
                    else:
                        ext = os.path.splitext(lower)[1]
                        if ext:
                            self._by_ext.setdefault(ext, []).append(full)
                    count += 1
            log.info("WalkSearcher: indexed %d files under %s", count, target)

    # ------------------------------------------------------------------
    # EverythingSDK-shaped API
    # ------------------------------------------------------------------

    def find_psk_files(self, folder: str = "") -> list[Path]:
        self._ensure_indexed(folder)
        return list(self._by_ext.get(".psk", [])) + list(self._by_ext.get(".pskx", []))

    def find_texture(self, texture_name: str, folder: str = "") -> list[Path]:
        return self.search_file(texture_name, extension="tga", folder=folder)

    def find_props_file(
        self, name: str, folder: str = "", max_results: int = 10_000
    ) -> list[Path]:
        self._ensure_indexed(folder)
        target_name = f"{name}.props.txt".lower()
        matches = [p for p in self._props_files if p.name.lower() == target_name]
        if len(matches) >= max_results:
            log.warning(
                "find_props_file (walker) hit max_results=%d for %r",
                max_results, name,
            )
            return matches[:max_results]
        return matches

    def search_file(
        self,
        filename: str,
        extension: str = "",
        folder: str = "",
        max_results: int = 50,
    ) -> list[Path]:
        self._ensure_indexed(folder)
        target_stem = filename.lower()
        if extension:
            ext = "." + extension.lstrip(".").lower()
            candidates = self._by_ext.get(ext, [])
            target = f"{target_stem}{ext}"
            return [p for p in candidates if p.name.lower() == target][:max_results]
        # No extension specified — match by stem across any extension.
        out: list[Path] = []
        for fname, paths in self._by_name.items():
            stem = os.path.splitext(fname)[0]
            # Strip the second extension segment for ``.props.txt`` files
            if fname.endswith(".props.txt"):
                stem = fname[: -len(".props.txt")]
            if stem == target_stem:
                out.extend(paths)
                if len(out) >= max_results:
                    return out[:max_results]
        return out

    def search(self, query: str, max_results: int = 100, sort: int = 0) -> list[str]:
        """Compatibility shim — accepts an Everything-style query string.

        We don't try to parse the full Everything syntax. Anything that calls
        this directly (instead of the typed helpers) won't be accelerated by
        the walker — return an empty list so callers degrade gracefully.
        """
        log.debug("WalkSearcher.search(%r) is unsupported — returning []", query)
        return []

    def test_connection(self) -> tuple[bool, str]:
        return True, "Built-in walker (Everything not detected)"

    def test_folder_search(self, folder: str) -> tuple[int, str]:
        self._ensure_indexed(folder)
        total = sum(len(v) for v in self._by_ext.values())
        if total <= 0:
            return 0, f"No files indexed under: {folder}"
        # Pick any sample for the message
        sample = ""
        for paths in self._by_ext.values():
            if paths:
                sample = str(paths[0])
                break
        return total, f"Indexed {total:,} files, e.g.: {sample}"
