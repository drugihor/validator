# app.py (ИСПРАВЛЕННАЯ ВЕРСИЯ - БЕЗ ДУБЛИКАТОВ)
import os
import time
import random
from typing import Optional
from flask import Flask, request, jsonify, render_template, send_file
import requests
import logging
from datetime import datetime

# Try to import validator classes
from validator import EmailValidator, Proxy, ValidationResult

# Try to get API_KEY from env, fallback to config.py if present
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    try:
        from config import API_KEY as CONFIG_API_KEY
        API_KEY = CONFIG_API_KEY
    except Exception:
        API_KEY = None  # If still None, check_auth will fail

# Allow UI to bypass API key when ALLOW_UI_NO_AUTH is truthy (env var).
ALLOW_UI_NO_AUTH = os.getenv("ALLOW_UI_NO_AUTH", "true").lower() in ("1", "true", "yes")

# Logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Ensure results dir exists
os.makedirs("results", exist_ok=True)
GOOD_CSV = os.path.join("results", "good.csv")
BAD_CSV = os.path.join("results", "bad.csv")

# Initialize CSV files with headers if not present
def init_csv(path, header="email,method,details,timestamp,proxy\n"):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)

init_csv(GOOD_CSV)
init_csv(BAD_CSV)

email_validator_instance = EmailValidator(timeout=10)

# Global proxy list - empty by default, can be loaded from environment variables
GLOBAL_PROXIES = []

DEFAULT_METHOD_ORDER = ["smtp", "mx", "imap", "pop3", "http"]
API_DELAY_SECONDS = 0.1  # Delay between checks in mass mode

def check_auth(req):
    """
    Return True if request is authorized.
    Behavior:
      - If ALLOW_UI_NO_AUTH is True and request looks like a browser (UA starts with Mozilla or Accept contains text/html),
        allow it (useful for UI testing; can be disabled via ALLOW_UI_NO_AUTH=false).
      - Else require X-API-Key header matching API_KEY.
    """
    # Allow UI no-auth if configured
    user_agent = req.headers.get("User-Agent", "")
    accept = req.headers.get("Accept", "")
    if ALLOW_UI_NO_AUTH and (user_agent.startswith("Mozilla") or "text/html" in accept):
        return True

    key = req.headers.get("X-API-Key")
    # If API_KEY not configured, deny by default (safer)
    if not API_KEY:
        logger.warning("API_KEY not configured on server; rejecting API request.")
        return False

    return key == API_KEY

def _append_result_csv(is_valid: bool, email: str, method: str, details: str, proxy_used: Optional[str]):
    path = GOOD_CSV if is_valid else BAD_CSV
    timestamp = datetime.utcnow().isoformat() + "Z"
    line = f'"{email}","{method}","{details}","{timestamp}","{proxy_used or ""}"\n'
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.exception("Failed to write result to CSV: %s", e)

def _update_stats_after_check(result_dict):
    """
    Update app.config['stats'] if present and initialized.
    Expected result_dict fields: email, is_valid (bool)
    """
    stats = app.config.get('stats')
    if not stats:
        return
    try:
        stats['checked'] = stats.get('checked', 0) + 1
        stats['remaining'] = max(0, stats.get('remaining', 0) - 1)
        if result_dict.get('is_valid'):
            stats['good'] = stats.get('good', 0) + 1
        else:
            stats['bad'] = stats.get('bad', 0) + 1
        # save back
        app.config['stats'] = stats
    except Exception:
        logger.exception("Failed to update stats")

def try_methods_sync(email: str, password: str, methods_order: list[str],
                      request_proxy: Optional[Proxy] = None, delay_seconds: float = API_DELAY_SECONDS) -> dict:
    """
    Synchronous email validation: tries validation methods in order,
    returns the first successful result or the last unsuccessful one.
    Also writes result to CSV and updates stats if stats exist.
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
            else:  # For methods like MX, HTTP that don't need password
                result_obj = validator_method(email, proxy_to_use)
        except Exception as e:
            logger.exception("Validator method %s raised exception for %s", method, email)
            result_obj = ValidationResult(email, "error", method, f"exception: {repr(e)}", proxy_to_use)

        # Normalize the result to a dict for API response
        last_result_dict = {
            "email": getattr(result_obj, "email", email),
            "is_valid": True if getattr(result_obj, "status", "") == "valid" else False,
            "status": getattr(result_obj, "status", "error"),
            "method_used": getattr(result_obj, "method", method),
            "details": getattr(result_obj, "details", ""),
            "proxy_used": str(getattr(result_obj, "proxy", proxy_to_use) or proxy_to_use)
        }

        # Persist result and update stats ASAP
        try:
            _append_result_csv(last_result_dict['is_valid'],
                               last_result_dict['email'],
                               last_result_dict['method_used'],
                               last_result_dict['details'].replace('"', "'"),
                               last_result_dict['proxy_used'])
            _update_stats_after_check(last_result_dict)
        except Exception:
            logger.exception("Error persisting result for %s", email)

        if result_obj.status == "valid":
            return last_result_dict

        # If not valid, and more methods to try, add a small delay
        if method != methods_order[-1]:
            time.sleep(delay_seconds)

    if last_result_dict:
        return last_result_dict
    else:
        # If no methods were supported or tried
        fallback = {
            "email": email,
            "is_valid": False,
            "status": "no_methods_supported",
            "method_used": "none",
            "details": "Validator does not support any of the requested methods",
            "proxy_used": str(proxy_to_use) if proxy_to_use else None
        }
        # write fallback result and update stats
        _append_result_csv(False, email, "none", fallback['details'], fallback['proxy_used'])
        _update_stats_after_check(fallback)
        return fallback


@app.route("/", methods=["GET"])
def home():
    """Главная страница с веб-интерфейсом."""
    return render_template('index.html')


@app.route("/api/validate-single-email", methods=["POST"])
def validate_single_email_api():
    # For web interface, skip auth check if ALLOW_UI_NO_AUTH enabled
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password", "")
    requested_method = data.get("method", "auto")  # Default to "auto"
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
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
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

    # Initialize stats for batch if not present
    app.config['stats'] = {
        'total': len(emails_to_validate),
        'checked': 0,
        'remaining': len(emails_to_validate),
        'good': 0,
        'bad': 0
    }

    # Simple synchronous processing for multiple emails (keeps compatibility with Render)
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
            # Increment checked/remaining accordingly
            missing_res = {
                "email": None,
                "is_valid": False,
                "status": "error",
                "method_used": "none",
                "details": "Email missing in item",
                "proxy_used": None
            }
            _append_result_csv(False, "None", "none", "Email missing in item", None)
            _update_stats_after_check(missing_res)
            return missing_res

        request_proxy = None
        if proxy_data:
            request_proxy = Proxy(
                proxy_data.get("host"),
                int(proxy_data.get("port")),
                proxy_data.get("username"),
                proxy_data.get("password"),
                proxy_data.get("scheme", "http")
            )

        res = try_methods_sync(email, password, methods_order, request_proxy, delay_seconds)
        return res

    results = [validate_item(item) for item in emails_to_validate]

    return jsonify({"results": results})


# File upload, stats, start/stop endpoints (kept mostly as-is but now rely on check_auth)
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
        logger.exception("Upload failed")
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


@app.route("/api/check-proxies", methods=["POST"])
def check_proxies_api():
    """Проверка списка прокси (живые/мертвые). Возвращает live/dead.
    Формат входа proxies: [{host, port, username?, password?, scheme?}]
    """
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    proxies_in = data.get("proxies", [])
    timeout = float(data.get("timeout", 5.0))
    test_url = data.get("test_url", "https://httpbin.org/ip")

    if not isinstance(proxies_in, list) or not proxies_in:
        return jsonify({"error": "Missing 'proxies' list"}), 400

    live = []
    dead = []

    for p in proxies_in:
        # Поддерживаем как строки, так и объекты
        if isinstance(p, str):
            raw = p.strip()
            scheme = "http"
            if "://" in raw:
                scheme, rest = raw.split("://", 1)
            else:
                rest = raw
            username = password = None
            if "@" in rest:
                auth, addr = rest.split("@", 1)
                if ":" in auth:
                    username, password = auth.split(":", 1)
                else:
                    username = auth
                host_port = addr
            else:
                host_port = rest
            host, port_str = host_port.split(":", 1)
            port = int(port_str)
            proxy_obj = Proxy(host, port, username, password, scheme)
            proxy_dict = {"host": host, "port": port, "username": username, "password": password, "scheme": scheme}
        else:
            proxy_obj = Proxy(
                p.get("host"),
                int(p.get("port")),
                p.get("username"),
                p.get("password"),
                p.get("scheme", "http")
            )
            proxy_dict = {
                "host": proxy_obj.host,
                "port": proxy_obj.port,
                "username": proxy_obj.username,
                "password": proxy_obj.password,
                "scheme": proxy_obj.scheme
            }

        proxies_config = {"http": str(proxy_obj), "https": str(proxy_obj)}
        try:
            resp = requests.get(test_url, proxies=proxies_config, timeout=timeout)
            if resp.status_code in (200, 204, 301, 302, 403):
                live.append(proxy_dict)
            else:
                dead.append({**proxy_dict, "reason": f"status {resp.status_code}"})
        except Exception as e:
            dead.append({**proxy_dict, "reason": str(e)})

    return jsonify({"live": live, "dead": dead, "total": len(proxies_in)})


if __name__ == "__main__":
    # Local dev run (Render should run via gunicorn with $PORT)
    port = int(os.getenv("PORT", "5000"))
    # Ensure results dir exists on startup (already done above, but safe)
    os.makedirs("results", exist_ok=True)
    app.run(host="0.0.0.0", port=port, debug=True)
