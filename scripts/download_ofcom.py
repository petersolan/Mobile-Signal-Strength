"""Download the Ofcom drive-test zips listed in config.RELEASES (resumable).

The Ofcom server is sometimes slow and drops connections, so each file is fetched with
HTTP Range resume and retried until its size matches Content-Length.
Usage: python scripts/download_ofcom.py [release ...]
"""
import sys
import time
import urllib.request
import zipfile

from config import ARCHIVE, RELEASES

UA = {"User-Agent": "Mobile-Signal-Strength pipeline"}


def remote_size(url):
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA)) as r:
        return int(r.headers["Content-Length"])


def fetch(url, dest, attempts=50):
    want = remote_size(url)
    for i in range(attempts):
        have = dest.stat().st_size if dest.exists() else 0
        if have == want:
            return
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={have}-"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "ab") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except OSError as e:
            print(f"  {dest.name}: {e}; retry {i + 1} at {dest.stat().st_size:,}/{want:,}", flush=True)
            time.sleep(5)
    raise RuntimeError(f"{dest.name}: incomplete after {attempts} attempts")


def main(names):
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for name in names or RELEASES:
        url = RELEASES[name]
        dest = ARCHIVE / url.rsplit("/", 1)[1]
        print(f"{name}: {url}", flush=True)
        fetch(url, dest)
        bad = zipfile.ZipFile(dest).testzip()
        if bad:
            raise RuntimeError(f"{dest.name}: corrupt member {bad}")
        print(f"  ok {dest.stat().st_size / 1e6:,.0f} MB", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
