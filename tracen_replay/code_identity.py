"""Stable digests of code objects for engine fingerprints.

``marshal.dumps(code)`` is not a stable identity: its byte stream records
which embedded objects were already shared (reference counts and interning at
the moment of the call), so the same function marshals differently in a fresh
worker process and in a long-running main process, and even twice in one
process.  A fingerprint built on it cannot be compared across processes.

:func:`code_digest` hashes what the code *does*: its bytecode, argument
layout, names and constants, recursing into nested code objects.  File names,
line tables and interning state are left out, so the digest is equal for the
same source text wherever and whenever it is compiled by the same interpreter
version.  Set constants are ordered before hashing so the process hash seed
cannot influence the result.
"""
from __future__ import annotations

import hashlib
import types


def _constant_text(value) -> str:
    if isinstance(value, types.CodeType):
        return '<code:' + code_digest(value).hexdigest() + '>'
    if isinstance(value, (frozenset, set)):
        return '{' + ','.join(sorted(_constant_text(item) for item in value)) + '}'
    if isinstance(value, tuple):
        return '(' + ','.join(_constant_text(item) for item in value) + ')'
    return type(value).__name__ + ':' + repr(value)


def code_digest(code: types.CodeType, digest=None):
    """Return a SHA-256 digest object identifying ``code`` by its behaviour."""
    digest = hashlib.sha256() if digest is None else digest
    digest.update(code.co_code)
    digest.update(repr((code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags,
                        code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars)).encode())
    for constant in code.co_consts:
        digest.update(_constant_text(constant).encode())
        digest.update(b'\x00')
    return digest


def function_digest(function) -> bytes:
    """The digest bytes of a plain function or method's code."""
    return code_digest(function.__code__).digest()


def package_digest(package_dir=None) -> str:
    """SHA-256 over every ``.py`` file of the analyzer package, by relative path.

    This is the worker's code identity reported to callers (``worker_version``
    in the job envelope): the same source tree hashes the same wherever it is
    installed, and any source change changes it.
    """
    import os
    root = os.path.dirname(os.path.abspath(__file__)) if package_dir is None else os.fspath(package_dir)
    digest = hashlib.sha256()
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
        for name in sorted(filenames):
            if not name.endswith('.py'):
                continue
            path = os.path.join(directory, name)
            digest.update(os.path.relpath(path, root).replace(os.sep, '/').encode('utf-8'))
            digest.update(b'\x00')
            with open(path, 'rb') as stream:
                digest.update(stream.read())
            digest.update(b'\x00')
    return digest.hexdigest()
