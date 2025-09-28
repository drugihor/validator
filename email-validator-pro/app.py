# app.py
from flask import Flask, render_template, request, jsonify, send_file
import threading
import time
import os
import csv
from config import API_KEY

from validator import EmailValidator, Proxy, ValidationResult

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['RESULT_FOLDER'] = 'results'
app.config['LOGS_FOLDER'] = 'logs'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['RESULT_FOLDER'], exist_ok=True)
os.makedirs(app.config['LOGS_FOLDER'], exist_ok=True)

# Глобальные переменные
emails = []
proxies = []
results = {"valid": [], "invalid": [], "error": [], "disposable": []}
stats = {
    "total": 0,
    "checked": 0,
    "good": 0,
    "bad": 0,
    "remaining": 0
}
running = False
validator = EmailValidator(timeout=30)

def load_emails(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return [line.strip().split(';', 1) for line in f if ';' in line and '@' in line]

def load_proxies(filepath):
    p_list = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split(':')
            if len(parts) == 2:
                p_list.append(Proxy(parts[0], int(parts[1])))
            elif len(parts) == 4:
                p_list.append(Proxy(parts[0], int(parts[1]), parts[2], parts[3]))
    return p_list

def worker():
    global running, stats
    total = len(emails)
    for i, (email, password) in enumerate(emails):
        if not running:
            break
        proxy = random.choice(proxies) if proxies else None
        result = validator.validate_smtp(email, password, proxy)
        results[result.status].append(result)
        stats['checked'] = i + 1
        stats['good'] = len(results['valid'])
        stats['bad'] = len(results['invalid']) + len(results['error']) + len(results['disposable'])
        stats['remaining'] = total - stats['checked']
        time.sleep(0.1)  # Анти-флуд

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    global emails, proxies, stats
    if 'email_file' in request.files:
        file = request.files['email_file']
        path = os.path.join(app.config['UPLOAD_FOLDER'], 'emails.txt')
        file.save(path)
        emails = load_emails(path)
        stats['total'] = len(emails)
        stats['remaining'] = len(emails)
        return jsonify({'emails': len(emails)})
    if 'proxy_file' in request.files:
        file = request.files['proxy_file']
        path = os.path.join(app.config['UPLOAD_FOLDER'], 'proxies.txt')
        file.save(path)
        proxies = load_proxies(path)
        return jsonify({'proxies': len(proxies)})
    return jsonify({'error': 'No file'}), 400

@app.route('/start', methods=['POST'])
def start():
    global running
    auth = request.headers.get('X-API-Key')
    if auth != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    if not emails:
        return jsonify({'error': 'No emails uploaded'}), 400
    if running:
        return jsonify({'error': 'Already running'}), 400
    running = True
    thread = threading.Thread(target=worker)
    thread.start()
    return jsonify({'status': 'started'})

@app.route('/stop', methods=['POST'])
def stop():
    global running
    auth = request.headers.get('X-API-Key')
    if auth != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    running = False
    return jsonify({'status': 'stopped'})

@app.route('/stats')
def get_stats():
    auth = request.headers.get('X-API-Key')
    if auth != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    return jsonify({
        'stats': stats,
        'running': running,
        'results': {
            'valid': len(results['valid']),
            'invalid': len(results['invalid']),
            'error': len(results['error']),
            'disposable': len(results['disposable'])
        }
    })

@app.route('/download/<kind>')
def download(kind):
    auth = request.headers.get('X-API-Key')
    if auth != API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    if kind not in ['good', 'bad']:
        return "Not found", 404

    filename = f"{kind}.csv"
    path = os.path.join(app.config['RESULT_FOLDER'], filename)

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Email', 'Status', 'Method', 'Details', 'Proxy'])
        if kind == 'good':
            for r in results['valid']:
                writer.writerow([r.email, r.status, r.method, r.details, r.proxy])
        else:
            for r in results['invalid'] + results['error'] + results['disposable']:
                writer.writerow([r.email, r.status, r.method, r.details, r.proxy])

    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
