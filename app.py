import os
import re
import secrets
import string # Keep for potential future password generation needs
import random # Keep for potential future password generation needs
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from pdfminer.high_level import extract_text as pdf_extract_text
from docx import Document as DocxDocument
import requests

# --- App Setup ---
# Ensure temp directory exists locally
TEMP_FOLDER = os.path.join(os.path.dirname(__file__), 'temp')
os.makedirs(TEMP_FOLDER, exist_ok=True)

app = Flask(__name__)
# Set a secure secret key for session management
# Use environment variable in production: os.environ.get('SECRET_KEY')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# --- Configuration ---
# OCR Space API Configuration
OCR_SPACE_API_URL = "https://api.ocr.space/parse/image"
# Use environment variable in production: os.environ.get('OCR_SPACE_API_KEY')
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY', "K87955728688957") # Replace placeholder if needed for local test
if OCR_SPACE_API_KEY == "K87955728688957":
    print("Warning: Using default/placeholder OCR Space API key. Set OCR_SPACE_API_KEY environment variable.")


# --- Data Storage (Replace with a database in production!) ---

# Store users with hashed passwords and roles
USERS_DB = {
    # Email: {username, hashed_password, role}
    "admin@example.com": {
        "username": "admin_user",
        "hashed_password": generate_password_hash("admin@a123"),
        "role": "admin"
    },
    "po@example.com": {
        "username": "po_verifier_1",
        "hashed_password": generate_password_hash("po@123"),
        "role": "po_verifier"
    },
    "ats@example.com": {
        "username": "ats_verifier_1",
        "hashed_password": generate_password_hash("ats@123"),
        "role": "ats_verifier"
    },
    "subadmin@example.com": {
        "username": "sub_admin_1",
        "hashed_password": generate_password_hash("sub@123"),
        "role": "subadmin"
    }
}

# Unified Criteria Storage for PO and ATS
# Structure: { criteria_id: {id, name, field, rule, confidence, type ('po' or 'ats')} }
CRITERIA_DB = {
    "crit1": {"id": "crit1", "name": "Vendor Name Check", "field": "Vendor Name", "rule": "Must exist in approved vendor list.", "confidence": 85, "type": "po"},
    "crit2": {"id": "crit2", "name": "PO Number Format", "field": "PO Number", "rule": "Must match pattern 'PO-######'.", "confidence": 95, "type": "po"},
    "crit3": {"id": "crit3", "name": "CV Keyword Match", "field": "Skills Section", "rule": "Must contain at least 3 keywords from job description.", "confidence": 75, "type": "ats"},
}
criteria_id_counter = len(CRITERIA_DB) + 1

# Dummy database for reference comparison (if needed by get_database_data)
dummy_database = {
    "S001": { "Sr no.": "S001", "Name": "Hemanshu Kasar", "City": "Nagpur", "Age": "23", "Country": "India", "Address": "7, gurudeo nagar" },
    "S002": { "Sr no.": "S002", "Name": "John Doe", "City": "New York", "Age": "30", "Country": "USA", "Address": "123 Main St" },
    # ... other dummy data ...
}

# --- Helper Functions ---
# (Keep generate_secure_password if you might add password reset or generation later)
# def generate_secure_password(length=12): ...

# --- Authentication & Authorization Decorators ---

def login_required(f):
    """Redirects to standard login if user not logged in or is admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session.get('logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login_page'))
        # Prevent admins from accessing user dashboard directly via URL
        if session.get('role') == 'admin':
             flash('Admins should use the admin console.', 'info')
             return redirect(url_for('admin_dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Redirects to admin login if user not logged in or not an admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session.get('logged_in'):
            flash('Please log in to access the admin area.', 'warning')
            return redirect(url_for('admin_login_page')) # Redirect to admin login
        if session.get('role') != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('login_page')) # Redirect non-admins away
        return f(*args, **kwargs)
    return decorated_function

# --- OCR / File Processing Functions ---

def ocr_image_via_api(image_path):
    """Performs OCR on an image using OCR Space API."""
    if OCR_SPACE_API_KEY == "K87955728688957": # Check against placeholder
         print("Warning: Attempting OCR with placeholder API key.")
         # return "Error: OCR Space API Key not configured or using placeholder." # Option to block
    try:
        with open(image_path, 'rb') as f: image_data = f.read()
        payload = {'apikey': OCR_SPACE_API_KEY, 'language': 'eng', 'isOverlayRequired': False}
        files = {'file': (os.path.basename(image_path), image_data)} # Use 'file' parameter name
        response = requests.post(OCR_SPACE_API_URL, files=files, data=payload)
        response.raise_for_status()
        result = response.json()

        if result and not result.get('IsErroredOnProcessing'):
            parsed_results = result.get('ParsedResults')
            if parsed_results and len(parsed_results) > 0:
                text = parsed_results[0].get('ParsedText', "No text found in image.")
                return text.strip()
            else:
                 return "No parsed results found in OCR response."
        elif result.get('IsErroredOnProcessing'):
            error_message = result.get('ErrorMessage', ["Unknown OCR Error"])[0]
            details = result.get('ErrorDetails', "")
            return f"OCR Space API Error: {error_message} - {details}"
        else:
            return "Unknown error from OCR Space API. Response: " + str(result)

    except requests.exceptions.RequestException as e: return f"Error connecting to OCR Space API: {e}"
    except Exception as e: return f"Error during OCR processing: {e}"

def extract_text_from_pdf(file_path):
    """Extracts text directly from a PDF file using pdfminer.six."""
    try:
        text = pdf_extract_text(file_path)
        return text.strip() if text else "No text extracted from PDF."
    except Exception as e:
        return f"Error extracting text from PDF: {e}"

def extract_text_from_docx(file_path):
    """Extracts text from a DOCX file using python-docx."""
    try:
        doc = DocxDocument(file_path)
        full_text = [p.text for p in doc.paragraphs if p.text]
        return '\n'.join(full_text).strip() if full_text else "No text extracted from DOCX."
    except Exception as e:
        return f"Error extracting text from DOCX: {e}"

def extract_text_from_file(file_path, filename):
    """Detects file type and calls appropriate extraction method."""
    file_extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if file_extension in ['png', 'jpg', 'jpeg', 'bmp', 'gif', 'tiff']:
        return ocr_image_via_api(file_path)
    elif file_extension == 'pdf':
        return extract_text_from_pdf(file_path)
    elif file_extension == 'docx':
        return extract_text_from_docx(file_path)
    else:
        return f"Error: Unsupported file format '{file_extension}'."

def extract_structured_data(text, fields_to_extract):
    """Extracts specific structured data fields from text using regex (case-insensitive)."""
    if not text or not fields_to_extract:
        return {}
    data = {field: None for field in fields_to_extract}
    lines = text.strip().split('\n')
    for field in fields_to_extract:
        # Pattern: Start of line, optional whitespace, field name (escaped), optional whitespace, colon, optional whitespace, captured value
        pattern = re.compile(r"^\s*" + re.escape(field) + r"\s*:\s*(.*)", re.IGNORECASE)
        for line in lines:
            match = pattern.match(line.strip())
            if match:
                value = match.group(1).strip()
                # Prevent overwriting if field name appears multiple times (take first match)
                if data[field] is None:
                    data[field] = value if value else None # Store None if value is empty string after strip
                # break # Uncomment if you only want the absolute first match per field name
    # Return only fields that were found (value is not None)
    return {k: v for k, v in data.items() if v is not None}


def get_database_data(identifier):
    """Fetches data from the dummy database based on a generic identifier (e.g., Sr no.)."""
    # Adapt this if your comparison key changes (e.g., PO Number)
    return dummy_database.get(identifier, None)

def compare_data(extracted_data, db_data):
    """Compares extracted data with database data (basic string comparison)."""
    if not db_data or not extracted_data:
        return 0, {}, "Missing data for comparison."

    matched_fields = 0
    mismatched_fields = {}
    # Compare only keys present in the db_data template
    total_comparable_fields = len(db_data)

    for key, db_value in db_data.items():
        extracted_value = extracted_data.get(key)

        # Handle None values gracefully during comparison
        db_str = str(db_value).strip() if db_value is not None else ""
        ext_str = str(extracted_value).strip() if extracted_value is not None else ""

        if ext_str.lower() == db_str.lower():
            matched_fields += 1
        else:
             # Record mismatch only if extracted value was found but different,
             # or if DB had a value and extracted was missing/None
             if extracted_value is not None or db_value is not None:
                 mismatched_fields[key] = {"db_value": db_value, "extracted_value": extracted_value}

    accuracy = (matched_fields / total_comparable_fields) * 100 if total_comparable_fields > 0 else 0
    return accuracy, mismatched_fields, None # Comparison itself didn't error


def validate_with_criteria(structured_data, criteria_db):
    """Placeholder: Validates extracted data against defined criteria."""
    validation_errors = {}
    validation_passed = True
    print(f"DEBUG: Validating data {structured_data} against {len(criteria_db)} criteria.") # Placeholder
    # --- TODO: Implement actual validation logic ---
    # Iterate through criteria_db. For each rule:
    # 1. Get the field name (rule['field'])
    # 2. Get the extracted value (structured_data.get(field_name))
    # 3. Apply validation based on rule['type'] ('regex', 'min_length', etc.) using rule['rule'] description
    # 4. Check confidence if applicable (rule['confidence']) - This might relate to OCR confidence, not rule confidence.
    # 5. If validation fails, add to validation_errors dictionary and set validation_passed = False
    # Example (Regex check):
    # if rule['type'] == 'regex' and value:
    #    if not re.match(rule['rule'], value): # Assuming rule['rule'] holds the pattern
    #       validation_errors[rule['field']] = f"Does not match required pattern described in rule: {rule['name']}"
    #       validation_passed = False
    # --------------------------------------------
    return validation_passed, validation_errors


# --- Context Processors (if needed) ---
# @app.context_processor ... (keep if get_database_data or others are used directly in templates)


# --- Routes ---

@app.route('/', methods=['GET'])
def landing_page():
    """Renders the main landing page."""
    if 'logged_in' in session and session['logged_in']:
        role = session.get('role')
        if role == 'admin': return redirect(url_for('admin_dashboard'))
        else: return redirect(url_for('app_dashboard'))
    return render_template('Template1.html')

# --- Standard User Login ---
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """Handles login for non-admin users."""
    if 'logged_in' in session and session['logged_in']:
        role = session.get('role')
        if role == 'admin': return redirect(url_for('admin_dashboard'))
        else: return redirect(url_for('app_dashboard'))

    if request.method == 'POST':
        email = request.form.get('email') # Use 'email' from form name
        password = request.form.get('password')
        user_data = USERS_DB.get(email)

        # Check if user exists, password is correct, AND role is NOT admin
        if user_data and user_data['role'] != 'admin' and check_password_hash(user_data['hashed_password'], password):
            session['logged_in'] = True
            session['user_email'] = email
            session['role'] = user_data['role']
            flash(f'Login successful! Welcome {user_data.get("username", email)}.', 'success')
            return redirect(url_for('app_dashboard'))
        else:
            flash('Invalid credentials or not authorized for user login.', 'danger')
            # Stay on the same login page

    return render_template('login.html')

# --- Admin Login ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_login_page():
    """Handles login specifically for Admin users."""
    if 'logged_in' in session and session['logged_in'] and session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard')) # Redirect logged-in admin

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user_data = USERS_DB.get(email)

        # Check if user exists, password is correct, AND role IS admin
        if user_data and user_data['role'] == 'admin' and check_password_hash(user_data['hashed_password'], password):
            session['logged_in'] = True
            session['user_email'] = email
            session['role'] = 'admin'
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials.', 'danger')
            # Stay on the admin login page

    return render_template('admin_login.html')

# --- User Dashboard ---
@app.route('/app', methods=['GET', 'POST'])
@login_required # Decorator handles unauthorized access and admin redirection
def app_dashboard():
    """User dashboard for PO/ATS Verifiers and Sub-Admins."""
    results = {}
    user_role = session.get('role')

    # Determine default active tab based on role
    default_tab = 'po-verification' # Fallback
    if user_role == 'ats_verifier': default_tab = 'ats-verification'
    # Subadmin defaults to PO, PO verifier defaults to PO

    # Get active tab from form POST or use default
    active_tab = request.form.get('active_tab', default_tab)

    if request.method == 'POST':
        upload_type = request.form.get('upload_type') # 'po' or 'ats'

        if 'document' not in request.files:
            flash('No file part in the request.', 'warning')
        else:
            doc_files = request.files.getlist('document')
            if not doc_files or all(f.filename == '' for f in doc_files):
                flash('No files selected for upload.', 'warning')
            else:
                processed_count = 0
                for doc_file in doc_files:
                    filename = doc_file.filename
                    if not filename: continue

                    # Create unique temp filename
                    temp_filename = f"{secrets.token_hex(4)}_{filename}"
                    temp_file_path = os.path.join(TEMP_FOLDER, temp_filename)

                    try:
                        doc_file.save(temp_file_path)
                        extracted_text = extract_text_from_file(temp_file_path, filename)
                        file_results = {"extracted_text": extracted_text} # Start with extracted text

                        if extracted_text and not extracted_text.startswith("Error"):
                            if upload_type == 'po':
                                po_fields_to_extract = [crit['field'] for crit in CRITERIA_DB.values() if crit['type'] == 'po']
                                po_fields_to_extract.append("Sr no.") # Example if needed for DB compare
                                structured_data = extract_structured_data(extracted_text, list(set(po_fields_to_extract))) # Use unique fields
                                file_results["structured_data"] = structured_data
                                # Get PO specific criteria for validation
                                po_criteria = {cid: rule for cid, rule in CRITERIA_DB.items() if rule['type'] == 'po'}
                                validation_passed, validation_errors = validate_with_criteria(structured_data, po_criteria)
                                file_results["validation_passed"] = validation_passed
                                file_results["validation_errors"] = validation_errors
                                # Example DB comparison using "Sr no." if extracted
                                sr_no = structured_data.get("Sr no.")
                                if sr_no:
                                     db_data = get_database_data(sr_no)
                                     accuracy, mismatched, comp_error = compare_data(structured_data, db_data)
                                     file_results["accuracy"] = accuracy
                                     file_results["mismatched_fields"] = mismatched
                                     file_results["comparison_error"] = comp_error

                            elif upload_type == 'ats':
                                ats_fields_to_extract = [crit['field'] for crit in CRITERIA_DB.values() if crit['type'] == 'ats']
                                structured_data = extract_structured_data(extracted_text, list(set(ats_fields_to_extract)))
                                file_results["structured_data"] = structured_data
                                # Add ATS specific logic/validation here
                                file_results["ats_analysis"] = "Basic data extracted. ATS validation/matching pending implementation."

                            results[filename] = file_results
                            processed_count += 1

                        else: # Text extraction failed
                             results[filename] = {"error": extracted_text or "Text extraction failed."}

                    except Exception as e:
                        results[filename] = {"error": f"Processing failed for {filename}: {str(e)}"}
                        app.logger.error(f"Error processing {filename}: {e}", exc_info=True) # Log full error
                    finally:
                        if os.path.exists(temp_file_path):
                            try:
                                os.remove(temp_file_path)
                            except OSError as e:
                                app.logger.error(f"Error removing temp file {temp_file_path}: {e}")

                if processed_count > 0:
                    flash(f'Processed {processed_count} file(s).', 'info')
                else:
                     flash('Could not process any files.', 'warning')


        # Ensure the correct tab is active after processing
        active_tab = upload_type + '-verification' if upload_type else default_tab

    # Pass role to template for conditional rendering of tabs
    return render_template('app_dashboard.html',
                           results=results,
                           user_role=user_role,
                           active_tab=active_tab)

# --- Admin Dashboard ---
@app.route('/admin/dashboard')
@admin_required # Use decorator for admin access
def admin_dashboard():
    """Renders the main admin dashboard page."""
    # Counts can be passed if needed, but JS fetches live data anyway
    # user_count = len(USERS_DB)
    # criteria_count = len(CRITERIA_DB)
    # return render_template('admin_dashboard.html', user_count=user_count, criteria_count=criteria_count)
    return render_template('admin_dashboard.html')

# --- Logout ---
@app.route('/logout')
def logout():
    """Logs out the user by clearing the session."""
    session.pop('logged_in', None)
    session.pop('user_email', None)
    session.pop('role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('landing_page'))


# --- === API Endpoints for Admin Console === ---

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_get_users():
    """Returns list of users (username, email, role)."""
    user_list = [{"username": data.get("username", email), # Fallback
                  "email": email,
                  "role": data.get("role", "N/A")}
                 for email, data in USERS_DB.items()]
    return jsonify(user_list)

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def api_add_user():
    """Adds a new user based on the new modal fields."""
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    privileges = data.get('privileges', []) # List: 'admin', 'ats-verifier', 'po-verifier'

    # Validation
    if not username or not email or not password:
        return jsonify({"error": "Username, email, and password are required"}), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
         return jsonify({"error": "Invalid email format"}), 400
    if not privileges:
        return jsonify({"error": "At least one privilege must be selected"}), 400
    if email in USERS_DB:
        return jsonify({"error": "User with this email already exists"}), 409
    # Consider checking if username exists too if needed

    # Determine Role from Privileges
    role = "user" # Should not be assigned if validation passes
    is_admin = "admin" in privileges
    is_po = "po-verifier" in privileges
    is_ats = "ats-verifier" in privileges

    if is_admin:
        role = "admin"
    elif is_po and is_ats:
        role = "subadmin"
    elif is_po:
        role = "po_verifier"
    elif is_ats:
        role = "ats_verifier"
    else:
         # This case should be caught by the frontend validation, but handle defensively
         return jsonify({"error": "Invalid combination of privileges selected"}), 400

    hashed_password = generate_password_hash(password)

    USERS_DB[email] = {
        "username": username,
        "hashed_password": hashed_password,
        "role": role
    }
    print(f"INFO: Admin created user '{username}' ({email}) with Role: {role}")

    return jsonify({
        "message": f"User '{username}' created successfully.",
        "username": username,
        "email": email,
        "role": role
    }), 201

@app.route('/api/admin/users/<string:email>', methods=['DELETE'])
@admin_required
def api_delete_user(email):
    """Deletes a user by email."""
    if email not in USERS_DB:
        return jsonify({"error": "User not found"}), 404
    # Prevent admin from deleting themselves
    if email == session.get('user_email'):
         return jsonify({"error": "Cannot delete the currently logged-in admin account"}), 403

    del USERS_DB[email]
    return jsonify({"message": f"User {email} deleted successfully"}), 200


@app.route('/api/admin/criteria', methods=['GET'])
@admin_required
def api_get_criteria():
    """Returns list of ALL validation criteria."""
    return jsonify(list(CRITERIA_DB.values()))

@app.route('/api/admin/criteria', methods=['POST'])
@admin_required
def api_add_criteria():
    """Adds a new PO or ATS validation criterion."""
    global criteria_id_counter
    data = request.json
    criteria_name = data.get('name')
    field = data.get('field')
    rule = data.get('rule')
    confidence = data.get('confidence')
    criteria_type = data.get('type') # 'po' or 'ats'

    # Validation
    if not all([criteria_name, field, rule, confidence is not None, criteria_type in ['po', 'ats']]):
        return jsonify({"error": "Missing required fields: name, field, rule, confidence, type"}), 400
    try:
        confidence_int = int(confidence)
        if not 0 <= confidence_int <= 100: raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Confidence must be an integer between 0 and 100"}), 400

    new_id = f"crit{criteria_id_counter}"
    criteria_id_counter += 1
    new_criterion = {
        "id": new_id, "name": criteria_name, "field": field,
        "rule": rule, "confidence": confidence_int, "type": criteria_type
    }
    CRITERIA_DB[new_id] = new_criterion # Add to the unified DB
    return jsonify(new_criterion), 201


@app.route('/api/admin/criteria/<string:criteria_id>', methods=['DELETE'])
@admin_required
def api_delete_criteria(criteria_id):
    """Deletes a PO or ATS validation criterion."""
    if criteria_id not in CRITERIA_DB:
        return jsonify({"error": "Criterion not found"}), 404

    deleted_criterion = CRITERIA_DB.pop(criteria_id)
    return jsonify({"message": f"Criterion '{deleted_criterion.get('name', criteria_id)}' deleted"}), 200


# --- Main Execution Block ---
if __name__ == '__main__':
    print("-" * 60)
    print("Flask App Starting...")
    print(f"SECRET_KEY Loaded: {'Yes' if app.secret_key != secrets.token_hex(16) else 'No (Using Temporary)'}")
    print(f"OCR_SPACE_API_KEY Loaded: {'Yes' if OCR_SPACE_API_KEY != 'K87955728688957' else 'No (Using Placeholder)'}")
    print("WARNING: User data and criteria are stored IN-MEMORY.")
    print("         Data will be LOST when the application restarts.")
    print("         Use a persistent database for production.")
    print("-" * 60)
    app.run(debug=True, host='0.0.0.0') # host='0.0.0.0' makes it accessible on your network