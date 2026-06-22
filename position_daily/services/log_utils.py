import logging
import time
from functools import wraps

logger = logging.getLogger("position_daily")


def log_step(name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            logger.info("START %s", name)
            try:
                result = func(*args, **kwargs)
                logger.info("OK %s (%.2fs)", name, time.perf_counter() - t0)
                return result
            except Exception:
                logger.exception("FAIL %s (%.2fs)", name, time.perf_counter() - t0)
                raise

        return wrapper

    return decorator
