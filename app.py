from flask import Flask, request, jsonify
from validator import EmailValidator, Proxy, ValidationResult # Import ValidationResult
import os
import asyncio # For async endpoints
import random # For proxy selection
from config import API_KEY # Import API_KEY from config.py
from typing import Optional


app = Flask(__name__)
email_validator_instance = EmailValidator(timeout=10) # Renamed for consistency

# Global list of proxies, could be loaded from a file or environment in a real app
# For this example, let's keep it simple or imagine it's loaded at startup
GLOBAL_PROXIES = [
    Proxy("192.168.1.1", 8080),
    Proxy("199.168.1.2", 8081, "user", "pass"),
    # Add more proxies here if needed, or implement a loader function
]

DEFAULT_METHOD_ORDER = ["smtp", "mx", "imap", "pop3", "http"]
API_DELAY_SECONDS = 0.1 # Delay between checks in mass mode

def check_auth(req):
    key = req.headers.get("X-API-Key")
    return key == API_KEY

async def try_methods_async(email: str, password: str, methods_order: list[str],
                            request_proxy: Optional[Proxy] = None, delay_seconds: float = API_DELAY_SECONDS) -> dict:
    """
    Asynchronous wrapper: tries methods in order, returns the first successful result
    or the last unsuccessful one.
    """
    last_result_dict = None
    
    # Prioritize proxy from request, otherwise use a random global proxy
    proxy_to_use = request_proxy
    if not proxy_to_use and GLOBAL_PROXIES:
        proxy_to_use = random.choice(GLOBAL_PROXIES)

    for method in methods_order:
        validator_method = None
        if method == "smtp" and hasattr(email_validator_instance, "validate_smtp"):
            validator_method = email_validator_instance.validate_smtp
        elif method == "mx" and hasattr(email_validator_instance, "validate_mx"):
            validator_method = email_validator_instance.validate_mx
        elif method == "imap" and hasattr(email_validator_instance, "validate_imap"):
            validator_method = email_validator_instance.validate_imap
        elif method == "pop3" and hasattr(email_validator_instance, "validate_pop3"):
            validator_method = email_validator_instance.validate_pop3
        elif method == "http" and hasattr(email_validator_instance, "validate_http"):
            validator_method = email_validator_instance.validate_http

        if validator_method is None:
            # Method not supported by the validator instance, skip
            continue 

        try:
            # Run the synchronous validator method in a thread pool
            if method in ["smtp", "imap", "pop3"]:
                result_obj = await asyncio.to_thread(validator_method, email, password, proxy_to_use)
            else: # For methods like MX, HTTP that don't need password
                result_obj = await asyncio.to_thread(validator_method, email, proxy_to_use)
        except Exception as e:
            result_obj = ValidationResult(email, "error", method, f"exception: {repr(e)}", proxy_to_use)

        # Normalize the result to a dict for API response
        # Using vars(result_obj) might include internal attributes, better to build explicitly
        last_result_dict = {
            "email": result_obj.email,
            "is_valid": True if result_obj.status == "valid" else False,
            "status": result_obj.status,
            "method_used": result_obj.method,
            "details": result_obj.details,
            "proxy_used": result_obj.proxy # This will be the __str__ representation of the Proxy object
        }

        if result_obj.status == "valid":
            return last_result_dict
        
        # If not valid, and more methods to try, add a small delay
        if method != methods_order[-1]:
            await asyncio.sleep(delay_seconds)

    if last_result_dict:
        return last_result_dict
    else:
        # If no methods were supported or tried
        return {
            "email": email,
            "is_valid": False,
            "status": "no_methods_supported",
            "method_used": "none",
            "details": "Validator does not support any of the requested methods",
            "proxy_used": str(proxy_to_use) if proxy_to_use else None
        }


@app.route("/api/validate-single-email", methods=["POST"])
async def validate_single_email_api(): # Made async
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() # Use get_json() for POST body
    email = data.get("email")
    password = data.get("password")
    requested_method = data.get("method", "auto") # Default to "auto"
    delay_seconds = float(data.get('delay_seconds', API_DELAY_SECONDS))

    if not email:
        return jsonify({"error": "Missing 'email' in request body"}), 400

    request_proxy = None
    proxy_data = data.get("proxy")
    if proxy_data:
        request_proxy = Proxy(proxy_data.get("host"), proxy_data.get("port"),
                              proxy_data.get("username"), proxy_data.get("password"))
    
    # Determine the order of methods
    methods_order = []
    if requested_method == "auto":
        methods_order = DEFAULT_METHOD_ORDER.copy()
    elif isinstance(requested_method, str):
        methods_order = [requested_method]
    elif isinstance(requested_method, list):
        methods_order = requested_method
    else:
        return jsonify({"error": "Invalid 'method' parameter. Must be 'auto', a string, or a list of strings."}), 400

    result = await try_methods_async(email, password, methods_order, request_proxy, delay_seconds)
    return jsonify(result)


@app.route("/api/validate-multiple-emails", methods=["POST"])
async def validate_multiple_emails_api(): # Made async
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    emails_to_validate = data.get("emails", [])
    if not isinstance(emails_to_validate, list) or not emails_to_validate:
        return jsonify({"error": "Missing 'emails' list in request body or list is empty"}), 400

    requested_method = data.get("method", "auto")
    delay_seconds = float(data.get('delay_seconds', API_DELAY_SECONDS))
    concurrency = int(data.get('concurrency', 10))
    if concurrency < 1:
        concurrency = 1

    # Determine the order of methods
    methods_order = []
    if requested_method == "auto":
        methods_order = DEFAULT_METHOD_ORDER.copy()
    elif isinstance(requested_method, str):
        methods_order = [requested_method]
    elif isinstance(requested_method, list):
        methods_order = requested_method
    else:
        return jsonify({"error": "Invalid 'method' parameter. Must be 'auto', a string, or a list of strings."}), 400

    semaphore = asyncio.Semaphore(concurrency)

    async def validate_item_with_concurrency(item):
        email = item.get("email")
        password = item.get("password")
        proxy_data = item.get("proxy")

        if not email:
            return {
                "email": None,
                "is_valid": False,
                "status": "error",
                "method_used": "none",
                "details": "Email missing in item",
                "proxy_used": None
            }

        request_proxy = None
        if proxy_data:
            request_proxy = Proxy(proxy_data.get("host"), proxy_data.get("port"),
                                  proxy_data.get("username"), proxy_data.get("password"))

        async with semaphore:
            res = await try_methods_async(email, password, methods_order, request_proxy, delay_seconds)
            # Apply additional delay *after* the entire item is processed, if configured
            return res

    tasks = [validate_item_with_concurrency(item) for item in emails_to_validate]
    results = await asyncio.gather(*tasks)
    
    return jsonify(results)


if __name__ == "__main__":
    # Ensure dnspython and requests are installed:
    # pip install dnspython requests
    # For production, use an ASGI server like Uvicorn for async endpoints:
    # uvicorn app:app --host 0.0.0.0 --port 5000 --reload
    # For local development, `flask run` (newer versions) or `python app.py` should work,
    # but the async benefits will be limited without a proper ASGI server.
    app.run(host="0.0.0.0", port=5000, debug=True)