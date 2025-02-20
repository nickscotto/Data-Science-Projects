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
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)

# Use the spreadsheet ID to open your Google Sheet.
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
try:
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
except Exception as e:
    st.error("Error opening spreadsheet: " + str(e))
    st.stop()
worksheet = spreadsheet.sheet1

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
    """
    Extract metadata (Bill_Month_Year and Person) from page 1.
    It scans all non-empty lines for a date in the form "January 2024" (or abbreviated "Jan 2024")
    and then among subsequent lines looks for a candidate that appears to be a name.
    If no candidate is found, it leaves the fields empty.
    """
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    # Define date patterns.
    date_patterns = [
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$",
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}$"
    ]
    
    date_index = None
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
                date_index = i
                break
        if metadata["Bill_Month_Year"]:
            break

    # Look for a candidate name in lines after the date.
    if date_index is not None:
        for line in lines[date_index+1:]:
            # Skip lines that contain typical billing phrases.
            if "for the period" in line.lower():
                continue
            # Heuristic: if the line contains at least one space and is mostly uppercase, assume it's a name.
            if " " in line:
                letters = [ch for ch in line if ch.isalpha()]
                if letters and all(ch.isupper() for ch in letters):
                    metadata["Person"] = line
                    break
    # If no candidate found, leave Person empty.
    return metadata

# --- Generate a User_ID Without Storing the Actual Name ---
def get_user_id(person):
    """Compute a deterministic User_ID by hashing the person's name (if available)."""
    if person:
        return hashlib.sha256(person.encode('utf-8')).hexdigest()
    else:
        return str(uuid.uuid4())

# --- Safe function to get all records from Google Sheets ---
def get_all_records_safe(ws):
    try:
        return ws.get_all_records(expected_headers=["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash"])
    except Exception as e:
        st.error("Error fetching records: " + str(e))
        data = ws.get_all_values()
        if data and len(data) > 1:
            headers = data[0]
            return [dict(zip(headers, row)) for row in data[1:]]
        return []

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
    
    # Generate User_ID using a hash of the person's name (without storing the name)
    person = metadata.get("Person", "")
    user_id = get_user_id(person)
    
    existing = get_all_records_safe(worksheet)
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
    existing = get_all_records_safe(worksheet)
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = []
    for key in row_dict.keys():
        if key not in headers:
            headers.append(key)
    if not existing:
        worksheet.append_row(headers)
    row_values = [str(row_dict.get(h, "")) for h in headers]
    worksheet.append_row(row_values)

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
    existing = get_all_records_safe(worksheet)
    duplicate = any(r.get("Bill_Hash") == bill_hash for r in existing)
    if duplicate:
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(file_io)
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
