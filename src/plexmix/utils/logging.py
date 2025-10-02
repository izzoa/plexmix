import logging
import logging.handlers
import os
import sys
import warnings
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_format: Optional[str] = None
) -> None:
    # Suppress gRPC and Google library warnings (must be set before import)
    os.environ['GRPC_VERBOSITY'] = 'ERROR'
    os.environ['GLOG_minloglevel'] = '2'
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    os.environ['GRPC_TRACE'] = ''
    os.environ['GRPC_VERBOSITY'] = 'NONE'

    # Suppress absl logging warnings (unless in debug mode)
    if level.upper() != 'DEBUG':
        try:
            import absl.logging
            absl.logging.set_verbosity('error')
            absl.logging.set_stderrthreshold('error')
        except ImportError:
            pass
        warnings.filterwarnings('ignore', category=DeprecationWarning, module='google')
        warnings.filterwarnings('ignore', category=FutureWarning, module='google')
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_formatter = logging.Formatter(log_format)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(log_format)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
