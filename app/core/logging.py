import logging
import structlog
import sys

def configure_logging():
    shared_processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure standard logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG,
    )

    # Optionally, configure specific loggers
    # logging.getLogger("uvicorn").handlers = [logging.StreamHandler(sys.stdout)]
    # logging.getLogger("uvicorn.access").handlers = [logging.StreamHandler(sys.stdout)]

    # Create a default logger for the application
    return structlog.get_logger("app")

logger = configure_logging()
