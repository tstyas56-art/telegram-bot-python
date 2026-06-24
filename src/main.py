#!/usr/bin/env python3
"""
Main entry point for WOWDrive Bot with web server
"""

import asyncio
import threading
import logging
from pathlib import Path

from bot import start_bot
from Web_site import start_web_server
from config import *

# Configure logging
logging.basicConfig(
    format=LOG_FORMAT,
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)

def main():
    """Main function to start both bot and web server"""
    logger.info("🚀 Starting WOWDrive Bot with Web Server...")
    
    try:
        # Start web server in a separate thread
        web_thread = threading.Thread(target=start_web_server, daemon=True)
        web_thread.start()
        logger.info("🌐 Web server started on http://localhost:8080")
        
        # Start the bot (blocking)
        logger.info("🤖 Starting Telegram bot...")
        start_bot()
        
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

if __name__ == '__main__':
    main()
