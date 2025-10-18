import asyncio
from functools import wraps

from app.core.logging import logger # Import structured logger

def async_retry(max_attempts: int = 3, delay: float = 1.0, backoff_factor: float = 2.0, exceptions=(Exception,)): # noqa
    """
    A decorator for retrying async functions with exponential backoff.

    Args:
        max_attempts (int): Maximum number of attempts.
        delay (float): Initial delay between retries in seconds.
        backoff_factor (float): Factor by which the delay increases each attempt.
        exceptions (tuple): A tuple of exceptions to catch and retry on.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    logger.warning("Attempt failed for function", function_name=func.__name__, attempt=attempt, error=str(e))
                    if attempt < max_attempts:
                        logger.info("Retrying function", function_name=func.__name__, delay=f"{current_delay:.2f}s")
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logger.error("All attempts failed for function", function_name=func.__name__, max_attempts=max_attempts)
                        raise
        return wrapper
    return decorator
