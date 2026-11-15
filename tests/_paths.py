"""Put scraper/ and tools/ on the path the way the workflows do (they run from those folders)."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scraper", "tools"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)
