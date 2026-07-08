import os
import logging
from logging.handlers import RotatingFileHandler
from Titan.config.config import LOGS_DIR

# Ensure log directory exists
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

log_format = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_format)
console_handler.setLevel(logging.INFO)

# Rotating File Handler
log_file = os.path.join(LOGS_DIR, "titan.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(log_format)
file_handler.setLevel(logging.DEBUG)

def get_logger(name: str) -> logging.Logger:
    """Returns a logger instance with console and file handlers configured."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Avoid duplicate handlers
    if not logger.handlers:
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        
    logger.propagate = False
    return logger

# Create pre-configured system loggers
trading_logger = get_logger("Titan.Trading")
system_logger = get_logger("Titan.System")
risk_logger = get_logger("Titan.Risk")
execution_logger = get_logger("Titan.Execution")
learning_logger = get_logger("Titan.Learning")
websocket_logger = get_logger("Titan.WebSocket")
db_logger = get_logger("Titan.DB")
