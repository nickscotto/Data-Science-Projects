import streamlit as st
import io
import os
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
# Authenticate using credentials stored in Streamlit Secrets
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)

# Set the spreadsheet name as per your sheet.
SHEET_NAME = "Bill_Data"
try:
    spreadsheet = gc.open(SHEET_NAME)
except gspread.SpreadsheetNotFound:
    spreadsheet = gc.create(SHEET_NAME)
    # Share the sheet with your service account email.
    spreadsheet.share(st.secrets["gcp_service_account"]["client_email"], perm_type="user", role="writer")
worksheet = spreadsheet.sheet1

# --- Helper: Standardize charge type names ---
def standardize_charge_type(charge_type):
    """
    Remove numeric kWh values from the charge type string.
    E.g., "Distribution Charge Last 2190 kWh" becomes "Distribution Charge Last kWh".
    """
    standardized = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type, flags=re.IGNORECASE)
    return standardized.strip()

# --- PDF Extraction Functions ---
def extract_charges_from_pdf(file_bytes):
    """
    Extract charge rows from pages 2 and 3.
    Returns a list of dicts with keys: Charge_Type, Rate, Amount.
    """
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
    """
    Extract metadata from page 1.
    Finds a line like "January 2025" (converted to "MM-YYYY") and assumes the next non-empty line is the person's name.
    Returns a dict with:
      - "Bill_Month_Year": formatted as "MM-YYYY"
      - "Person": the extracted name.
    """
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = text.splitlines()
        month_regex = r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$"
        for i, line in enumerate(lines):
            candidate = line.strip()
            if re.match(month_regex, candidate, re.IGNORECASE):
                try:
                    parsed_date = datetime.strptime(candidate, "%B %Y")
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except Exception:
                    metadata["Bill_Month_Year"] = candidate
                for j in range(i+1, len(lines)):
                    candidate2 = lines[j].strip()
                    if candidate2:
                        metadata["Person"] = candidate2
                        break
                break
    return metadata

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    """
    Process a PDF bill (file-like object) and return a row dictionary.
    The row includes:
      - User_ID (unique per Person)
      - Bill_ID (unique per bill, determined by bill hash)
      - Bill_Month_Year
      - Bill_Hash (for duplicate detection)
      - Standardized charge columns (with Amount and Rate)
    (The Person field is used internally for mapping but is not output.)
    """
    # Compute bill hash.
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    
    # Consolidate charges.
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
    
    # Use Person for mapping User_ID.
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    person = metadata.get("Person", "")
    if person in st.session_state.customer_ids:
        user_id = st.session_state.customer_ids[person]
    else:
        user_id = str(uuid.uuid4())
        st.session_state.customer_ids[person] = user_id
    
    # Check for duplicate bill using Bill_Hash.
    existing = worksheet.get_all_records()
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

def append_row_to_sheet(row_dict):
    # Get current records and header.
    existing = worksheet.get_all_records()
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = []
    # Add any new keys to the header.
    for key in row_dict.keys():
        if key not in headers:
            headers.append(key)
    # If sheet is empty, add headers as first row.
    if not existing:
        worksheet.append_row(headers)
    # Order row values according to headers.
    row_values = [str(row_dict.get(h, "")) for h in headers]
    worksheet.append_row(row_values)

# --- Streamlit App Interface ---
st.title("Delmarva BillWatch")
st.write("Upload your PDF bill. Your deidentified utility charge information will be stored in Google Sheets.")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf", accept_multiple_files=False)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    file_io = io.BytesIO(file_bytes)
    # Duplicate detection.
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    existing = worksheet.get_all_records()
    duplicate = any(r.get("Bill_Hash") == bill_hash for r in existing)
    if duplicate:
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(file_io)
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
