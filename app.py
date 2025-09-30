from flask import Flask, request, jsonify, render_template, send_file
from validator import EmailValidator, Proxy, ValidationResult # Import ValidationResult
import os
import time # For delays
import random # For proxy selection
from config import API_KEY # Import API_KEY from config.py
from typing import Optional


app = Flask(__name__)
email_validator_instance = EmailValidator(timeout=10)

@app.route("/", methods=["GET"])
def home():
    """Главная страница с веб-интерфейсом."""
    return render_template('index.html')

# Global proxy list - empty by default, can be loaded from environment variables
GLOBAL_PROXIES = []

DEFAULT_METHOD_ORDER = ["smtp", "mx", "imap", "pop3", "http"]
API_DELAY_SECONDS = 0.1 # Delay between checks in mass mode

def check_auth(req):
    key = req.headers.get("X-API-Key")
    return key == API_KEY

def try_methods_sync(email: str, password: str, methods_order: list[str],
                      request_proxy: Optional[Proxy] = None, delay_seconds: float = API_DELAY_SECONDS) -> dict:
    """
    Synchronous email validation: tries validation methods in order, 
    returns the first successful result or the last unsuccessful one.
    """
    last_result_dict = None
    
    # Use proxy only if explicitly provided in request
    proxy_to_use = request_proxy

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
            # Call the synchronous validator method directly
            if method in ["smtp", "imap", "pop3"]:
                result_obj = validator_method(email, password, proxy_to_use)
            else: # For methods like MX, HTTP that don't need password
                result_obj = validator_method(email, proxy_to_use)
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
            time.sleep(delay_seconds)

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
def validate_single_email_api():
    # For web interface, skip auth check. For API calls, check X-API-Key header
    if request.headers.get('User-Agent', '').startswith('Mozilla') or 'text/html' in request.headers.get('Accept', ''):
        # Request from web browser - skip auth
        pass
    elif not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() # Use get_json() for POST body
    email = data.get("email")
    password = data.get("password", "")
    requested_method = data.get("method", "auto") # Default to "auto"
    delay_seconds = float(data.get('delay_seconds', API_DELAY_SECONDS))

    if not email:
        return jsonify({"error": "Missing 'email' in request body"}), 400

    request_proxy = None
    proxy_data = data.get("proxy")
    if proxy_data:
        request_proxy = Proxy(
            proxy_data.get("host"),
            proxy_data.get("port"),
            proxy_data.get("username"),
            proxy_data.get("password"),
            proxy_data.get("scheme", "http")
        )
    
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

    result = try_methods_sync(email, password, methods_order, request_proxy, delay_seconds)
    return jsonify(result)


@app.route("/api/validate-multiple-emails", methods=["POST"])
def validate_multiple_emails_api():
    # For web interface, skip auth check. For API calls, check X-API-Key header
    if request.headers.get('User-Agent', '').startswith('Mozilla') or 'text/html' in request.headers.get('Accept', ''):
        # Request from web browser - skip auth
        pass
    elif not check_auth(request):
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

    # Simple synchronous processing for multiple emails
    def validate_item(item):
        # Handle both string emails (from web interface) and object emails (from API)
        if isinstance(item, str):
            email = item
            password = ""
            proxy_data = None
        else:
            email = item.get("email")
            password = item.get("password", "")
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
            request_proxy = Proxy(
                proxy_data.get("host"),
                proxy_data.get("port"),
                proxy_data.get("username"),
                proxy_data.get("password"),
                proxy_data.get("scheme", "http")
            )

        res = try_methods_sync(email, password, methods_order, request_proxy, delay_seconds)
        return res

    # Process all emails sequentially (for simplicity and Render compatibility)
    results = [validate_item(item) for item in emails_to_validate]
    
    return jsonify({"results": results})


# Web interface routes for file upload and batch processing
@app.route("/upload", methods=["POST"])
def upload_files():
    """Загрузка файлов email:pass и прокси для веб-интерфейса"""
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        if 'email_file' in request.files:
            file = request.files['email_file']
            if file.filename != '':
                content = file.read().decode('utf-8')
                emails = [line.strip() for line in content.split('\n') if line.strip()]
                # Сохраняем в глобальную переменную для обработки
                app.config['uploaded_emails'] = emails
                return jsonify({"emails": len(emails)})
        
        if 'proxy_file' in request.files:
            file = request.files['proxy_file']
            if file.filename != '':
                content = file.read().decode('utf-8')
                proxies = [line.strip() for line in content.split('\n') if line.strip()]
                app.config['uploaded_proxies'] = proxies
                return jsonify({"proxies": len(proxies)})
                
        return jsonify({"error": "No file uploaded"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/start", methods=["POST"])
def start_validation():
    """Запуск пакетной валидации"""
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    
    if 'uploaded_emails' not in app.config:
        return jsonify({"error": "No emails uploaded"}), 400
    
    # Инициализируем статистику
    app.config['stats'] = {
        'total': len(app.config['uploaded_emails']),
        'checked': 0,
        'remaining': len(app.config['uploaded_emails']),
        'good': 0,
        'bad': 0
    }
    
    return jsonify({"status": "started"})

@app.route("/stop", methods=["POST"])
def stop_validation():
    """Остановка пакетной валидации"""
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({"status": "stopped"})

@app.route("/stats", methods=["GET"])
def get_stats():
    """Получение статистики валидации"""
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    
    stats = app.config.get('stats', {
        'total': 0, 'checked': 0, 'remaining': 0, 'good': 0, 'bad': 0
    })
    
    return jsonify({"stats": stats})

@app.route("/download/<file_type>", methods=["GET"])
def download_results(file_type):
    """Скачивание результатов валидации"""
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    
    if file_type not in ['good', 'bad']:
        return jsonify({"error": "Invalid file type"}), 400
    
    try:
        filename = f"results/{file_type}.csv"
        return send_file(filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({"error": f"{file_type}.csv not found"}), 404


if __name__ == "__main__":
    # Ensure dnspython and requests are installed:
    # pip install dnspython requests
    # For production, use a WSGI server like Gunicorn:
    # gunicorn --bind 0.0.0.0:5000 --workers 2 app:app
    # For local development, `flask run` or `python app.py` should work.
    app.run(host="0.0.0.0", port=5000, debug=True)
