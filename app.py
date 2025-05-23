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
        {"id": "po_doc_number", "label": "PO Number", "description": "Purchase Order Number"},
        {"id": "po_doc_vendor_id", "label": "Vendor", "description": "Vendor ID (e.g., S101334)"},
        {"id": "po_doc_phone", "label": "Phone", "description": "Vendor Phone Number"},
        {"id": "po_doc_total", "label": "Total", "description": "Grand Total Amount"},
        {"id": "po_doc_order_date", "label": "Order Date", "description": "PO Order Date"},
        # Add any other PO fields an admin might *optionally* grant access to,
        # even if they are not part of the key comparison set.
        # {"id": "po_doc_vendor_name", "label": "Vendor Name", "description": "Full Vendor Name (e.g. PROTOATIC, INC.)"},
    ],
    "ats": [ # Keep ATS simple as per previous versions, unless specified
        {"id": "ats_name", "label": "Name", "description": "Applicant Name"},
        {"id": "ats_email", "label": "Email", "description": "Applicant Email"},
        {"id": "ats_phone", "label": "Phone", "description": "Applicant Phone"},
        {"id": "ats_skills", "label": "Skills", "description": "Applicant Skills"},
        {"id": "ats_sr_no", "label": "Sr no.", "description": "Applicant Sr no."},
    ],
    "part_drawing": [ # Fields for the Quote PDF
        {"id": "pd_quote_id", "label": "Quote ID", "description": "Quote Identifier"},
        {"id": "pd_customer_id", "label": "Customer ID", "description": "Customer Identifier from Quote"},
        {"id": "pd_quote_date", "label": "Quote Date", "description": "Date of the Quote"},
        {"id": "pd_expiration_date", "label": "Expiration Date", "description": "Expiration Date of the Quote"},
        {"id": "pd_sales_contact", "label": "Sales Contact", "description": "Sales Contact from Quote"},
        {"id": "pd_quote_terms", "label": "Quote Terms", "description": "Payment/Quote Terms from Quote"},
        {"id": "pd_table_part_no", "label": "Part Number", "description": "Part Number from Quote Table"},
        {"id": "pd_table_description", "label": "Description", "description": "Description from Quote Table"},
        {"id": "pd_table_revision", "label": "Revision", "description": "Revision from Quote Table"},
    ]
}


# --- Define Fields For Comparison (Subset of Master Fields) ---
# These are the *labels* used for comparison and accuracy calculation.
# In app.py
PO_KEY_COMPARISON_FIELDS = ["PO Number", "Vendor", "Phone", "Total", "Order Date"]
ATS_KEY_COMPARISON_FIELDS = ["Sr no.", "Name", "Email", "Skills", "Phone"] # Assuming this is desired for ATS
PART_DRAWING_KEY_COMPARISON_FIELDS = ["Quote ID", "Customer ID", "Quote Date", "Part Number", "Description", "Revision"] # Choose your 3-4 key ones


# FIELD_ID_TO_LABEL_MAP will regenerate based on the above
FIELD_ID_TO_LABEL_MAP = {
    doc_type: {field['id']: field['label'] for field in fields}
    for doc_type, fields in MASTER_FIELD_DEFINITIONS.items()
}
# # Map Field IDs to Labels (used internally)
# FIELD_ID_TO_LABEL_MAP = {
#     doc_type: {field['id']: field['label'] for field in fields}
#     for doc_type, fields in MASTER_FIELD_DEFINITIONS.items()
# }
# # Map Labels to Field IDs (used internally)
# FIELD_LABEL_TO_ID_MAP = {
#     doc_type: {field['label']: field['id'] for field in fields}
#     for doc_type, fields in MASTER_FIELD_DEFINITIONS.items()
# }

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
    "po": {
        "81100": { # Lookup key is PO Number
            "PO Number": "81100",
            "Vendor": "S101334",    # Label matches PO_KEY_COMPARISON_FIELDS
            "Phone": "734-426-3655",
            "Total": "$ 5,945.00",
            "Order Date": "8/8/2024"
        }
        # Add more PO examples if needed
    },
    "ats": { # ATS data
        "S009": {"Sr no.": "S009", "Name": "Olivia Miller", "Email": "olivia.m@example.net", "Skills": "Shopify, Java, React, Camunda", "Phone": "8788019869"}
    },
    "part_drawing": { # Lookup key is Quote ID
        "16Q05495": {
            "Quote ID": "16Q05495",
            # These are from PART_DRAWING_KEY_COMPARISON_FIELDS:
            "Part Number": "1402.00-1197",    # Data for the table line item
            "Description": "CONTACT TERMINAL",
            "Revision": "B",
            # You can include other fields here if they are also in PART_DRAWING_KEY_COMPARISON_FIELDS
            # For example, if you added "Customer ID" to the comparison list:
            # "Customer ID": "PRO120",
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

# In app.py
def extract_structured_data(text, fields_to_extract_labels, upload_type=None):
    if not text or not fields_to_extract_labels: return {}
    data = {label: None for label in fields_to_extract_labels}
    lines = text.strip().split('\n')
    text_content_lower = text.lower()

    # --- Generic Key-Value Extraction (as a fallback or for simple fields) ---
    for i, line_text in enumerate(lines):
        line_strip = line_text.strip()
        for field_label in fields_to_extract_labels:
            if data[field_label] is not None: continue # Already found by specific logic or previous generic match

            pattern_label = re.escape(field_label)
            # Common: Label: Value
            match = re.match(r"^\s*" + pattern_label + r"\s*:\s*(.+)", line_strip, re.IGNORECASE)
            if match:
                data[field_label] = match.group(1).strip()
                continue

            # Less common: Label Value (on same line) - use with caution
            if field_label.lower() in ["po number", "order date", "quote id", "customer id"]: # Only for specific labels
                match_space = re.match(r"^\s*" + pattern_label + r"\s+([^\s].*)", line_strip, re.IGNORECASE)
                if match_space:
                    potential_value = match_space.group(1).strip()
                    if len(potential_value) > 1 and not any(other_label.lower() in potential_value.lower() for other_label in fields_to_extract_labels if other_label != field_label and len(other_label)>2):
                        data[field_label] = potential_value
                        continue
    
    # --- PO Specific Extraction ---
    if upload_type == 'po':
        if "PO Number" in fields_to_extract_labels:
            # Look for "PO Number: 81100" or "PO Number 81100" (often near top or header)
            # Also "Purchase Order 81100" or "P.O. Number 81100"
            m = re.search(r"(?:P(?:urchase|\.O\.)\s*O(?:rder)?\s*No(?:Number|\.)?)\s*[:\-]?\s*([A-Z0-9\-]+)", text, re.IGNORECASE)
            if m: data["PO Number"] = m.group(1).strip()

    if "Vendor" in fields_to_extract_labels: # This is for Vendor ID like S101334
        m = re.search(r"\bVendor\s*[:\-]?\s*(S\d+)\b", text, re.IGNORECASE)
        if m: data["Vendor"] = m.group(1).strip()

    if "Phone" in fields_to_extract_labels:
        # Try to find phone specifically associated with the "Vendor" section
        # Look for "Vendor:" then some lines, then "Phone:"
        # This is a heuristic and depends on consistent document structure.
        vendor_block_text = ""
        vendor_header_match = re.search(r"Vendor\s*[:\-]\s*", text, re.IGNORECASE)
        if vendor_header_match:
            # Try to get text block after "Vendor:" up to next major section like "Ship To:" or end of typical vendor address block
            end_of_vendor_block_match = re.search(r"Ship To:|F\.O\.B|Terms", text[vendor_header_match.end():], re.IGNORECASE)
            if end_of_vendor_block_match:
                vendor_block_text = text[vendor_header_match.end() : vendor_header_match.end() + end_of_vendor_block_match.start()]
            else: # If no clear end, take a reasonable chunk
                vendor_block_text = text[vendor_header_match.end() : vendor_header_match.end() + 300] # Approx 5-6 lines

            phone_in_vendor_block = re.search(r"(?:Phone|Fax\s*[:\-]?\s*\S+\s*Phone)\s*[:\-]?\s*(\(?\d{3}\)?[\s\.\-]?\d{3}[\s\.\-]?\d{4})", vendor_block_text, re.IGNORECASE)
            if phone_in_vendor_block:
                data["Phone"] = phone_in_vendor_block.group(1).strip()
            else: # Fallback to general phone search if not found in vendor block
                m_phone_general = re.search(r"\bPhone\s*[:\-]?\s*(\(?\d{3}\)?[\s\.\-]?\d{3}[\s\.\-]?\d{4})", text, re.IGNORECASE)
                if m_phone_general:
                    # This might still get the buyer's phone if it's labeled "Phone" and vendor's isn't explicitly.
                    # We need to be careful not to overwrite if we already got something better.
                    if data["Phone"] is None: # Only if not already found in vendor block
                        data["Phone"] = m_phone_general.group(1).strip()
        else: # If "Vendor:" header isn't found, do a general phone search as a last resort
            m_phone_general_fallback = re.search(r"\bPhone\s*[:\-]?\s*(\(?\d{3}\)?[\s\.\-]?\d{3}[\s\.\-]?\d{4})", text, re.IGNORECASE)
            if m_phone_general_fallback:
                data["Phone"] = m_phone_general_fallback.group(1).strip()

        if "Total" in fields_to_extract_labels: # This is for Grand Total
            # Prioritize labels like "Grand Total", "TOTAL DUE", then "Total"
            # Look for the amount on the same line or subsequent lines if structure is predictable
            keywords_ordered = ["Grand Total", "TOTAL DUE", "Amount Due", "Total Balance", "Total Amount", "TOTAL"]
            found_total_val = None
            for kw in keywords_ordered:
                # Regex: Keyword, optional colon/hyphen, optional whitespace, then the currency amount
                m = re.search(r"\b" + re.escape(kw) + r"\b\s*[:\-]?\s*([\$€£]?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2}))", text, re.IGNORECASE | re.MULTILINE)
                if m: found_total_val = m.group(1).strip(); break
            if found_total_val: data["Total"] = found_total_val
            else: # Fallback: find last currency amount that might be a total
                 amounts = re.findall(r"([\$€£]?\s*\d{1,3}(?:,\d{3})*\.\d{2})\b", text)
                 if amounts: data["Total"] = amounts[-1].strip() # Take the last one found

        if "Order Date" in fields_to_extract_labels:
            m = re.search(r"Order Date\s*[:\-]?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})", text, re.IGNORECASE)
            if m: data["Order Date"] = m.group(1).strip()

    # --- Part Drawing (Quote PDF) Specific Extraction ---
    elif upload_type == 'part_drawing':
        # Header fields
        if "Quote ID" in fields_to_extract_labels:
            m = re.search(r"Quote ID:\s*([A-Z0-9]+)", text, re.IGNORECASE)
            if m: data["Quote ID"] = m.group(1).strip()
        if "Customer ID" in fields_to_extract_labels:
            m = re.search(r"Customer ID:\s*([A-Z0-9]+)", text, re.IGNORECASE)
            if m: data["Customer ID"] = m.group(1).strip()
        if "Quote Date" in fields_to_extract_labels:
            m = re.search(r"Quote Date:\s*(\d{1,2}/\d{1,2}/\d{2,4})", text, re.IGNORECASE)
            if m: data["Quote Date"] = m.group(1).strip()
        if "Expiration Date" in fields_to_extract_labels:
            m = re.search(r"Expiration Date:\s*(\d{1,2}/\d{1,2}/\d{2,4})", text, re.IGNORECASE)
            if m: data["Expiration Date"] = m.group(1).strip()

        # Line: "Quote ID Date Sales Contact Quote Terms" -> data line below it
        # Example data line: "16Q05495 8/12/2024 Kevin Braasch Net 30"
        header_line_pattern = r"Quote ID\s+Quote Date\s+Sales Contact\s+Quote Terms"
        data_line_pattern = r"([A-Z0-9]+)\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+([\w\s.]+?)\s+([\w\s\d]+)" # Grouped for each value
        
        header_match = re.search(header_line_pattern, text, re.IGNORECASE)
        if header_match:
            # Search for the data_line_pattern in the text immediately following the header
            search_text_after_header = text[header_match.end():]
            data_line_match = re.search(r"^\s*" + data_line_pattern, search_text_after_header, re.MULTILINE | re.IGNORECASE)
            if data_line_match:
                if "Quote ID" in fields_to_extract_labels and data["Quote ID"] is None: data["Quote ID"] = data_line_match.group(1).strip()
                if "Quote Date" in fields_to_extract_labels and data["Quote Date"] is None: data["Quote Date"] = data_line_match.group(2).strip()
                if "Sales Contact" in fields_to_extract_labels: data["Sales Contact"] = data_line_match.group(3).strip()
                if "Quote Terms" in fields_to_extract_labels: data["Quote Terms"] = data_line_match.group(4).strip()

        # Table data: Part Number, Description, Revision
        # Sample line: "1402.00-1197 Barrel CONTACT TERMINAL B"
        # We need to find the line containing the primary part number, e.g., 1402.00-1197 from your sample.
        # This assumes there's one main part number per quote for this extraction logic.
        # A more robust solution would iterate through all table lines.
        
        # Heuristic: find the line containing the specific part number if it's a known format or common one
        # For the sample "1402.00-1197"
        # Regex to find the entire line: PartNumber ProductCode(optional) Description Revision
        target_line_match = re.search(r"^(1402\.00-1197)\s+(?:([\w\s]+?)\s+)?(CONTACT\s+TERMINAL)\s+([A-Z0-9])\s*$", text, re.MULTILINE | re.IGNORECASE)
        if target_line_match:
            if "Part Number" in fields_to_extract_labels:
                data["Part Number"] = target_line_match.group(1).strip()
            
            product_code = target_line_match.group(2) # e.g., "Barrel"
            main_description = target_line_match.group(3).strip() # "CONTACT TERMINAL"
            
            if "Description" in fields_to_extract_labels:
                if product_code and product_code.strip().lower() != main_description.lower():
                    data["Description"] = f"{product_code.strip()} {main_description}"
                else:
                    data["Description"] = main_description
            
            if "Revision" in fields_to_extract_labels:
                data["Revision"] = target_line_match.group(4).strip()
        else: # Fallback for table data if the specific line isn't found as above
             if "Part Number" in fields_to_extract_labels and data["Part Number"] is None:
                 m = re.search(r"\b(\d{4}\.\d{2}-\d{4})\b", text) # General part number format
                 if m: data["Part Number"] = m.group(1)
             if "Description" in fields_to_extract_labels and data["Description"] is None:
                  # Try finding description near a known part number if it was found
                 if data.get("Part Number"):
                     m = re.search(re.escape(data["Part Number"]) + r"\s+[^\n]*?\s+([A-Z\s]+TERMINAL|[A-Z\s]+CONTACT)\b", text, re.IGNORECASE)
                     if m : data["Description"] = m.group(1).strip()
             if "Revision" in fields_to_extract_labels and data["Revision"] is None:
                 m = re.search(r"\b(?:Rev|Revision)\s*[:\-]?\s*([A-Z0-9])\b", text, re.IGNORECASE)
                 if m: data["Revision"] = m.group(1)

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
                    fields_for_comparison_labels = PO_KEY_COMPARISON_FIELDS
                    db_lookup_key_label = "PO Number"
                elif upload_type == 'ats':
                    fields_for_comparison_labels = ATS_KEY_COMPARISON_FIELDS
                    db_lookup_key_label = "Sr no."
                elif upload_type == 'part_drawing':
                    fields_for_comparison_labels = PART_DRAWING_KEY_COMPARISON_FIELDS
                    db_lookup_key_label = "Quote ID"

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
                            structured_data = extract_structured_data(extracted_text, allowed_field_labels, upload_type=upload_type)
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

