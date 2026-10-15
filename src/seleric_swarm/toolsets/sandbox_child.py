"""The sandbox's child process: runs one agent-authored script and prints its outcome as JSON.

Started by ``toolsets/sandbox.py`` as ``python -I sandbox_child.py`` with an empty environment, the mission's WORKDIR as
its working directory and the request on stdin (``{"code", "evidence", "workdir", "allowed_imports", "nonce"}``). Stdlib
only, so it needs no application import. The parent kills it on timeout, so a runaway script cannot outlive its budget.

Containment, from the inside out: resource limits (address space, CPU seconds, file size, open files); a restricted
builtins table with a guarded ``__import__``; and an audit hook, which Python cannot remove once installed, that refuses
process creation, network use, native-library loading, reads outside the interpreter's own files and WORKDIR, and writes
outside WORKDIR. The hook is the part that holds when the restricted builtins are sidestepped.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import resource
import sys
import traceback
from typing import Any

MEMORY_LIMIT_BYTES = 2 * 1024**3
FILE_SIZE_LIMIT_BYTES = 64 * 1024**2
OPEN_FILES_LIMIT = 64
STDOUT_CAP_CHARS = 1_000_000

# Events that are refused outright while the script runs.
_DENIED_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.kill",
    "os.killpg", "os.setuid", "os.setgid", "os.chroot", "os.putenv", "os.unsetenv",
    "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyaddr", "socket.__new__",
    "ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function", "ctypes.cdata", "ctypes.addressof",
    "ctypes.create_string_buffer", "ctypes.create_unicode_buffer", "ctypes.get_errno", "ctypes.set_errno",
    "mmap.__new__", "pty.spawn", "webbrowser.open", "urllib.Request", "http.client.connect",
})
# Imports that give a way around the table above (they load the native pieces the denied events guard).
_DENIED_IMPORTS = frozenset({
    "_posixsubprocess", "subprocess", "ctypes", "_ctypes", "socket", "_socket", "multiprocessing", "pty", "mmap",
    "ssl", "_ssl", "urllib", "http", "ftplib", "smtplib", "telnetlib", "asyncio", "threading", "_thread", "signal",
    "resource", "fcntl", "termios", "importlib", "imp", "runpy", "code", "codeop", "pickle", "marshal", "shelve",
    "sysconfig", "site",
})
# Filesystem changes: every path argument must resolve under WORKDIR.
_WRITE_EVENTS = frozenset({
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.symlink", "os.link", "os.truncate",
    "os.mkfifo", "os.mknod", "os.utime", "shutil.copyfile", "shutil.copytree", "shutil.rmtree", "shutil.move",
    "shutil.copymode", "shutil.copystat", "shutil.chown",
})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _limits(cpu_seconds: int) -> None:
    for name, soft in (
        ("RLIMIT_AS", MEMORY_LIMIT_BYTES), ("RLIMIT_CPU", cpu_seconds), ("RLIMIT_FSIZE", FILE_SIZE_LIMIT_BYTES),
        ("RLIMIT_NOFILE", OPEN_FILES_LIMIT), ("RLIMIT_CORE", 0),
    ):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(getattr(resource, name), (soft, soft))


def _under(path: Any, roots: tuple[str, ...]) -> bool:
    try:
        real = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return False
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def _install_hook(workdir: str) -> Any:
    """Install the audit hook (inactive until the returned ``arm`` is called, so the interpreter's own set-up is free)."""
    work = os.path.realpath(workdir)
    interpreter = tuple({os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)})
    readable = (work, *interpreter)
    armed = {"on": False}

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if not armed["on"]:
            return
        if event in _DENIED_EVENTS or event.startswith("ctypes."):
            raise PermissionError(f"{event} is not allowed in the sandbox")
        if event == "import":
            if str(args[0]).split(".")[0] in _DENIED_IMPORTS:
                raise ImportError(f"import of '{args[0]}' is blocked in the sandbox")
            return
        if event == "open":
            path, mode, flags = args[0], args[1], args[2]
            if isinstance(path, int):
                return
            writing = bool(flags & _WRITE_FLAGS) if isinstance(flags, int) else any(c in str(mode or "") for c in "wax+")
            if not _under(path, (work,) if writing else readable):
                raise PermissionError(f"{'writing' if writing else 'reading'} {os.fsdecode(path)!r} is outside WORKDIR")
            return
        if event in _WRITE_EVENTS:
            for arg in args:
                if isinstance(arg, (str, bytes, os.PathLike)) and not _under(arg, (work,)):
                    raise PermissionError(f"{event} outside WORKDIR is not allowed in the sandbox")

    sys.addaudithook(hook)

    def arm() -> None:
        armed["on"] = True

    return arm


def _default(value: Any) -> Any:
    """JSON form of what a script may leave in ``result``: numpy / pandas values, decimals, dates, sets."""
    for attr in ("item", "tolist", "to_dict", "isoformat"):
        fn = getattr(value, attr, None)
        if callable(fn):
            with contextlib.suppress(Exception):
                return fn(orient="records") if attr == "to_dict" and type(value).__name__ == "DataFrame" else fn()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _guarded_import(os_facade: Any, allowed: frozenset[str]) -> Any:
    def guarded(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root == "os":
            return os_facade
        if root not in allowed:
            raise ImportError(f"import of '{name}' is blocked in the sandbox; allowed: {sorted(allowed)} (os is pre-provided)")
        return builtins.__import__(name, *args, **kwargs)

    return guarded


class _Os:
    """``os``-like facade: path helpers and a listing of WORKDIR, nothing else."""

    def __init__(self, workdir: str) -> None:
        self.path = os.path
        self._workdir = workdir

    def listdir(self, path: str | None = None) -> list[str]:
        return os.listdir(path or self._workdir)


_UNSAFE_BUILTINS = frozenset({
    "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "input", "exit", "quit", "help", "breakpoint",
    "memoryview",
})


def main() -> None:
    request = json.loads(sys.stdin.read())
    workdir = request["workdir"]
    nonce = request["nonce"]
    allowed = frozenset(request["allowed_imports"])
    _limits(int(request.get("cpu_seconds") or 12))
    os.chdir(workdir)
    for name in sorted(allowed):  # heavy optional libraries load before the hook is armed, so their own imports run free
        if name in request["code"] and name in ("numpy", "pandas"):
            with contextlib.suppress(Exception):
                __import__(name)
    arm = _install_hook(workdir)

    os_facade = _Os(workdir)
    safe = {n: getattr(builtins, n) for n in dir(builtins) if not n.startswith("_") and n not in _UNSAFE_BUILTINS}
    safe["__import__"] = _guarded_import(os_facade, allowed)
    namespace: dict[str, Any] = {"__builtins__": safe, "evidence": request["evidence"], "WORKDIR": workdir, "os": os_facade}
    safe["globals"] = safe["locals"] = lambda: namespace
    safe["vars"] = lambda obj=namespace: namespace if obj is namespace else builtins.vars(obj)
    defined = sorted(k for k in namespace if not k.startswith("__"))

    out: dict[str, Any] = {"defined": defined}
    stdout = io.StringIO()
    try:
        code = builtins.compile(request["code"], "<sandbox>", "exec")
        arm()
        with contextlib.redirect_stdout(stdout):
            builtins.exec(code, namespace)  # noqa: S102 - the point of this process
        out["result"] = namespace.get("result")
    except BaseException as exc:  # SystemExit from exit() too: reported, never silent
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["error_type"] = type(exc).__name__
        out["tb"] = traceback.format_exc()[-800:]
    printed = stdout.getvalue()
    out["stdout"] = printed[:STDOUT_CAP_CHARS]
    try:
        text = json.dumps(out, default=_default, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        out.pop("result", None)
        out["error"], out["error_type"] = f"result is not JSON-serialisable: {exc}", "TypeError"
        text = json.dumps(out, default=_default, ensure_ascii=False)
    real_stdout = sys.__stdout__ or sys.stdout
    real_stdout.write(f"\n{nonce}{text}\n")
    real_stdout.flush()


if __name__ == "__main__":
    main()
