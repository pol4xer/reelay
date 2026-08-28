import logging
import sys

from telegram import Update

from .app import build_application
from .config import Settings
from .runtime import single_process


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings()
    try:
        with single_process(settings.data_dir / "reelay.lock"):
            application, post_times = build_application(settings)

            print("Reelay started: " + ", ".join(post_times) + f" ({settings.timezone})")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
    except RuntimeError as error:
        if str(error) != "Another Reelay process is already running":
            raise
        raise SystemExit(f"Reelay not started: {error}") from None


if __name__ == "__main__":
    main()
