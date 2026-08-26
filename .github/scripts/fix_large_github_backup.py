from pathlib import Path

path = Path("youtube_strategy_engine.py")
text = path.read_text(encoding="utf-8")

old = '''        if record.get("type") not in {None, "file"} or record.get("encoding") != "base64":
            raise AppError("The GitHub cloud-backup path must point to a normal JSON file.")
        try:
            content = "".join(str(record.get("content") or "").split())
            raw = base64.b64decode(content, validate=True)
            library = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise AppError("The GitHub cloud backup is damaged or is not a valid JSON strategy library.") from exc
'''

new = '''        if record.get("type") not in {None, "file"}:
            raise AppError("The GitHub cloud-backup path must point to a normal JSON file.")
        try:
            if record.get("encoding") == "base64":
                content = "".join(str(record.get("content") or "").split())
            else:
                # GitHub's Contents API stops embedding file content once a file
                # grows beyond roughly 1 MB. The path is still a valid file; fetch
                # the same Git blob by SHA so large strategy libraries continue to
                # restore/save normally instead of being mistaken for a bad path.
                record_sha = str(record.get("sha") or "")
                if not re.fullmatch(r"[a-fA-F0-9]{40,64}", record_sha):
                    raise AppError("GitHub did not return a readable version of the cloud backup.")
                blob = self._request(
                    f"{self._repository_url}/git/blobs/{quote(record_sha, safe='')}"
                )
                if blob.get("encoding") != "base64":
                    raise AppError("GitHub returned the cloud backup in an unsupported encoding.")
                content = "".join(str(blob.get("content") or "").split())
            raw = base64.b64decode(content, validate=True)
            library = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise AppError("The GitHub cloud backup is damaged or is not a valid JSON strategy library.") from exc
'''

if old not in text:
    if "GitHub's Contents API stops embedding file content" in text:
        print("Large GitHub backup fallback already installed.")
        raise SystemExit(0)
    raise SystemExit("Expected GitHub cloud-backup read block was not found; refusing to patch.")

path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Installed large-file GitHub backup fallback.")
