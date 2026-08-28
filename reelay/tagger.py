import asyncio
import json
import re
import shutil
from pathlib import Path


HASHTAG = re.compile(r"(?<!\w)#[\w]+", re.UNICODE)
WORDS = re.compile(r"[^\W_]+", re.UNICODE)
GENERIC_LABELS = {
    "adult",
    "child",
    "face",
    "human",
    "image",
    "indoor",
    "outdoor",
    "people",
    "person",
    "portrait",
}
STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "that",
    "the",
    "this",
    "with",
    "your",
}


class AutoTagger:
    def __init__(self, settings):
        self.settings = settings
        self.max_tags = settings.auto_tag_count
        self.enabled = settings.auto_tags
        self.swift_source = Path(__file__).with_name("vision_tags.swift")
        self.binary = settings.data_dir / "bin" / "reelay-vision"
        self._compile_lock = asyncio.Lock()

    async def generate(self, video_path):
        if not self.enabled:
            return ""

        video_path = Path(video_path)
        tags = self._source_hashtags(video_path.parent)

        if len(tags) < self.max_tags:
            try:
                evidence = await self._vision_evidence(video_path)
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
                evidence = {"labels": [], "texts": []}
            tags.extend(self._evidence_tags(evidence, tags))

        tags = tags[: max(self.max_tags - 1, 0)]
        self._append_unique(tags, "#reels")
        return " ".join(tags[: self.max_tags])

    def _source_hashtags(self, job_dir):
        info_files = list(job_dir.glob("*.info.json"))
        if not info_files:
            return []
        try:
            metadata = json.loads(info_files[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

        tags = []
        for hashtag in HASHTAG.findall(metadata.get("description") or ""):
            self._append_unique(tags, hashtag)
            if len(tags) >= self.max_tags:
                break
        return tags

    async def _vision_evidence(self, video_path):
        frames = await self._extract_frames(video_path)
        try:
            await self._ensure_binary()
            output = await self._run([str(self.binary), *map(str, frames)])
            return json.loads(output)
        finally:
            shutil.rmtree(video_path.parent / ".tag_frames", ignore_errors=True)

    async def _extract_frames(self, video_path):
        output = await self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
        )
        duration = float(output.strip())
        if duration <= 0:
            raise RuntimeError("Video duration is unavailable")

        frame_dir = video_path.parent / ".tag_frames"
        shutil.rmtree(frame_dir, ignore_errors=True)
        frame_dir.mkdir(parents=True)

        frames = []
        for index, fraction in enumerate((0.2, 0.5, 0.8), start=1):
            timestamp = min(max(duration * fraction, 0), max(duration - 0.05, 0))
            frame = frame_dir / f"frame-{index}.jpg"
            await self._run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=720:-2",
                    "-q:v",
                    "3",
                    str(frame),
                ]
            )
            if frame.is_file():
                frames.append(frame)

        if not frames:
            raise RuntimeError("Could not extract frames for tags")
        return frames

    async def _ensure_binary(self):
        async with self._compile_lock:
            if (
                self.binary.is_file()
                and self.binary.stat().st_mtime >= self.swift_source.stat().st_mtime
            ):
                return
            self.binary.parent.mkdir(parents=True, exist_ok=True)
            await self._run(
                [
                    "xcrun",
                    "swiftc",
                    "-O",
                    str(self.swift_source),
                    "-framework",
                    "Vision",
                    "-framework",
                    "AppKit",
                    "-o",
                    str(self.binary),
                ]
            )

    def _evidence_tags(self, evidence, existing):
        tags = []
        texts = [text.strip() for text in evidence.get("texts", []) if text.strip()]
        labels = evidence.get("labels", [])

        for text in texts:
            words = WORDS.findall(text.casefold())
            if len(words) > 4:
                words = [word for word in words if word not in STOP_WORDS][:4]
            slug = "".join(words)[:30]
            if slug:
                self._append_unique(tags, f"#{slug}", existing)

        label_names = set()
        for item in labels:
            if float(item.get("confidence") or 0) < 0.25:
                continue
            label = str(item.get("label") or "").casefold()
            label_names.add(label)
            if label in GENERIC_LABELS:
                continue
            words = [word for word in WORDS.findall(label) if word not in STOP_WORDS]
            slug = "".join(words)[:30]
            if slug:
                self._append_unique(tags, f"#{slug}", existing)

        if texts and label_names.intersection({"adult", "child", "people", "person"}):
            self._append_unique(tags, "#meme", existing)
        return tags

    @staticmethod
    def _append_unique(tags, tag, also=None):
        normalized = tag.casefold()
        seen = {value.casefold() for value in tags}
        if also:
            seen.update(value.casefold() for value in also)
        if normalized not in seen:
            tags.append(tag)

    async def _run(self, command):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            message = stderr.decode(errors="replace").strip()
            raise RuntimeError(message or f"Command failed: {process.returncode}")
        return stdout.decode(errors="replace")
