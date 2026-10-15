"""Package source, reports and evidence while excluding local credentials/state."""
import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from dotenv import dotenv_values


def main():
    archive = Path("submission/K4-DAY27-MaiQuangDung-2A202602966.zip")
    manifest_path = Path("submission/manifest.json")
    names = subprocess.check_output(["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"]).decode().split("\0")
    paths = sorted({Path(name) for name in names if name and Path(name) not in {archive, manifest_path}}, key=str)
    secrets = [v.encode() for k, v in dotenv_values(".env").items()
               if k in {"OPENROUTER_API_KEY", "GITHUB_TOKEN"} and v]
    files = []
    for path in paths:
        assert not set(path.parts) & {".git", "env", ".venv", ".uv-cache", "scratch", "__pycache__"}, path
        assert path.name != ".env" and path.suffix not in {".db", ".pyc"}, path
        data = path.read_bytes()
        assert not any(secret in data for secret in secrets), f"Credential detected in {path}"
        files.append({"path":path.as_posix(), "bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    manifest = {"student":"Mai Quang Dũng", "student_id":"2A202602966",
                "assignment":"Day27 Track03 HITL PR Review Agent", "files":files}
    manifest_data = json.dumps(manifest,indent=2,ensure_ascii=False).encode("utf-8")
    manifest_path.write_bytes(manifest_data)
    with ZipFile(archive,"w",compression=ZIP_DEFLATED,compresslevel=9) as output:
        for item in files:
            output.write(item["path"],item["path"])
        output.writestr(manifest_path.as_posix(),manifest_data)
    with ZipFile(archive) as output:
        assert output.testzip() is None
        for item in files:
            assert hashlib.sha256(output.read(item["path"])).hexdigest()==item["sha256"]
    print(f"Submission ZIP verified: {archive}, {len(files)} files plus manifest, {archive.stat().st_size} bytes")


if __name__ == "__main__":
    main()
