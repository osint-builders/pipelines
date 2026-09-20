"""Check that each release job received the gate's exact bundle bytes."""

import hashlib
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    digest = hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()
    if digest != os.environ["EXPECTED_BUNDLE_SHA256"]:
        raise SystemExit("Downloaded bundle checksum mismatch")
