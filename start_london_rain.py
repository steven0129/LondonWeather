import faulthandler
import os
import runpy
import sys
import traceback
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
TARGET = APP_DIR / "london_rain_floating_v2.py"
LOG = APP_DIR / "startup.log"
FAULT_LOG = APP_DIR / "fault.log"


def log(message: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


if __name__ == "__main__":
    os.chdir(APP_DIR)
    sys.argv = [str(TARGET)]
    log(f"launcher starting: {TARGET}")
    with FAULT_LOG.open("a", encoding="utf-8") as fault_file:
        fault_file.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] faulthandler enabled\n")
        fault_file.flush()
        faulthandler.enable(file=fault_file, all_threads=True)
        try:
            runpy.run_path(str(TARGET), run_name="__main__")
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
            log(f"launcher exited via SystemExit: {exc.code!r}")
            if code:
                raise
        except Exception:
            log("launcher crashed:\n" + traceback.format_exc())
            raise
        finally:
            log("launcher finished")
