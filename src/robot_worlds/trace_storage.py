"""Lossless trace checkpoints with bounded serialization memory.

Gzip files contain an ordinary JSON array after standard decompression. Complete
members keep each atomic checkpoint readable; a later append replaces only the
closing member. No measurements, samples, precision or object identities are lost.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path


class StatePool:
    """Share exactly equal object records across owned, immutable snapshots."""

    def __init__(self):
        self.states = {}
        self.identifiers = {}
        self.signatures = {}

    def retain(self, frame):
        if not isinstance(frame, dict) or not isinstance(frame.get('objects'), dict):
            return frame
        objects = {}
        for identifier, state in frame['objects'].items():
            identifier = self.identifiers.setdefault(identifier, identifier)
            previous = self.states.get(identifier)
            # JSON encoding preserves bool/int/float types and signed zero.
            # Its C encoder avoids recursively comparing thousands of unchanged
            # scene objects in Python at every sample. Key order may miss a
            # sharing opportunity, but can never merge different measurements.
            signature = json.dumps(state, separators=(',', ':'), allow_nan=False)
            if self.signatures.get(identifier) == signature:
                state = previous
            else:
                self.states[identifier] = state
                self.signatures[identifier] = signature
            objects[identifier] = state
        frame['objects'] = objects
        return frame


class TraceCheckpoint:
    """Atomic append checkpoints for an owned, immutable native trace.

    Custom adapters that mutate history must use a fresh writer for each save.
    Serialization holds at most one sample, not a second copy of the full trace.
    """

    def __init__(self, path: Path):
        self.path = path
        self.frames = []
        self.prefix_end = None
        self.file_size = None

    def _chunk(self, stream):
        return (gzip.GzipFile(filename='', mode='wb', fileobj=stream, compresslevel=1, mtime=0)
                if self.path.suffix == '.gz' else nullcontext(stream))

    def write(self, trace):
        append = self.prefix_end is not None and len(trace) >= len(self.frames) and all(
            a is b for a, b in zip(self.frames, trace))
        if append and len(trace) == len(self.frames):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.writing-trace-', dir=self.path.parent)
        os.close(fd)
        try:
            if append:
                if self.path.stat().st_size != self.file_size:
                    raise ValueError('Trace checkpoint changed outside its writer')
                shutil.copyfile(self.path, temporary)
            with open(temporary, 'r+b') as stream:
                if append:
                    stream.truncate(self.prefix_end)
                    stream.seek(self.prefix_end)
                    first = len(self.frames)
                else:
                    first = 0
                    with self._chunk(stream) as chunk:
                        chunk.write(b'[')
                with self._chunk(stream) as chunk:
                    for index in range(first, len(trace)):
                        if index:
                            chunk.write(b',')
                        chunk.write(json.dumps(trace[index], separators=(',', ':'), allow_nan=False).encode())
                prefix_end = stream.tell()
                with self._chunk(stream) as chunk:
                    chunk.write(b']\n')
                file_size = stream.tell()
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.frames = list(trace)
        self.prefix_end, self.file_size = prefix_end, file_size


def read_trace(path: Path) -> list:
    """Read legacy JSON or gzip JSON one sample at a time, pooling exact repeats."""
    opener = gzip.open if path.suffix == '.gz' else open
    decoder, pool, result = json.JSONDecoder(), StatePool(), []
    buffer, eof = '', False
    with opener(path, 'rt', encoding='utf-8') as stream:
        def fill():
            nonlocal buffer, eof
            chunk = stream.read(1024*1024)
            eof = not chunk
            buffer += chunk
            if len(buffer) > 64*1024*1024:
                raise ValueError('A single trace sample exceeds the 64 MiB read budget')

        def available():
            nonlocal buffer
            buffer = buffer.lstrip()
            while not buffer and not eof:
                fill()
                buffer = buffer.lstrip()
            return bool(buffer)

        if not available() or buffer[0] != '[':
            raise ValueError('Trace must be a JSON array')
        buffer = buffer[1:]
        if not available():
            raise ValueError('Truncated trace array')
        if buffer[0] != ']':
            while True:
                while True:
                    try:
                        frame, end = decoder.raw_decode(buffer)
                        break
                    except json.JSONDecodeError:
                        if eof:
                            raise ValueError('Malformed or truncated trace sample') from None
                        fill()
                result.append(pool.retain(frame))
                buffer = buffer[end:]
                if not available() or buffer[0] not in ',]':
                    raise ValueError('Missing trace array delimiter')
                if buffer[0] == ']':
                    break
                buffer = buffer[1:]
                if not available() or buffer[0] == ']':
                    raise ValueError('Missing trace sample after comma')
        buffer = buffer[1:]
        if available():
            raise ValueError('Unexpected data after trace array')
    return result
