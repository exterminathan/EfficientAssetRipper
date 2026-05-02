"""Wrapper around voidtools Everything SDK (IPC via ctypes).

Requires Everything desktop app to be running. Uses Everything64.dll for
fast file-name searches across all indexed volumes.
"""

import ctypes
import ctypes.wintypes as wt
import os
import threading
from pathlib import Path
from typing import Optional

from _base import base_dir

# ---------------------------------------------------------------------------
# Everything SDK constants
# ---------------------------------------------------------------------------
EVERYTHING_OK = 0
EVERYTHING_ERROR_MEMORY = 1
EVERYTHING_ERROR_IPC = 2
EVERYTHING_ERROR_REGISTERCLASSEX = 3
EVERYTHING_ERROR_CREATEWINDOW = 4
EVERYTHING_ERROR_CREATETHREAD = 5
EVERYTHING_ERROR_INVALIDINDEX = 6
EVERYTHING_ERROR_INVALIDCALL = 7

EVERYTHING_REQUEST_FILE_NAME = 0x00000001
EVERYTHING_REQUEST_PATH = 0x00000002
EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME = 0x00000004
EVERYTHING_REQUEST_SIZE = 0x00000010

EVERYTHING_SORT_NAME_ASCENDING = 1

# LoadLibraryEx flag — restrict DLL search to System32 (no CWD/PATH planting)
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800

_ERROR_MESSAGES = {
    EVERYTHING_ERROR_MEMORY: "Out of memory",
    EVERYTHING_ERROR_IPC: "Everything service is not running",
    EVERYTHING_ERROR_REGISTERCLASSEX: "RegisterClassEx failed",
    EVERYTHING_ERROR_CREATEWINDOW: "CreateWindow failed",
    EVERYTHING_ERROR_CREATETHREAD: "CreateThread failed",
    EVERYTHING_ERROR_INVALIDINDEX: "Invalid index",
    EVERYTHING_ERROR_INVALIDCALL: "Invalid call",
}


class EverythingError(Exception):
    pass


def _normalize_folder(folder: str) -> str:
    """Normalize a folder path for Everything's path: filter.

    Everything expects backslashes and no trailing slash.
    """
    if not folder:
        return ""
    return folder.replace("/", "\\").rstrip("\\")


def _default_dll_candidates() -> list[str]:
    """Absolute candidate paths for Everything64.dll.

    Sources, in priority order:
    1. QSettings-configured `everything_dll`, if absolute.
    2. The bundled `data/` directory next to the install (handy for portable installs).
    3. The well-known voidtools install locations.

    Bare filenames are deliberately excluded — passing one to ``WinDLL`` would
    trigger the legacy DLL search order (CWD/PATH) and let an attacker plant a
    malicious ``Everything64.dll`` next to the EXE.
    """
    candidates: list[str] = []
    try:
        import config  # local import to avoid a hard dep at module-load time
        configured = config.get("everything_dll")
        if configured and os.path.isabs(configured):
            candidates.append(configured)
    except Exception:  # noqa: BLE001 — config is optional
        pass

    bundled = base_dir() / "data" / "Everything64.dll"
    candidates.append(str(bundled))
    candidates.append(r"C:\Program Files\Everything\Everything64.dll")
    candidates.append(r"C:\Program Files (x86)\Everything\Everything64.dll")
    return candidates


def _load_winddll(path: str):
    """Load a DLL with LOAD_LIBRARY_SEARCH_SYSTEM32 to block planting."""
    if not os.path.isabs(path):
        raise OSError(f"refusing to load non-absolute DLL path: {path!r}")
    try:
        return ctypes.WinDLL(path, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
    except TypeError:
        # Python < 3.8 fallback (winmode unsupported); we still required abs path.
        return ctypes.WinDLL(path)


class EverythingSDK:
    """Thin ctypes interface to Everything64.dll."""

    def __init__(self, dll_path: Optional[str] = None):
        if dll_path:
            if not os.path.isabs(dll_path):
                raise EverythingError(
                    f"Everything DLL path must be absolute (got {dll_path!r})"
                )
            try:
                self._dll = _load_winddll(dll_path)
            except OSError as e:
                raise EverythingError(
                    f"Could not load Everything DLL at {dll_path}: {e}"
                ) from e
        else:
            self._dll = None
            for candidate in _default_dll_candidates():
                if not os.path.isabs(candidate):
                    continue
                try:
                    self._dll = _load_winddll(candidate)
                    break
                except OSError:
                    continue
            if self._dll is None:
                raise EverythingError(
                    "Could not find Everything64.dll. "
                    "Set the path in Settings or install Everything."
                )

        self._setup_prototypes()

    # ------------------------------------------------------------------
    def _setup_prototypes(self):
        d = self._dll

        d.Everything_SetSearchW.argtypes = [ctypes.c_wchar_p]
        d.Everything_SetSearchW.restype = None

        d.Everything_SetRequestFlags.argtypes = [ctypes.c_uint32]
        d.Everything_SetRequestFlags.restype = None

        d.Everything_SetSort.argtypes = [ctypes.c_uint32]
        d.Everything_SetSort.restype = None

        d.Everything_SetMax.argtypes = [ctypes.c_uint32]
        d.Everything_SetMax.restype = None

        d.Everything_QueryW.argtypes = [ctypes.c_bool]
        d.Everything_QueryW.restype = ctypes.c_bool

        d.Everything_GetNumResults.argtypes = []
        d.Everything_GetNumResults.restype = ctypes.c_uint32

        d.Everything_GetLastError.argtypes = []
        d.Everything_GetLastError.restype = ctypes.c_uint32

        d.Everything_GetResultFullPathNameW.argtypes = [
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        d.Everything_GetResultFullPathNameW.restype = ctypes.c_uint32

    # ------------------------------------------------------------------
    def _check_error(self):
        err = self._dll.Everything_GetLastError()
        if err != EVERYTHING_OK:
            msg = _ERROR_MESSAGES.get(err, f"Unknown error ({err})")
            raise EverythingError(msg)

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int = 100,
        sort: int = EVERYTHING_SORT_NAME_ASCENDING,
    ) -> list[str]:
        """Run an Everything search query and return list of full paths.

        The query uses Everything's search syntax, e.g.:
            "XWing_01_Droid_C.tga"
            "ext:psk;pskx path:D:\\Games"
        """
        self._dll.Everything_SetSearchW(query)
        self._dll.Everything_SetRequestFlags(
            EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME
        )
        self._dll.Everything_SetSort(sort)
        self._dll.Everything_SetMax(max_results)

        ok = self._dll.Everything_QueryW(True)
        if not ok:
            self._check_error()

        count = self._dll.Everything_GetNumResults()
        # Defensive: never iterate beyond the requested cap (DLL has been
        # known to return larger counts under unusual conditions).
        if count > max_results:
            count = max_results

        results: list[str] = []
        for i in range(count):
            # Two-call pattern: query the buffer size, then allocate exactly.
            needed = self._dll.Everything_GetResultFullPathNameW(i, None, 0)
            if needed <= 0:
                continue
            buf = ctypes.create_unicode_buffer(needed + 1)
            self._dll.Everything_GetResultFullPathNameW(i, buf, needed + 1)
            results.append(buf.value)

        return results

    # ------------------------------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        """Test that Everything IPC is working. Returns (ok, message)."""
        try:
            # Run a trivial search
            self._dll.Everything_SetSearchW("")
            self._dll.Everything_SetMax(1)
            ok = self._dll.Everything_QueryW(True)
            if not ok:
                err = self._dll.Everything_GetLastError()
                msg = _ERROR_MESSAGES.get(err, f"Unknown error ({err})")
                return False, f"Query failed: {msg}"
            return True, "Everything SDK connected successfully"
        except Exception as e:
            return False, f"Exception: {e}"

    # ------------------------------------------------------------------
    def test_folder_search(self, folder: str) -> tuple[int, str]:
        """Test searching in a folder. Returns (count, message)."""
        folder = _normalize_folder(folder)
        query = f'path:"{folder}"'
        try:
            results = self.search(query, max_results=5)
            if results:
                return len(results), f"Found files, e.g.: {results[0]}"
            else:
                return 0, f"No files found under: {folder}\nQuery: {query}"
        except EverythingError as e:
            return 0, f"Search error: {e}"

    # ------------------------------------------------------------------
    def search_file(
        self,
        filename: str,
        extension: str = "",
        folder: str = "",
        max_results: int = 50,
    ) -> list[Path]:
        """Search for a file by name, optional extension, optional folder scope."""
        parts: list[str] = []
        if folder:
            parts.append(f'path:"{_normalize_folder(folder)}"')
        if extension:
            ext = extension.lstrip(".")
            # Use wfn: (whole filename) for exact filename matching
            parts.append(f'wfn:"{filename}.{ext}"')
        else:
            parts.append(f'wfn:"{filename}"')

        query = " ".join(parts)
        raw = self.search(query, max_results=max_results)
        return [Path(p) for p in raw]

    # ------------------------------------------------------------------
    def find_psk_files(self, folder: str = "") -> list[Path]:
        """Find all .psk and .pskx files, optionally scoped to a folder."""
        parts: list[str] = []
        if folder:
            parts.append(f'path:"{_normalize_folder(folder)}"')
        parts.append("ext:psk;pskx")
        query = " ".join(parts)
        raw = self.search(query, max_results=100_000)
        return [Path(p) for p in raw]

    # ------------------------------------------------------------------
    def find_texture(
        self, texture_name: str, folder: str = ""
    ) -> list[Path]:
        """Find a TGA texture file by its base name."""
        return self.search_file(texture_name, extension="tga", folder=folder)

    # ------------------------------------------------------------------
    def find_props_file(
        self, name: str, folder: str = "", max_results: int = 10_000
    ) -> list[Path]:
        """Find a .props.txt file by name (without extension).

        Default cap is 10k rather than 50; large UE5 projects routinely
        register the same texture or master material under thousands of paths
        and the closest-path tiebreaker can't pick the right one if the cap
        sliced off the candidate it needed. We log a warning when the result
        list hits the cap so an operator can investigate.
        """
        parts: list[str] = []
        if folder:
            parts.append(f'path:"{_normalize_folder(folder)}"')
        # Use a filename search for exact match
        parts.append(f'wfn:"{name}.props.txt"')
        query = " ".join(parts)
        raw = self.search(query, max_results=max_results)
        if len(raw) >= max_results:
            import logging
            logging.getLogger(__name__).warning(
                "find_props_file hit max_results=%d for %r — consider tightening folder scope",
                max_results, name,
            )
        return [Path(p) for p in raw]


# Singleton (lazily initialized)
_instance: Optional[EverythingSDK] = None
_instance_lock = threading.Lock()


def get_sdk(dll_path: Optional[str] = None) -> EverythingSDK:
    global _instance
    # Double-checked locking: avoid taking the lock on the hot path.
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EverythingSDK(dll_path)
    return _instance


def reset_sdk():
    global _instance
    with _instance_lock:
        old = _instance
        _instance = None
    if old is None:
        return
    # Drop the DLL handle so a stale path doesn't linger between settings changes.
    try:
        handle = getattr(old._dll, "_handle", None)
        if handle:
            import _ctypes  # type: ignore[attr-defined]
            try:
                _ctypes.FreeLibrary(handle)
            except Exception:  # noqa: BLE001 — best-effort
                pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            old._dll = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
