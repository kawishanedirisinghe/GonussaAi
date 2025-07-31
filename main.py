from logger import get_logger

def main():
    """
    A simple function to demonstrate the use of the logger.
    """
    # Get the logger instance
    logger = get_logger()

    # Log some messages
    logger.info("This is an info message.")
    logger.warning("This is a warning message.")
    logger.error("This is an error message.")

if __name__ == "__main__":
    main()
