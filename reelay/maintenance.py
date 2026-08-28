import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from .config import Settings
from .db import PLATFORM_MEDIA_COLUMNS, QueueDB
from .publishers import PublisherRegistry


def audit_queue(probe_media=False):
    settings = Settings()
    db = QueueDB(settings.db_path)
    db.init()
    publishers = PublisherRegistry.from_settings(settings)
    required = [platform.value for platform in publishers]
    jobs = db.all_jobs()
    counts = Counter(job["status"] for job in jobs)
    issues = []

    for job in jobs:
        job_id = job["id"]
        video_path = Path(job["video_path"]) if job.get("video_path") else None
        if job["status"] == "published":
            missing = [
                platform for platform in required if not job.get(PLATFORM_MEDIA_COLUMNS[platform])
            ]
            if missing:
                issues.append(f"#{job_id}: published without {', '.join(missing)}")
            continue

        if not video_path or not video_path.is_file():
            issues.append(f"#{job_id}: {job['status']} without local MP4")
            continue
        if probe_media:
            media_error = _probe_error(video_path)
            if media_error:
                issues.append(f"#{job_id}: {media_error}")

    print("Queue:", ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("Required platforms:", ", ".join(required))
    print("Paused:", "yes" if db.is_paused() else "no")
    if issues:
        print("Issues:")
        for issue in issues:
            print("-", issue)
        return 1
    print("Issues: none")
    return 0


def _probe_error(video_path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height:format=duration",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return (result.stderr.strip() or "ffprobe failed")[-300:]
    try:
        payload = json.loads(result.stdout)
        duration = float(payload.get("format", {}).get("duration", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "ffprobe returned invalid metadata"
    streams = payload.get("streams") or []
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if not video:
        return "video stream is missing"
    if duration <= 0 or duration > 180:
        return f"duration is outside Shorts range: {duration:.1f}s"
    return None


def main():
    parser = argparse.ArgumentParser(description="Reelay maintenance commands")
    parser.add_argument(
        "command",
        choices=("queue-audit",),
        help="maintenance command",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="validate every pending MP4 with ffprobe",
    )
    arguments = parser.parse_args()
    if arguments.command == "queue-audit":
        raise SystemExit(audit_queue(probe_media=arguments.probe))


if __name__ == "__main__":
    main()
