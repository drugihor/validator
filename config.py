# config.py
import os
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()

# Get API key from environment variables
API_KEY = os.getenv('API_KEY', 'your_strong_api_key_here')

if API_KEY == 'your_strong_api_key_here':
    print("WARNING: Using default API key. Set API_KEY environment variable for security.")
