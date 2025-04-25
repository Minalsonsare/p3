from flask import Flask, render_template, request, redirect, url_for, session, flash
from pdfminer.high_level import extract_text as pdf_extract_text
from docx import Document as DocxDocument
import os
import requests
import re
import secrets # Import secrets for generating a secure key

# Ensure temp directory exists locally when app starts
os.makedirs(os.path.join(os.path.dirname(__file__), 'temp'), exist_ok=True)

app = Flask(__name__)
# Set a secure secret key for session management
# In production, use a value from environment variables or a secret file
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16)) # Use env var first, fallback to random for local test


# OCR Space API Configuration (Remember to set your API key as environment variable in production!)
OCR_SPACE_API_URL = "https://api.ocr.space/parse/image"
OCR_SPACE_API_KEY = os.environ.get('OCR_SPACE_API_KEY')
if not OCR_SPACE_API_KEY or OCR_SPACE_API_KEY == "K87955728688957":
    # Replace with your actual key for local testing if not using env vars
    # Or better, load from a local .env file using python-dotenv
    OCR_SPACE_API_KEY = "K87955728688957" # Use a placeholder you KNOW isn't your real key in code
    print("Warning: OCR Space API key not found in environment variables or is the default placeholder.")
    if app.debug: # Only print detailed warning in debug mode
        print("Please set the OCR_SPACE_API_KEY environment variable.")


# --- Dummy Users (Replace with a real database/auth system in production) ---
DUMMY_USERS = {
    "sushil": "Sushil@ap1",
    "admin": "admin@a123",
    "user": "pass@a123"
}

# ---------------------------------------------------------------------------


# --- Context Processor (Keep as is) ---
@app.context_processor
def inject_utilities():
    """Injects utility functions into the template context."""
    return dict(get_database_data=get_database_data)
# ----------------------------------

# --- OCR and Data Extraction Functions (Keep as is) ---
def ocr_image_via_api(image_path):
    """Performs OCR on an image using OCR Space API."""
    if OCR_SPACE_API_KEY == "YOUR_OCR_SPACE_API_KEY_HERE":
         return "Error: OCR Space API Key not configured."
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()

        payload = {
            'apikey': OCR_SPACE_API_KEY,
            'language': 'eng'
        }
        files = {'image': ('image.png', image_data)}

        response = requests.post(OCR_SPACE_API_URL, files=files, data=payload)
        response.raise_for_status()
        result = response.json()

        if result and not result.get('IsErroredOnProcessing'):
            text = result.get('ParsedResults')[0].get('ParsedText') if result.get('ParsedResults') else "No text found in image."
            return text.strip()
        else:
            error_message = result.get('ErrorMessage') or "Unknown error from OCR Space API."
            return f"OCR Space API Error: {error_message}"

    except requests.exceptions.RequestException as e:
        return f"Error connecting to OCR Space API: {e}"
    except Exception as e:
        return f"Error during OCR processing: {e}"


def extract_text_from_pdf(file_path):
    """Extracts text directly from a PDF file using pdfminer.six."""
    try:
        text = pdf_extract_text(file_path)
        return text.strip()
    except Exception as e:
        return f"Error extracting text from PDF: {e}"


def extract_text_from_docx(file_path):
    """Extracts text from a DOCX file using python-docx."""
    try:
        doc = DocxDocument(file_path)
        full_text = []
        for paragraph in doc.paragraphs:
            full_text.append(paragraph.text)
        return '\n'.join(full_text).strip() # Join paragraphs with newlines
    except Exception as e:
        return f"Error extracting text from DOCX: {e}"


def extract_text_from_file(file_path, filename):
    """Detects file type and calls appropriate extraction method."""
    file_extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if file_extension in ['png', 'jpg', 'jpeg', 'bmp', 'gif', 'tiff']: # Image types
        return ocr_image_via_api(file_path)
    elif file_extension == 'pdf':
        return extract_text_from_pdf(file_path)
    elif file_extension == 'docx':
        return extract_text_from_docx(file_path)
    else:
        return "Unsupported file format."

# --- Data Extraction and Comparison Functions (Keep as is) ---

def extract_structured_data(text):
    """Extracts specific structured data fields from text using regex."""
    data = {}
    # Define the fields we want to extract
    field_names = ["Sr no.", "Name", "City", "Age", "Country", "Address"]
    # Initialize all expected fields with None
    for field in field_names:
        data[field] = None

    lines = text.strip().split('\n')

    for field in field_names:
        pattern = re.compile(r"^\s*" + re.escape(field) + r"\s*:\s*(.*)", re.IGNORECASE)
        for line in lines:
            match = pattern.match(line.strip())
            if match:
                value = match.group(1).strip()
                data[field] = value
                break # Found this field, move to the next field_name
    return data

# Dummy database (Keep as is)
dummy_database = {
    "S001": { "Sr no.": "S001", "Name": "Hemanshu Kasar", "City": "Nagpur", "Age": "23", "Country": "India", "Address": "7, gurudeo nagar" },
    "S002": { "Sr no.": "S002", "Name": "John Doe", "City": "New York", "Age": "30", "Country": "USA", "Address": "123 Main St" },
    "S003": { "Sr no.": "S003", "Name": "Sarah Johnson", "City": "London", "Age": "27", "Country": "UK", "Address": "45 Oxford Street" },
    "S004": { "Sr no.": "S004", "Name": "Raj Patel", "City": "Mumbai", "Age": "32", "Country": "India", "Address": "201, Sea View Apartments" },
    "S005": { "Sr no.": "S005", "Name": "Maria Garcia", "City": "Barcelona", "Age": "29", "Country": "Spain", "Address": "Carrer de Mallorca, 15" },
    "S006": { "Sr no.": "S006", "Name": "Akira Tanaka", "City": "Tokyo", "Age": "35", "Country": "Japan", "Address": "2-1-3 Shibuya" },
    "S007": { "Sr no.": "S007", "Name": "Chen Wei", "City": "Shanghai", "Age": "26", "Country": "China", "Address": "88 Nanjing Road" },
    "S008": { "Sr no.": "S008", "Name": "Lucas Silva", "City": "São Paulo", "Age": "31", "Country": "Brazil", "Address": "Rua Augusta, 1200" },
    "S009": { "Sr no.": "S009", "Name": "Olivia Miller", "City": "Sydney", "Age": "28", "Country": "Australia", "Address": "42 Bondi Beach Road" },
    "S010": { "Sr no.": "S010", "Name": "Ahmed Hassan", "City": "Cairo", "Age": "33", "Country": "Egypt", "Address": "17 Al Tahrir Square" }
}


def get_database_data(sr_no):
    """Fetches data from the dummy database based on Sr no."""
    return dummy_database.get(sr_no, None)

def compare_data(extracted_data, db_data):
    """Compares extracted data with database data and calculates accuracy."""
    if not db_data:
        return 0, {}, "Sr no. not found in database."

    matched_fields = 0
    mismatched_fields = {}
    total_fields = len(db_data)

    for key, db_value in db_data.items():
        extracted_value = extracted_data.get(key)

        if extracted_value is not None and db_value is not None and str(extracted_value).lower() == str(db_value).lower():
             matched_fields += 1
        elif extracted_value != db_value:
             mismatched_fields[key] = {"db_value": db_value, "extracted_value": extracted_value}

    accuracy = (matched_fields / total_fields) * 100 if total_fields > 0 else 0
    return accuracy, mismatched_fields, None


# --- Route for Landing Page ---
@app.route('/', methods=['GET'])
def landing_page():
    """Renders the main landing page (Template1.html)."""
    # If user is already logged in, redirect them to the dashboard
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('app_dashboard'))
    return render_template('Template1.html')


# --- Route for Login Page ---
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """Renders the login page or handles login submission."""
    # If user is already logged in, redirect them to the dashboard
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('app_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username in DUMMY_USERS and DUMMY_USERS[username] == password:
            session['logged_in'] = True
            # Optionally store username in session: session['username'] = username
            flash('Login successful!', 'success') # Use flash messages for feedback
            return redirect(url_for('app_dashboard'))
        else:
            flash('Invalid User ID or Password', 'danger') # Use flash messages for feedback
            # Stay on the login page, flash message will be displayed by template
            return render_template('login.html') # Re-render login page on failure

    # Render login form for GET requests
    return render_template('login.html')


# --- Route for Dashboard (Requires Login) ---
@app.route('/app', methods=['GET', 'POST'])
def app_dashboard():
    """Handles file uploads and renders the OCR/comparison results (app_dashboard.html)."""
    # Protect this route - redirect to login if not logged in
    if 'logged_in' not in session or not session['logged_in']:
        flash('Please login to access the dashboard.', 'warning')
        return redirect(url_for('login_page'))

    results = {}

    # Initialize active tab for template. Default to 'po-verification'
    active_tab = request.form.get('active_tab', 'po-verification')


    if request.method == 'POST':
        # --- File Upload and Processing Logic (Only if submitting from PO Verification tab) ---
        # You might want a hidden input in the form to indicate which tab the submission came from
        # For now, we assume file uploads only happen in PO Verification tab
        if 'image' in request.files:
             image_files = request.files.getlist('image')

             if not image_files or all(f.filename == '' for f in image_files):
                  results["Overall Error"] = {"error": "No files selected."}
             else:
                for image_file in image_files:
                    filename = image_file.filename
                    if filename == '':
                         results[f"Skipped empty file input ({len(results) + 1})"] = {"error": "Skipped empty file input."}
                         continue

                    if image_file:
                        temp_dir = os.path.join(app.root_path, 'temp')
                        temp_filename = f"{os.getpid()}_{os.urandom(4).hex()}_{filename}"
                        temp_file_path = os.path.join(temp_dir, temp_filename)

                        try:
                            image_file.save(temp_file_path)
                            extracted_text = extract_text_from_file(temp_file_path, filename)

                            structured_data = {}
                            accuracy = None
                            mismatched_fields = {}
                            comparison_error = None

                            if extracted_text and not extracted_text.startswith("Error"):
                                file_extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
                                if file_extension in ['docx', 'pdf', 'png', 'jpg', 'jpeg', 'bmp', 'gif', 'tiff']:
                                     structured_data = extract_structured_data(extracted_text)
                                     sr_no_value = structured_data.get("Sr no.")
                                     if sr_no_value:
                                         db_data = get_database_data(sr_no_value)
                                         accuracy, mismatched_fields, comparison_error = compare_data(structured_data, db_data)
                                     else:
                                         comparison_error = "Sr no. not found in extracted data, cannot compare."

                            results[filename] = {
                                "extracted_text": extracted_text,
                                "structured_data": structured_data,
                                "accuracy": accuracy,
                                "mismatched_fields": mismatched_fields,
                                "comparison_error": comparison_error
                            }

                        except Exception as e:
                             results[filename] = {"error": f"Processing failed for {filename}: {e}"}

                        finally:
                             if os.path.exists(temp_file_path):
                                 os.remove(temp_file_path)

        # After a POST request (file upload), ensure we are back on the PO Verification tab
        active_tab = 'po-verification'


    # For GET requests or after POST processing, render the dashboard template
    # Pass results and active_tab to the template
    return render_template('app_dashboard.html', results=results, active_tab=active_tab)


# --- Route for Logout ---
@app.route('/logout')
def logout():
    """Logs out the user by clearing the session."""
    session.pop('logged_in', None) # Remove the logged_in key from session
    # Optionally remove username: session.pop('username', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('landing_page'))


if __name__ == '__main__':
    # Ensure API key and Secret Key are set for local testing if not using env vars
    if OCR_SPACE_API_KEY == "YOUR_OCR_SPACE_API_KEY_HERE":
        print("Please replace 'YOUR_OCR_SPACE_API_KEY_HERE' with your actual key or set the environment variable.")
    if os.environ.get('SECRET_KEY') is None and app.secret_key == secrets.token_hex(16):
         print("Warning: Using a temporary random SECRET_KEY. Set the SECRET_KEY environment variable for production.")

    app.run(debug=True)