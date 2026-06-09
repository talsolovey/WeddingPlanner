import os
from flask import Flask, request, jsonify, send_from_directory
import json
from datetime import datetime

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route('/')
def serve_index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/signup', methods=['POST'])
def signup():
    email_data = request.get_json()
    email = email_data.get('email', '')
    if not email or '@' not in email:
        return jsonify({'error': 'Invalid email address'}), 400
    entry = {'email': email, 'timestamp': datetime.now().isoformat()}

    try:
        with open(os.path.join(BASE_DIR, 'emails.json'), 'r') as f:
            emails_list = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        emails_list = []

    emails_list.append(entry)
    with open(os.path.join(BASE_DIR, 'emails.json'), 'w') as f:
        json.dump(emails_list, f, indent=2)

    return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)