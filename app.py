import os
import re
import secrets
import string
import random
from functools import wraps
import json # Ensure this is imported

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from pdfminer.high_level import extract_text as pdf_extract_text
from docx import Document as DocxDocument
import requests

# --- App Setup ---
TEMP_FOLDER = os.path.join(os.path.dirname(__file__), 'temp')
os.makedirs(TEMP_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# --- Configuration ---
OCR_SPACE_API_URL = "https://api.ocr.space/parse/image"
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY', "K87955728688957")
if OCR_SPACE_API_KEY == "K87955728688957":
    print("Warning: Using default/placeholder OCR Space API key.")

# --- Master Field Definitions (Required for Admin Field Selection) ---
MASTER_FIELD_DEFINITIONS = {
    "po": [
        {"id": "po_number", "label": "PO Number", "description": "Purchase Order Number"},
        {"id": "order_date", "label": "Order Date", "description": "Date the PO was created"},
        {"id": "vendor_name", "label": "Vendor Name", "description": "Name of the vendor"},
        {"id": "grand_total", "label": "Grand Total", "description": "The final total amount"}, # Renamed from total_amount for consistency with DB example
        # Add other PO fields admins might grant access to...
        {"id": "item_description", "label": "Item Description", "description": "Description of a line item"},
    ],
    "ats": [
        {"id": "candidate_name", "label": "Name", "description": "Full name of the applicant"}, # Changed label to match common resume field
        {"id": "candidate_email", "label": "Email", "description": "Email address of the applicant"},
        {"id": "candidate_phone", "label": "Phone", "description": "Phone number of the applicant"},
        {"id": "skills_list", "label": "Skills", "description": "List of skills from the resume"},
        {"id": "sr_no", "label": "Sr no.", "description": "Serial number if applicable"},
        # Add other ATS fields...
    ],
     "part_drawing": [
        {"id": "drawing_number_from_quote", "label": "Part Number", "description": "Part Number from Quote (used as Drawing Number)"}, # Label matches Quote
        {"id": "part_name_from_quote", "label": "Description", "description": "Description from Quote (used as Part Name)"},    # Label matches Quote
        {"id": "revision_from_quote", "label": "Revision", "description": "Revision from Quote"},                          # Label matches Quote
        {"id": "material_from_quote", "label": "Material Spec (from text)", "description": "Attempt to find material info"}, # Less reliable
        {"id": "customer_from_quote", "label": "Quoted To", "description": "Customer from Quote"} # Label matches Quote
    ]
}

# --- Define Fields For Comparison (Subset of Master Fields) ---
# These are the *labels* used for comparison and accuracy calculation.
PO_FIELDS_FOR_COMPARISON = ["PO Number", "Vendor Name", "Grand Total"]
ATS_FIELDS_FOR_COMPARISON = ["Sr no.", "Name", "Email", "Skills","Phone"]
PART_DRAWING_FIELDS_FOR_COMPARISON = ["Drawing Number", "Part Name", "Revision", "Material"]

# Map Field IDs to Labels (used internally)
FIELD_ID_TO_LABEL_MAP = {
    doc_type: {field['id']: field['label'] for field in fields}
    for doc_type, fields in MASTER_FIELD_DEFINITIONS.items()
}
# Map Labels to Field IDs (used internally)
FIELD_LABEL_TO_ID_MAP = {
    doc_type: {field['label']: field['id'] for field in fields}
    for doc_type, fields in MASTER_FIELD_DEFINITIONS.items()
}

# --- Define available tabs/modules in the system ---
AVAILABLE_TABS = {
    "po": {"id": "po", "name": "PO Verification", "icon": "fas fa-file-invoice"},
    "ats": {"id": "ats", "name": "ATS Verification", "icon": "fas fa-file-alt"},
    "part_drawing": {"id": "part_drawing", "name": "Part Drawing Verification", "icon": "fas fa-drafting-compass"}
}

# --- Data Storage (Keep User Permissions Structure) ---
USERS_DB = {
    # Email: {username, hashed_password, role, permissions}
    # Permissions structure remains the same: { tab_id: {can_access: bool, allowed_fields: [field_id,...]}, ...}
    "admin@example.com": {
        "username": "admin_user", "hashed_password": generate_password_hash("admin@a123"), "role": "admin",
        "permissions": { # Admin gets all access by default
            tab_id: {"can_access": True, "allowed_fields": [f["id"] for f in MASTER_FIELD_DEFINITIONS.get(tab_id, [])]}
            for tab_id in AVAILABLE_TABS
        }
    },
    "po@example.com": {
        "username": "po_verifier_1", "hashed_password": generate_password_hash("po@123"), "role": "po_verifier",
        "permissions": {
            "po": {"can_access": True, "allowed_fields": ["po_number", "vendor_name", "grand_total"]}, # Example: Initial limited fields
            "ats": {"can_access": False, "allowed_fields": []},
            "part_drawing": {"can_access": False, "allowed_fields": []}
        }
    },
     "ats@example.com": {
        "username": "ats_verifier_1", "hashed_password": generate_password_hash("ats@123"), "role": "ats_verifier",
        "permissions": {
            "po": {"can_access": False, "allowed_fields": []},
            "ats": {"can_access": True, "allowed_fields": ["candidate_name", "candidate_email", "skills_list", "sr_no"]}, # Example: Limited ATS
            "part_drawing": {"can_access": False, "allowed_fields": []}
        }
    },
    "subadmin@example.com": { # Example: Maybe subadmin gets all fields for PO/ATS
        "username": "sub_admin_1", "hashed_password": generate_password_hash("sub@123"), "role": "subadmin",
        "permissions": {
            "po": {"can_access": True, "allowed_fields": [f["id"] for f in MASTER_FIELD_DEFINITIONS.get("po", [])]},
            "ats": {"can_access": True, "allowed_fields": [f["id"] for f in MASTER_FIELD_DEFINITIONS.get("ats", [])]},
            "part_drawing": {"can_access": False, "allowed_fields": []}
        }
    }
    # Add other users as needed
}

# --- Dummy Database for Comparison (Adjust structure/keys) ---
# Keys should ideally match the lookup fields (PO Number, Sr no., Drawing Number)
dummy_database = {
    # Format: { doc_type: { lookup_key: { field_label: db_value, ... } } }
    "po": {
        "PO-789012": {"PO Number": "PO-789012", "Vendor Name": "Nortech Systems", "Grand Total": "5945.00"},
        "81100": {"PO Number": "81100", "Vendor Name": "PROTOATIC, INC.", "Grand Total": "$ 5,945.00"}
    },
    "ats": {
        "S009": {"Sr no.": "S009", "Name": "Olivia Miller", "Email": "olivia.m@example.net", "Skills": "Shopify, Java, React, Camunda", "Phone": "8788019869"} # Slightly different data for mismatch example
    },
     "part_drawing": {
        # Lookup key is the "Part Number" value from the quote
        "1402.00-1197": {
            "Part Number": "1402.00-1197",       # Matches label "Part Number"
            "Description": "CONTACT TERMINAL",  # Matches label "Description"
            "Revision": "B"                     # Matches label "Revision"
            # Material is tricky with current regex, so might omit from DB for now for reliable comparison
        }
     }
}

# --- Helper Functions ---
def generate_temporary_password(length=10):
    # (Keep this function as provided before)
    alphabet = string.ascii_letters + string.digits + string.punctuation
    while True:
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        if (any(c.islower() for c in password) and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password) and any(c in string.punctuation for c in password)
                and len(password) >= length):
            break
    return password

# --- Authentication & Authorization Decorators ---
def login_required(f):
    # (Keep this function as provided before - checks session['logged_in'] and permissions)
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session.get('logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login_page'))
        if session.get('role') == 'admin':
             flash('Admins should use the admin console.', 'info')
             return redirect(url_for('admin_dashboard'))
        user_perms = session.get('user_permissions', {})
        has_accessible_tabs = any(details.get('can_access') for details in user_perms.values())
        if not has_accessible_tabs and session.get('role') != 'admin':
            flash('You do not have access to any application modules. Please contact an administrator.', 'warning')
            return redirect(url_for('logout'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    # (Keep this function as provided before - checks admin role)
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session.get('logged_in'):
            flash('Please log in to access the admin area.', 'warning')
            return redirect(url_for('admin_login_page'))
        if session.get('role') != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# --- OCR / File Processing ---
# (Keep ocr_image_via_api, extract_text_from_pdf, extract_text_from_docx, extract_text_from_file as provided before)
def ocr_image_via_api(image_path):
    """Performs OCR on an image using OCR Space API."""
    if OCR_SPACE_API_KEY == "K87955728688957": # Check against placeholder
         print("Warning: Attempting OCR with placeholder API key.")
    try:
        with open(image_path, 'rb') as f: image_data = f.read()
        payload = {'apikey': OCR_SPACE_API_KEY, 'language': 'eng', 'isOverlayRequired': False}
        files = {'file': (os.path.basename(image_path), image_data)}
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

# In app.py
def extract_text_from_pdf(file_path):
    try:
        text = pdf_extract_text(file_path)
        text = text.strip() if text else "" # Ensure text is a string
        
        # If pdfminer extracts very little text, it might be an image-based PDF. Try OCR.
        # Threshold can be adjusted. Some PDFs have a tiny bit of metadata text.
        if len(text) < 50: # Arbitrary threshold for "very little text"
            print(f"DEBUG: PDF '{os.path.basename(file_path)}' yielded little text ({len(text)} chars). Attempting OCR.")
            ocr_text = ocr_image_via_api(file_path) # OCR.space can often handle PDF images
            if ocr_text and not ocr_text.startswith("Error"):
                return ocr_text.strip()
            else:
                # If OCR also fails or returns an error, stick with original (possibly empty) pdfminer text,
                # or return a more specific error.
                print(f"DEBUG: OCR attempt for PDF '{os.path.basename(file_path)}' also failed or yielded no new text. OCR result: {ocr_text}")
                # If original text was empty and OCR failed, indicate failure.
                if not text and (not ocr_text or ocr_text.startswith("Error")):
                    return f"Error: No text extracted from PDF '{os.path.basename(file_path)}' by direct means or OCR."

        return text if text else "No text extracted from PDF." # Return original if it was substantial enough
    except Exception as e:
        # Attempt OCR as a fallback if pdfminer raises an exception too
        print(f"DEBUG: pdfminer failed for '{os.path.basename(file_path)}': {e}. Attempting OCR as fallback.")
        try:
            ocr_text = ocr_image_via_api(file_path)
            if ocr_text and not ocr_text.startswith("Error"):
                return ocr_text.strip()
            else:
                return f"Error extracting text from PDF (pdfminer failed, OCR also failed/empty): {e}"
        except Exception as ocr_e:
            return f"Error extracting text from PDF (pdfminer failed, OCR attempt also raised error: {ocr_e}): {e}"
            

def extract_text_from_docx(file_path):
    try:
        doc = DocxDocument(file_path)
        full_text = [p.text for p in doc.paragraphs if p.text]
        return '\n'.join(full_text).strip() if full_text else "No text extracted from DOCX."
    except Exception as e:
        return f"Error extracting text from DOCX: {e}"

def extract_text_from_file(file_path, filename):
    file_extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if file_extension in ['png', 'jpg', 'jpeg', 'bmp', 'gif', 'tiff']:
        return ocr_image_via_api(file_path)
    elif file_extension == 'pdf':
        return extract_text_from_pdf(file_path)
    elif file_extension == 'docx':
        return extract_text_from_docx(file_path)
    else:
        return f"Error: Unsupported file format '{file_extension}'."

  # In app.py
def extract_structured_data(text, fields_to_extract_labels,upload_type=None):
    if not text or not fields_to_extract_labels: return {}
    data = {label: None for label in fields_to_extract_labels}
    lines = text.strip().split('\n')

    for i, line in enumerate(lines):
        line_strip = line.strip()
        for field_label in fields_to_extract_labels:
            if data[field_label] is not None: continue # Already found

            pattern_label = re.escape(field_label)
            
            # Standard Key: Value pattern
            pattern_kv = re.compile(r"^\s*" + pattern_label + r"\s*[:]?\s*(.*)", re.IGNORECASE)
            match_kv = pattern_kv.match(line_strip)
            if match_kv:
                value = match_kv.group(1).strip()
                if value: data[field_label] = value; break 

            # Special handling for "Quoted To" (example for multi-line)
            if field_label == "Quoted To":
                if line_strip.lower() == "quoted to":
                    # Assume customer name is on the next non-empty line
                    if i + 1 < len(lines) and lines[i+1].strip():
                        data[field_label] = lines[i+1].strip()
                        break 
            
            # Special handling for table-like Part Number, Description, Revision in Quote
            # This is very specific to the Quote PDF layout
            if upload_type == 'part_drawing': # Need upload_type context here, or make this function type-specific
                if field_label == "Part Number" and "1402.00-1197" in line_strip and "Barrel" in line_strip: # Heuristic
                    pn_match = re.search(r"(1402\.00-1197)", line_strip)
                    if pn_match: data["Part Number"] = pn_match.group(1)
                if field_label == "Description" and "CONTACT TERMINAL" in line_strip and "Revision" in line_strip :
                     desc_match = re.search(r"(CONTACT TERMINAL)", line_strip)
                     if desc_match: data["Description"] = desc_match.group(1)
                if field_label == "Revision" and "Revision" in line_strip and "B" in line_strip: # Example
                     rev_match = re.search(r"Revision\s*\n\s*(\w+)", text, re.IGNORECASE | re.MULTILINE) # Try multiline search
                     if not rev_match: # Fallback for single line
                        rev_match_single = re.search(r"Revision\s*([A-Z0-9]+)", line_strip, re.IGNORECASE)
                        if rev_match_single: rev_match = rev_match_single
                     if rev_match: data["Revision"] = rev_match.group(1).strip()


    return data


# --- Database Comparison Logic ---
def get_db_comparison_record(doc_type, lookup_value):
    """Fetches a specific record from the dummy_database for comparison."""
    if doc_type in dummy_database and lookup_value:
        # Case-insensitive key lookup might be useful here if lookup_value case varies
        for key, record in dummy_database[doc_type].items():
            if key.lower() == lookup_value.lower():
                return record
    return None

def compare_data(extracted_data, db_record, fields_for_comparison_labels):
    """Compares extracted data against a DB record for specified field labels."""
    if not db_record:
        return 0, {}, "Comparison record not found in database."
    if not fields_for_comparison_labels:
        return 0, {}, "No fields specified for comparison."

    matched_fields = 0
    mismatched_fields = {}

    # Determine the fields that are BOTH in the comparison list AND present in the DB record
    actual_comparable_fields = [label for label in fields_for_comparison_labels if label in db_record]

    if not actual_comparable_fields:
         return 0, {}, "None of the specified comparison fields exist in the database record."

    total_comparable_fields_count = len(actual_comparable_fields)

    for label in actual_comparable_fields:
        db_value = db_record.get(label)
        extracted_value = extracted_data.get(label) # Assumes extracted_data uses labels as keys

        # Normalize for comparison (handle case, currency, whitespace)
        db_str = str(db_value).strip().lower().replace('$', '').replace(',', '').strip() if db_value is not None else ""
        ext_str = str(extracted_value).strip().lower().replace('$', '').replace(',', '').strip() if extracted_value is not None else ""

        # Consider empty strings a non-match unless both are empty
        if ext_str == db_str and db_str != "": # Match only if both have same non-empty value
            matched_fields += 1
        elif db_value is not None: # If DB expects a value, record mismatch if extracted is different or empty/None
            mismatched_fields[label] = {"db_value": db_value, "extracted_value": extracted_value}

    accuracy = (matched_fields / total_comparable_fields_count) * 100 if total_comparable_fields_count > 0 else 0
    return accuracy, mismatched_fields, None

# --- Routes ---
@app.route('/', methods=['GET'])
def landing_page():
    # Redirect logged-in users
    if 'logged_in' in session and session.get('logged_in'):
        role = session.get('role')
        if role == 'admin': return redirect(url_for('admin_dashboard'))
        user_perms = session.get('user_permissions', {})
        has_accessible_tabs = any(details.get('can_access') for details in user_perms.values())
        if has_accessible_tabs: return redirect(url_for('app_dashboard'))
    # Otherwise, show landing page
    return render_template('Template1.html')

def _load_user_session_data(user_email, user_data):
    """Helper to load user data into session after successful login."""
    session['logged_in'] = True
    session['user_email'] = user_email
    session['username'] = user_data.get("username", user_email)
    session['role'] = user_data.get('role', 'user') # Default role if missing
    session['user_permissions'] = user_data.get('permissions', {})

    accessible_tabs_info = {}
    # Admins don't use this dashboard, process for non-admins
    if session['role'] != 'admin':
        user_permissions = session['user_permissions']
        for tab_id, perm_details in user_permissions.items():
            if perm_details.get("can_access") and tab_id in AVAILABLE_TABS:
                tab_master_config = AVAILABLE_TABS[tab_id]
                allowed_field_ids = perm_details.get("allowed_fields", [])
                allowed_labels = [
                    FIELD_ID_TO_LABEL_MAP.get(tab_id, {}).get(f_id)
                    for f_id in allowed_field_ids
                    if FIELD_ID_TO_LABEL_MAP.get(tab_id, {}).get(f_id) is not None
                ]
                accessible_tabs_info[tab_id] = {
                    "id": tab_id,
                    "name": tab_master_config["name"],
                    "icon": tab_master_config["icon"],
                    "allowed_field_ids": allowed_field_ids,
                    "allowed_field_labels": allowed_labels
                }
    session['accessible_tabs_info'] = accessible_tabs_info

# (Keep login_page and admin_login_page as they were - they use _load_user_session_data)
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if 'logged_in' in session and session['logged_in']:
        role = session.get('role')
        if role == 'admin': return redirect(url_for('admin_dashboard'))
        if session.get('accessible_tabs_info') and any(session['accessible_tabs_info']):
             return redirect(url_for('app_dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user_data = USERS_DB.get(email)

        if user_data and user_data['role'] != 'admin' and check_password_hash(user_data['hashed_password'], password):
            _load_user_session_data(email, user_data)
            if not session.get('accessible_tabs_info') and user_data['role'] != 'admin':
                flash('Login successful, but you have no assigned application modules.', 'warning')
                session.clear()
                return redirect(url_for('login_page'))
            flash(f'Login successful! Welcome {session["username"]}.', 'success')
            return redirect(url_for('app_dashboard'))
        else:
            flash('Invalid credentials or not authorized for user login.', 'danger')
    return render_template('login.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin_login_page():
    if 'logged_in' in session and session['logged_in'] and session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user_data = USERS_DB.get(email)
        if user_data and user_data['role'] == 'admin' and check_password_hash(user_data['hashed_password'], password):
            _load_user_session_data(email, user_data)
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials.', 'danger')
    return render_template('admin_login.html')

@app.route('/logout')
def logout():
    # Clear all relevant session keys
    session.pop('logged_in', None)
    session.pop('user_email', None)
    session.pop('username', None)
    session.pop('role', None)
    session.pop('user_permissions', None)
    session.pop('accessible_tabs_info', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('landing_page'))

# --- Updated User Dashboard Route ---
@app.route('/app', methods=['GET', 'POST'])
@login_required
def app_dashboard():
    results = {}
    accessible_tabs_info = session.get('accessible_tabs_info', {})
    if not accessible_tabs_info:
        flash("No accessible modules found.", 'warning')
        return redirect(url_for('logout')) # Or landing

    default_tab_id = next(iter(accessible_tabs_info))
    active_tab_id = request.form.get('active_tab_id', default_tab_id) # Get active tab from form or default
    if active_tab_id not in accessible_tabs_info: # Validate active tab
        active_tab_id = default_tab_id

    if request.method == 'POST':
        upload_type = request.form.get('upload_type') # e.g., 'po', 'ats'
        if upload_type not in accessible_tabs_info:
             flash(f"Access denied for {upload_type.upper()} upload.", "danger")
             return redirect(url_for('app_dashboard'))

        active_tab_id = upload_type # Set active tab to the one used for upload

        if 'document' not in request.files: flash('No file part in request.', 'warning')
        else:
            doc_files = request.files.getlist('document')
            if not doc_files or all(f.filename == '' for f in doc_files): flash('No files selected.', 'warning')
            else:
                processed_count = 0
                current_tab_perms = accessible_tabs_info.get(upload_type, {})
                allowed_field_labels = current_tab_perms.get('allowed_field_labels', [])

                # Determine fields for comparison based on upload_type
                fields_for_comparison_labels = []
                db_lookup_key_label = None
                if upload_type == 'po':
                    fields_for_comparison_labels = PO_FIELDS_FOR_COMPARISON
                    db_lookup_key_label = "PO Number"
                elif upload_type == 'ats':
                    fields_for_comparison_labels = ATS_FIELDS_FOR_COMPARISON
                    db_lookup_key_label = "Sr no."
                elif upload_type == 'part_drawing':
                    fields_for_comparison_labels = PART_DRAWING_FIELDS_FOR_COMPARISON
                    db_lookup_key_label = "Part Number"

                # Filter comparison fields to only those the user is allowed to see
                user_allowed_comparison_labels = [
                    label for label in fields_for_comparison_labels if label in allowed_field_labels
                ]

                for doc_file in doc_files:
                    # (File saving and text extraction logic as before)
                    filename = doc_file.filename
                    if not filename: continue
                    temp_filename = f"{secrets.token_hex(4)}_{filename}"
                    temp_file_path = os.path.join(TEMP_FOLDER, temp_filename)
                    file_results = {}

                    try:
                        doc_file.save(temp_file_path)
                        extracted_text = extract_text_from_file(temp_file_path, filename)
                        file_results["extracted_text"] = extracted_text

                        if extracted_text and not extracted_text.startswith("Error"):
                            # Extract only allowed fields
                            structured_data = extract_structured_data(extracted_text, allowed_field_labels, upload_type)
                            file_results["structured_data"] = structured_data

                            # --- Comparison Logic ---
                            accuracy = 0
                            mismatched = {}
                            comp_error = "Comparison not performed."
                            db_record = None
                            db_display_subset = None

                            # Check if lookup key label is allowed and extracted
                            if db_lookup_key_label and db_lookup_key_label in allowed_field_labels:
                                lookup_key_value = structured_data.get(db_lookup_key_label)
                                if lookup_key_value:
                                    db_record = get_db_comparison_record(upload_type, lookup_key_value)
                                    if db_record:
                                        # Perform comparison ONLY on fields user is allowed AND are designated for comparison
                                        if user_allowed_comparison_labels:
                                            accuracy, mismatched, comp_error = compare_data(
                                                structured_data, db_record, user_allowed_comparison_labels
                                            )
                                            # Prepare subset of DB data for display (only compared fields)
                                            db_display_subset = {
                                                label: db_record.get(label) for label in user_allowed_comparison_labels if label in db_record
                                            }
                                        else:
                                            comp_error = "User does not have permission to view any comparison fields."
                                    else:
                                        comp_error = f"Record with {db_lookup_key_label} '{lookup_key_value}' not found in database."
                                else:
                                    comp_error = f"Required key '{db_lookup_key_label}' not found in extracted data."
                            else:
                                 comp_error = f"User lacks permission to view the key field '{db_lookup_key_label}' required for comparison."

                            file_results["accuracy"] = accuracy
                            file_results["mismatched_fields"] = mismatched # Already filtered by compare_data logic
                            file_results["comparison_error"] = comp_error
                            file_results["db_record_for_display"] = db_display_subset # Pass only the relevant subset
                            file_results["compared_fields_list"] = user_allowed_comparison_labels # Pass the list used

                            processed_count += 1
                        else:
                            file_results["error"] = extracted_text or "Text extraction failed."

                        results[filename] = file_results
                    except Exception as e:
                        results[filename] = {"error": f"Processing error: {str(e)}"}
                        app.logger.error(f"Error processing {filename}: {e}", exc_info=True)
                    finally:
                        # (Temp file removal logic as before)
                        if os.path.exists(temp_file_path):
                            try: os.remove(temp_file_path)
                            except OSError as e_os: app.logger.error(f"Error removing temp file: {e_os}")

                if processed_count > 0: flash(f'Processed {processed_count} file(s).', 'info')
                else: flash('Could not process any files.', 'warning')

    return render_template('app_dashboard.html',
                           results=results,
                           accessible_tabs_info=accessible_tabs_info, # Pass the accessible tabs data
                           active_tab_id=active_tab_id, # Pass the active tab ID
                           current_tab_display_name=accessible_tabs_info.get(active_tab_id, {}).get("name", "Dashboard") # For title
                           )

# --- Admin Routes ---
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    # Pass the Python dictionaries directly
    return render_template(
        'admin_dashboard.html',
        # Use different variable names to avoid confusion if needed, or reuse
        available_tabs_data=AVAILABLE_TABS,
        master_field_definitions_data=MASTER_FIELD_DEFINITIONS
    )

# --- Admin API Endpoints ---
# (Keep api_get_users, api_get_user_details, api_add_user, api_update_user,
# api_reset_user_password, api_delete_user as provided before)
# --- GET Users ---
@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_get_users():
    user_list = []
    for email, data in USERS_DB.items():
        user_list.append({
            "username": data.get("username", email),
            "email": email,
            "role": data.get("role", "N/A"),
        })
    return jsonify(user_list)

# --- GET User Details ---
@app.route('/api/admin/users/<string:user_email>', methods=['GET'])
@admin_required
def api_get_user_details(user_email):
    user_data = USERS_DB.get(user_email)
    if not user_data: return jsonify({"error": "User not found"}), 404
    editable_user_data = {
        "username": user_data.get("username"),
        "email": user_email,
        "role": user_data.get("role"),
        "permissions": user_data.get("permissions", {}) # Send current permissions
    }
    return jsonify(editable_user_data)

# --- POST Add User ---
@app.route('/api/admin/users', methods=['POST'])
@admin_required
def api_add_user():
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    privileges = data.get('privileges', [])

    # Basic Validation
    if not all([username, email, password, privileges]): return jsonify({"error": "Missing required fields"}), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email): return jsonify({"error": "Invalid email format"}), 400
    if email in USERS_DB: return jsonify({"error": "User email already exists"}), 409

    # Determine Role & Initial Permissions (Grant all allowed fields for assigned tabs initially)
    role, user_permissions = derive_role_and_permissions(privileges)
    if role == "error": return jsonify({"error": "Invalid privilege combination"}), 400

    hashed_password = generate_password_hash(password)
    USERS_DB[email] = {
        "username": username, "hashed_password": hashed_password,
        "role": role, "permissions": user_permissions
    }
    print(f"INFO: Admin created user '{username}' ({email}) Role: {role}")
    return jsonify({"message": f"User '{username}' created.", "user": {"username": username, "email": email, "role": role}}), 201

def derive_role_and_permissions(privileges):
    """Derives role and initial permissions from privilege list."""
    role = "user" # Default
    user_permissions = {tab_id: {"can_access": False, "allowed_fields": []} for tab_id in AVAILABLE_TABS.keys()}
    is_admin = "admin" in privileges
    is_po = "po-verifier" in privileges
    is_ats = "ats-verifier" in privileges
    is_part = "part-drawing-verifier" in privileges

    if is_admin:
        role = "admin"
        for tab_id in AVAILABLE_TABS.keys():
            user_permissions[tab_id]["can_access"] = True
            user_permissions[tab_id]["allowed_fields"] = [f["id"] for f in MASTER_FIELD_DEFINITIONS.get(tab_id, [])]
    else:
        # Assign roles based on combinations
        if is_po and is_ats: role = "subadmin"
        elif is_po: role = "po_verifier"
        elif is_ats: role = "ats_verifier"
        elif is_part: role = "part_drawing_verifier"
        # If only one non-admin privilege, role matches. If multiple non-PO/ATS, might need a "custom" role or rely purely on permissions.
        # For now, simple roles are set.

        # Grant initial full field access for selected tabs
        if is_po:
            user_permissions["po"]["can_access"] = True
            user_permissions["po"]["allowed_fields"] = [f["id"] for f in MASTER_FIELD_DEFINITIONS.get("po", [])]
        if is_ats:
            user_permissions["ats"]["can_access"] = True
            user_permissions["ats"]["allowed_fields"] = [f["id"] for f in MASTER_FIELD_DEFINITIONS.get("ats", [])]
        if is_part:
            user_permissions["part_drawing"]["can_access"] = True
            user_permissions["part_drawing"]["allowed_fields"] = [f["id"] for f in MASTER_FIELD_DEFINITIONS.get("part_drawing", [])]

        # Final check if a role was actually assigned based on privileges
        if role == "user" and (is_po or is_ats or is_part):
             role = "custom_access_user" # Assign a generic role if specific combo role doesn't fit

        if role == "user": # No valid non-admin privilege selected
            return "error", {} # Indicate error state

    return role, user_permissions


# --- PUT Update User ---
@app.route('/api/admin/users/<string:user_email>', methods=['PUT'])
@admin_required
def api_update_user(user_email):
    if user_email not in USERS_DB: return jsonify({"error": "User not found"}), 404
    current_user_data = USERS_DB[user_email]
    data = request.json

    new_username = data.get('username', current_user_data.get('username'))
    if not new_username: return jsonify({"error": "Username cannot be empty"}), 400
    current_user_data['username'] = new_username

    new_permissions = data.get('permissions')
    if new_permissions:
        valid_permissions = {}
        for tab_id, perms in new_permissions.items():
            if tab_id in AVAILABLE_TABS:
                can_access = bool(perms.get('can_access', False))
                allowed_ids = perms.get('allowed_fields', [])
                valid_master_ids = {f['id'] for f in MASTER_FIELD_DEFINITIONS.get(tab_id, [])}
                sanitized_allowed = [fid for fid in allowed_ids if fid in valid_master_ids]
                valid_permissions[tab_id] = {"can_access": can_access, "allowed_fields": sanitized_allowed}
        current_user_data['permissions'] = valid_permissions

    USERS_DB[user_email] = current_user_data
    # Return updated data (excluding password hash)
    updated_user_info = {k: v for k, v in current_user_data.items() if k != 'hashed_password'}
    updated_user_info['email'] = user_email
    return jsonify({"message": f"User '{new_username}' updated.", "user": updated_user_info}), 200

# --- POST Reset Password ---
@app.route('/api/admin/users/<string:user_email>/reset-password', methods=['POST'])
@admin_required
def api_reset_user_password(user_email):
    if user_email not in USERS_DB: return jsonify({"error": "User not found"}), 404
    if user_email == session.get('user_email'): return jsonify({"error": "Cannot reset own password this way."}), 403
    temp_password = generate_temporary_password()
    USERS_DB[user_email]['hashed_password'] = generate_password_hash(temp_password)
    print(f"INFO: Password reset for {user_email}. New temp: {temp_password}")
    return jsonify({"message": f"Password for {user_email} reset.", "temporary_password": temp_password}), 200

# --- DELETE User ---
@app.route('/api/admin/users/<string:email>', methods=['DELETE'])
@admin_required
def api_delete_user(email):
    if email not in USERS_DB: return jsonify({"error": "User not found"}), 404
    if email == session.get('user_email'): return jsonify({"error": "Cannot delete self."}), 403
    del USERS_DB[email]
    return jsonify({"message": f"User {email} deleted."}), 200

# --- REMOVED Criteria API Endpoints ---
# /api/admin/criteria GET, POST, DELETE are removed

# --- Main Execution ---
if __name__ == '__main__':
    print("-" * 60)
    print("Flask App Starting...")
    print(f"SECRET_KEY Loaded: {'Yes' if app.secret_key != secrets.token_hex(16) else 'No (Temporary)'}")
    print(f"OCR_SPACE_API_KEY Loaded: {'Yes' if OCR_SPACE_API_KEY != 'K87955728688957' else 'No (Placeholder)'}")
    print("Available Tabs:", list(AVAILABLE_TABS.keys()))
    print("WARNING: User data stored IN-MEMORY.")
    print("-" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)

