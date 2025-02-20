import streamlit as st
import io
import re
import uuid
import hashlib
from datetime import datetime
import pdfplumber
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- Google Sheets Setup ---
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)

# Use your spreadsheet ID (the sheet must be shared with your service account)
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
spreadsheet = gc.open_by_key(SPREADSHEET_ID)

def get_or_create_worksheet(spreadsheet, title):
    """Return a worksheet with the given title; create it if it doesn't exist."""
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows="1000", cols="50")
    return ws

# Get (or create) the two worksheets:
bill_ws = get_or_create_worksheet(spreadsheet, "Bill_Data")
mapping_ws = get_or_create_worksheet(spreadsheet, "Mapping")

# --- Helper: Standardize Charge Type Names ---
def standardize_charge_type(charge_type):
    return re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type, flags=re.IGNORECASE).strip()

# --- PDF Extraction Functions ---
def extract_charges_from_pdf(file_bytes):
    rows = []
    regex_pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?"
        r"\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    with pdfplumber.open(file_bytes) as pdf:
        for page_index in [1, 2]:
            if page_index < len(pdf.pages):
                text = pdf.pages[page_index].extract_text() or ""
                lines = text.splitlines()
                header_found = False
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if not header_found and "Type of charge" in line and "Amount($" in line:
                        header_found = True
                        continue
                    if header_found:
                        match = re.match(regex_pattern, line)
                        if match:
                            desc = match.group("desc").strip()
                            rate_val = match.group("rate") or ""
                            amount_str = match.group("amount").replace(",", "").replace("−", "")
                            try:
                                amount = float(amount_str)
                            except ValueError:
                                continue
                            if any(keyword in desc.lower() for keyword in ["page", "year", "meter", "temp", "date"]):
                                continue
                            rows.append({
                                "Charge_Type": desc,
                                "Rate": rate_val,
                                "Amount": amount
                            })
    return rows

def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    # Define date patterns (full and abbreviated month names)
    date_patterns = [
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$",
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}$"
    ]
    
    for i, line in enumerate(lines):
        for pattern in date_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                try:
                    try:
                        parsed_date = datetime.strptime(line, "%B %Y")
                    except Exception:
                        parsed_date = datetime.strptime(line, "%b %Y")
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except Exception:
                    metadata["Bill_Month_Year"] = line
                # Use the next non-empty line as the person's name (if available)
                if i + 1 < len(lines):
                    metadata["Person"] = lines[i+1]
                break
        if metadata["Bill_Month_Year"]:
            break
    # Do not prompt the user; if not found, leave as empty string.
    return metadata

# --- Mapping Persistence in Google Sheets ---
def load_customer_ids(ws):
    """Load the Person-to-User_ID mapping from the Mapping worksheet."""
    try:
        data = ws.get_all_records()
        if data:
            return {row["Person"]: row["User_ID"] for row in data if row.get("Person") and row.get("User_ID")}
    except Exception as e:
        st.error("Error loading customer IDs: " + str(e))
    return {}

def save_customer_ids(ws, mapping):
    """Save the Person-to-User_ID mapping to the Mapping worksheet.
       Overwrites the entire worksheet.
    """
    # Prepare header and data rows.
    header = ["Person", "User_ID"]
    rows = [header]
    for person, user_id in mapping.items():
        rows.append([person, user_id])
    ws.clear()
    for row in rows:
        ws.append_row(row)

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    
    consolidated = {}
    for c in charges:
        ct = standardize_charge_type(c["Charge_Type"])
        amt = c["Amount"]
        rate_val = c["Rate"]
        if ct in consolidated:
            consolidated[ct]["Amount"] += amt
            if not consolidated[ct]["Rate"] and rate_val:
                consolidated[ct]["Rate"] = rate_val
        else:
            consolidated[ct] = {"Amount": amt, "Rate": rate_val}
    
    # Load or initialize customer_ids mapping from Mapping worksheet.
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = load_customer_ids(mapping_ws)
    
    person = metadata.get("Person", "")
    if person in st.session_state.customer_ids:
        user_id = st.session_state.customer_ids[person]
    else:
        user_id = str(uuid.uuid4())
        st.session_state.customer_ids[person] = user_id
        save_customer_ids(mapping_ws, st.session_state.customer_ids)
    
    # Check for duplicate bill using Bill_Hash in Bill_Data worksheet.
    existing = bill_ws.get_all_records()
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())
    
    output_row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash
    }
    for ct, data in consolidated.items():
        if data["Amount"] != 0:
            output_row[f"{ct} Amount"] = data["Amount"]
        if data["Rate"]:
            output_row[f"{ct} Rate"] = data["Rate"]
    return output_row

def append_row_to_sheet(ws, row_dict):
    existing = ws.get_all_records()
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = []
    for key in row_dict.keys():
        if key not in headers:
            headers.append(key)
    if not existing:
        ws.append_row(headers)
    row_values = [str(row_dict.get(h, "")) for h in headers]
    ws.append_row(row_values)

# --- Streamlit App Interface ---
st.title("Delmarva BillWatch")
st.write("Upload your PDF bill. Your deidentified utility charge information will be stored in Google Sheets.")

st.markdown("""
**Privacy Disclaimer:**  
By submitting your form, you agree that your response may be used to support an investigation into billing issues with Delmarva Power.  
Your information will not be shared publicly or sold.  
This form is for informational and organizational purposes only and does not constitute legal representation.
""")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf", accept_multiple_files=False)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    file_io = io.BytesIO(file_bytes)
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    existing = bill_ws.get_all_records()
    duplicate = any(r.get("Bill_Hash") == bill_hash for r in existing)
    if duplicate:
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(file_io)
        append_row_to_sheet(bill_ws, output_row)
        st.success("Thank you for your contribution!")
