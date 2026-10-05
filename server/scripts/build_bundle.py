"""Build the runtime download bundle deterministically without local secrets."""
import gzip
import io
from pathlib import Path
import tarfile


def build():
    server = Path(__file__).resolve().parents[1]
    output = server.parent / "assets" / "codex-x-mode-bridge.tar.gz"
    output.parent.mkdir(exist_ok=True)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(server.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
                continue
            if path.suffix in {".pyc", ".sqlite3", ".lock", ".pem", ".key"} or path.name.endswith((".siwc.json", ".sqlite3-wal", ".sqlite3-shm")) or path.name == "bridge-private.json":
                continue
            data = path.read_bytes()
            info = tarfile.TarInfo("codex-x-mode-bridge/" + path.relative_to(server).as_posix())
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    with output.open("wb") as target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
            compressed.write(raw.getvalue())
    print(output)


if __name__ == "__main__":
    build()
