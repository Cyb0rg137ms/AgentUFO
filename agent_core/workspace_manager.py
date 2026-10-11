"""
workspace_manager.py
====================
Real-filesystem workspace manager with seat-based path ownership,
atomic file writes, durable lock persistence, and crash recovery.

Enforces that crew members (seats) edit real files on disk (not browser memory)
with strict mutual exclusion on paths.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from functools import wraps
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from agent_core.permissions import WorkspacePolicy, PermissionDenied


def _synchronized(method):
    """Serialize in-process access to shared lock state using a re-entrant mutex."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._mutex:
            return method(self, *args, **kwargs)
    return wrapper


class PathLockConflictError(Exception):
    """Raised when a seat tries to write to a path locked by another seat."""
    pass


class PathNotFoundError(Exception):
    """Raised when an operation targets a nonexistent path."""
    pass


@dataclass
class PathLock:
    """Represents an active lock on a specific file path."""
    path: str
    seat_id: str
    seat_role: str
    token: str
    acquired_at: float
    timeout_seconds: float = 600.0  # 10 minutes default lease

    def is_expired(self) -> bool:
        return (time.time() - self.acquired_at) > self.timeout_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "seat_id": self.seat_id,
            "seat_role": self.seat_role,
            "token": self.token,
            "acquired_at": self.acquired_at,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PathLock:
        return cls(
            path=data["path"],
            seat_id=data["seat_id"],
            seat_role=data.get("seat_role", "worker"),
            token=data["token"],
            acquired_at=data["acquired_at"],
            timeout_seconds=data.get("timeout_seconds", 600.0),
        )


class WorkspaceManager:
    """
    Manages an on-disk project workspace with seat-level path locking,
    durable lock state in .agentufo/locks.json, and atomic mutations.
    """

    def __init__(self, workspace_root: Optional[Union[str, Path]] = None, root_dir: Optional[Union[str, Path]] = None,
                 policy: Optional[WorkspacePolicy] = None) -> None:
        self._mutex = threading.RLock()
        self.root = Path(workspace_root or root_dir or os.getcwd()).resolve()
        if policy is not None and policy.root != self.root:
            raise ValueError("WorkspacePolicy.root must match workspace_root")
        self.policy = policy or WorkspacePolicy(root=self.root)
        self.agentufo_dir = self.root / ".agentufo"
        self.locks_file = self.agentufo_dir / "locks.json"
        self.staging_dir = self.agentufo_dir / "staging"
        self._locks: Dict[str, PathLock] = {}
        self._ensure_dirs()
        self._load_locks()

    @_synchronized
    def rebase_root(self, new_root: Union[str, Path]) -> None:
        """Re-targets workspace to an active project directory, reloading locks."""
        self.root = Path(new_root).resolve()
        self.policy = WorkspacePolicy(root=self.root)
        self.agentufo_dir = self.root / ".agentufo"
        self.locks_file = self.agentufo_dir / "locks.json"
        self.staging_dir = self.agentufo_dir / "staging"
        self._ensure_dirs()
        self._load_locks()

    def _ensure_dirs(self) -> None:
        """Ensures workspace, .agentufo, backups, and staging directories exist."""
        self.agentufo_dir.mkdir(parents=True, exist_ok=True)
        (self.agentufo_dir / "backups").mkdir(parents=True, exist_ok=True)
        (self.agentufo_dir / "staging").mkdir(parents=True, exist_ok=True)

    def _normalize_rel_path(self, target_path: Union[str, Path], operation: str = "read") -> str:
        """Normalize a path and reject traversal or symlink escapes from the workspace."""
        p = Path(target_path)
        if any(part == ".." for part in p.parts):
            raise ValueError("Path traversal is not allowed")
        candidate = p if p.is_absolute() else self.root / p
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path is outside the active workspace")
        rel = resolved.relative_to(self.root)
        if not rel.parts:
            raise ValueError("Workspace root is not a file path")
        try:
            self.policy.resolve(resolved, operation)
        except PermissionDenied as exc:
            raise ValueError(str(exc)) from exc
        return str(rel).replace("\\", "/")

    @_synchronized
    def _load_locks(self) -> None:
        """Loads durable path locks from disk, purging expired ones."""
        if not self.locks_file.exists():
            self._locks = {}
            return

        try:
            with open(self.locks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._locks = {}
            for path_str, lock_data in data.items():
                lock = PathLock.from_dict(lock_data)
                if not lock.is_expired():
                    self._locks[path_str] = lock
        except Exception:
            self._locks = {}

    @_synchronized
    def _persist_locks(self) -> None:
        """Atomically saves active path locks to disk."""
        data = {p: lock.to_dict() for p, lock in self._locks.items()}
        temp_file = self.locks_file.with_name(f"locks.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, self.locks_file)
        finally:
            try:
                temp_file.unlink(missing_ok=True)
            except OSError:
                pass

    @_synchronized
    def acquire_lock(
        self,
        target_path: Union[str, Path],
        seat_id: str,
        seat_role: str = "builder",
        lease_seconds: float = 600.0,
    ) -> PathLock:
        """
        Acquires an exclusive lock on target_path for a seat.
        Raises PathLockConflictError if already locked by a different seat.
        """
        rel_path = self._normalize_rel_path(target_path)
        existing = self._locks.get(rel_path)

        if existing and not existing.is_expired():
            if existing.seat_id != seat_id:
                raise PathLockConflictError(
                    f"Path '{rel_path}' is currently locked by {existing.seat_id} ({existing.seat_role}). "
                    f"Seat '{seat_id}' cannot acquire lock."
                )
            # Re-acquire/extend lease for the same seat
            existing.acquired_at = time.time()
            existing.timeout_seconds = lease_seconds
            self._persist_locks()
            return existing

        token = uuid.uuid4().hex
        lock = PathLock(
            path=rel_path,
            seat_id=seat_id,
            seat_role=seat_role,
            token=token,
            acquired_at=time.time(),
            timeout_seconds=lease_seconds,
        )
        self._locks[rel_path] = lock
        self._persist_locks()
        return lock

    @_synchronized
    def release_lock(self, target_path: Union[str, Path], seat_id: str) -> bool:
        """Releases a lock if held by seat_id."""
        rel_path = self._normalize_rel_path(target_path)
        lock = self._locks.get(rel_path)
        if not lock:
            return True

        if lock.seat_id == seat_id or seat_id == "master_admin":
            del self._locks[rel_path]
            self._persist_locks()
            return True
        return False

    @_synchronized
    def release_all_for_seat(self, seat_id: str) -> int:
        """Releases all path locks held by a seat."""
        released = 0
        to_del = [p for p, lock in self._locks.items() if lock.seat_id == seat_id]
        for p in to_del:
            del self._locks[p]
            released += 1
        if released:
            self._persist_locks()
        return released

    @_synchronized
    def get_lock(self, target_path: Union[str, Path]) -> Optional[PathLock]:
        """Gets active lock for path if any and not expired."""
        rel_path = self._normalize_rel_path(target_path)
        lock = self._locks.get(rel_path)
        if lock and not lock.is_expired():
            return lock
        return None

    @_synchronized
    def is_locked(self, target_path: Union[str, Path]) -> bool:
        """Returns True if the path is locked and not expired."""
        lock = self.get_lock(target_path)
        return lock is not None

    @_synchronized
    def get_lock_owner(self, target_path: Union[str, Path]) -> Optional[str]:
        """Returns the seat_id holding the lock on target_path, or None."""
        lock = self.get_lock(target_path)
        return lock.seat_id if lock else None

    @_synchronized
    def list_active_locks(self) -> List[PathLock]:
        """Returns all currently active, unexpired path locks."""
        self._load_locks()
        return [lock for lock in self._locks.values() if not lock.is_expired()]

    @_synchronized
    def claim_paths(
        self,
        seat_id: str,
        paths: List[str],
        seat_role: str = "builder",
        lease_seconds: float = 600.0,
    ) -> Dict[str, Any]:
        """
        Atomically claims multiple real disk paths for a seat (matching agentufo-studio ops.ts).
        If any path is locked by another seat (even surviving crash/restart), the second seat is denied!
        """
        self._load_locks()
        granted: List[str] = []
        denied: List[Dict[str, Any]] = []

        for p_raw in paths:
            rel = self._normalize_rel_path(p_raw)
            existing = self.get_lock(rel)
            if existing and existing.seat_id != seat_id:
                denied.append({
                    "path": rel,
                    "locked_by": existing.seat_id,
                    "seat_role": existing.seat_role,
                    "acquired_at": existing.acquired_at,
                    "expires_in_seconds": round(max(0.0, (existing.acquired_at + existing.timeout_seconds) - time.time()), 1),
                })
            else:
                granted.append(rel)

        # Only acquire if all requested paths can be locked without conflict
        if not denied:
            for rel in granted:
                self.acquire_lock(rel, seat_id=seat_id, seat_role=seat_role, lease_seconds=lease_seconds)

        return {
            "success": len(denied) == 0,
            "seat_id": seat_id,
            "granted": granted,
            "denied": denied,
        }

    @_synchronized
    def break_lock(self, target_path: Union[str, Path], breaker_id: str = "master_admin", reason: str = "") -> bool:
        """
        Force-breaks a stale or dead lock on disk with audit trail in .agentufo/lock_audit.json.
        """
        rel_path = self._normalize_rel_path(target_path)
        lock = self.get_lock(rel_path)
        if not lock:
            return True

        # Write audit trail
        audit_file = self.agentufo_dir / "lock_audit.json"
        audit_entries = []
        if audit_file.exists():
            try:
                with open(audit_file, "r", encoding="utf-8") as f:
                    audit_entries = json.load(f)
            except Exception:
                pass
        audit_entries.append({
            "action": "break_lock",
            "path": rel_path,
            "previous_owner": lock.seat_id,
            "broken_by": breaker_id,
            "reason": reason or "Administrative override / crash purge",
            "timestamp": time.time(),
        })
        try:
            with open(audit_file, "w", encoding="utf-8") as f:
                json.dump(audit_entries[-50:], f, indent=2)
        except Exception:
            pass

        if rel_path in self._locks:
            del self._locks[rel_path]
            self._persist_locks()
        return True

    # -------------------------------------------------------------------------
    # Atomic Staging & Merge Gate Support (Repo Tool Pattern)
    # -------------------------------------------------------------------------

    @_synchronized
    def stage_file(self, target_path: Union[str, Path], content: str, seat_id: str) -> Path:
        """
        Writes proposed file changes into the workspace staging area.
        Does NOT corrupt the repository file before Review Gate verification!
        """
        rel_path = self._normalize_rel_path(target_path, operation="write")
        seat_stage_dir = self.staging_dir / seat_id
        seat_stage_dir.mkdir(parents=True, exist_ok=True)

        staged_path = seat_stage_dir / rel_path
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        with open(staged_path, "w", encoding="utf-8") as f:
            f.write(content)
        return staged_path

    @_synchronized
    def get_staged_content(self, target_path: Union[str, Path], seat_id: str) -> Optional[str]:
        """Reads staged content for target_path if it exists."""
        rel_path = self._normalize_rel_path(target_path)
        staged_path = self.staging_dir / seat_id / rel_path
        if staged_path.exists() and staged_path.is_file():
            return staged_path.read_text(encoding="utf-8", errors="replace")
        return None

    @_synchronized
    def commit_staged(self, target_path: Union[str, Path], seat_id: str) -> Path:
        """
        Atomically commits staged file into real workspace destination path upon review pass.
        """
        rel_path = self._normalize_rel_path(target_path, operation="write")
        staged_path = self.staging_dir / seat_id / rel_path
        if not staged_path.exists():
            raise PathNotFoundError(f"No staged changes found for '{rel_path}' by seat '{seat_id}'.")

        content = staged_path.read_text(encoding="utf-8", errors="replace")
        committed_path = self.write_file_atomic(rel_path, content, seat_id=seat_id)

        # Clean up staging file
        try:
            staged_path.unlink()
        except Exception:
            pass

        return committed_path

    @_synchronized
    def discard_staged(self, target_path: Union[str, Path], seat_id: str) -> bool:
        """
        Discards staged file upon Review Gate rejection so the staged change is not applied.
        """
        rel_path = self._normalize_rel_path(target_path)
        staged_path = self.staging_dir / seat_id / rel_path
        if staged_path.exists():
            try:
                staged_path.unlink()
                return True
            except Exception:
                pass
        return False

    @_synchronized
    def diff_staged(self, target_path: Union[str, Path], seat_id: str) -> str:
        """
        Generates Git-style unified diff between existing workspace file and staged candidate.
        """
        import difflib
        rel_path = self._normalize_rel_path(target_path)
        full_path = self.root / rel_path
        original_lines = []
        if full_path.exists() and full_path.is_file():
            original_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()

        staged_content = self.get_staged_content(rel_path, seat_id)
        if staged_content is None:
            return ""

        staged_lines = staged_content.splitlines()
        diff = difflib.unified_diff(
            original_lines,
            staged_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
        )
        return "\n".join(diff)

    @_synchronized
    def write_file_atomic(
        self,
        target_path: Union[str, Path],
        content: str,
        seat_id: str,
        create_backup: bool = True,
    ) -> Path:
        """
        Writes content to disk atomically.
        Validates that seat_id owns the path lock!
        """
        rel_path = self._normalize_rel_path(target_path, operation="write")
        lock = self.get_lock(rel_path)

        if not lock:
            # Auto-acquire lock if unowned
            self.acquire_lock(rel_path, seat_id=seat_id)
        elif lock.seat_id != seat_id and seat_id != "master_admin":
            raise PathLockConflictError(
                f"Cannot write '{rel_path}': Path is locked by {lock.seat_id} ({lock.seat_role}). "
                f"Seat '{seat_id}' lacks write authorization."
            )

        full_path = self.root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Create backup if file already exists
        if full_path.exists() and create_backup:
            bak_path = self.agentufo_dir / "backups" / rel_path
            bak_path.parent.mkdir(parents=True, exist_ok=True)
            bak_path = bak_path.with_name(f"{bak_path.name}.{time.time_ns()}.bak")
            shutil.copy2(str(full_path), str(bak_path))

        temp_path = full_path.with_name(f".{full_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, full_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return full_path

    @_synchronized
    def read_file(self, target_path: Union[str, Path]) -> str:
        """Reads file content from workspace."""
        rel_path = self._normalize_rel_path(target_path)
        full_path = self.root / rel_path
        if not full_path.exists() or not full_path.is_file():
            raise PathNotFoundError(f"File '{rel_path}' does not exist in workspace.")
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    @_synchronized
    def get_workspace_tree(self, max_depth: int = 4) -> List[Dict[str, Any]]:
        """Returns hierarchical file tree with lock metadata."""
        tree = []
        ignored = {".git", ".agentufo", "__pycache__", "node_modules", ".pytest_cache", ".venv", "venv"}

        def scan_dir(dir_path: Path, current_depth: int) -> List[Dict[str, Any]]:
            if current_depth > max_depth:
                return []
            items = []
            try:
                for entry in sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if entry.name in ignored:
                        continue
                    rel = self._normalize_rel_path(entry)
                    if entry.is_dir():
                        items.append({
                            "name": entry.name,
                            "path": rel,
                            "is_dir": True,
                            "children": scan_dir(entry, current_depth + 1),
                        })
                    else:
                        lock = self.get_lock(rel)
                        items.append({
                            "name": entry.name,
                            "path": rel,
                            "is_dir": False,
                            "size": entry.stat().st_size,
                            "locked": lock is not None,
                            "locked_by": lock.seat_id if lock else None,
                            "locked_role": lock.seat_role if lock else None,
                        })
            except Exception:
                pass
            return items

        return scan_dir(self.root, 1)
