#!/usr/bin/env python3
"""Follow present and future harness logs into one bounded-memory stream."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time

CHUNK = 64 * 1024
EXTRA = {
    'rolling-queue.log': 'queue',
    'deepagents-infra-repair.log': 'deepagents-repair',
    'gc-ua-queue.log': 'gc-ua-queue',
    'bwrap-pass3-queue.log': 'legacy-queue',
    'bwrap-remaining-queue.log': 'legacy-remaining',
}


class Follower:
    def __init__(self, directory, output, state, initial_tail=128 * 1024):
        self.directory, self.output, self.state_path = map(Path, (directory, output, state))
        self.initial_tail = initial_tail
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        # Only files present at startup are tailed; later discoveries start at byte 0.
        self.initial = {str(path) for path, _ in self.sources()}

    def sources(self):
        paths = {path: path.name.removesuffix('-bwrap-pass3.log')
                 for path in self.directory.glob('*-bwrap-pass3.log')}
        paths.update({self.directory / name: label for name, label in EXTRA.items()
                      if (self.directory / name).is_file()})
        return sorted(paths.items())

    def pump(self):
        changed = False
        with self.output.open('ab') as output:
            for path, label in self.sources():
                try:
                    with path.open('rb') as source:
                        info = os.fstat(source.fileno())
                        key, identity = str(path), [info.st_dev, info.st_ino]
                        previous = self.state.get(key)
                        if previous and previous['identity'] == identity and previous['offset'] <= info.st_size:
                            offset = previous['offset']
                        elif previous:
                            offset = 0  # Replaced or truncated log.
                        else:
                            offset = max(0, info.st_size - self.initial_tail) if key in self.initial else 0
                            source.seek(offset)
                            if offset:
                                source.readline(CHUNK)  # Skip an initial partial line.
                                offset = source.tell()
                        source.seek(offset)
                        consumed = 0
                        while consumed < 4 * CHUNK:  # Bound bytes, not the number of progress lines.
                            line = source.readline(CHUNK)
                            if not line:
                                break
                            if not line.endswith(b'\n') and len(line) < CHUNK:
                                break  # Wait for a complete line without buffering it.
                            output.write(b'[' + label.encode() + b'] ' + line)
                            if not line.endswith(b'\n'):
                                output.write(b' [continued]\n')
                            offset = source.tell()
                            consumed += len(line)
                        entry = {'identity': identity, 'offset': offset}
                        if previous != entry:
                            self.state[key] = entry
                            changed = True
                except FileNotFoundError:
                    continue  # File rotation between discovery and open.
            output.flush()
        if changed:
            temporary = self.state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.state))
            os.replace(temporary, self.state_path)
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    lock = (args.directory / 'all-harnesses.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    follower = Follower(args.directory, args.directory / 'all-harnesses.log',
                        args.directory / 'all-harnesses.state.json')
    while True:
        follower.pump()
        time.sleep(0.5)


if __name__ == '__main__':
    main()
