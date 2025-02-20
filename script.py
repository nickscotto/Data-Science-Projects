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
    st.error("Error accessing the Google Sheet: " + str(e))
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
    Attempt to extract metadata (bill month-year and person’s name) from page 1.
    If not found, prompt the user to enter the missing information.
    """
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        # Get non-empty lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    # Try to find a date in the format "January 2024"
    month_regex = r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$"
    found = False
    for i, line in enumerate(lines):
        if re.match(month_regex, line, re.IGNORECASE):
            try:
                parsed_date = datetime.strptime(line, "%B %Y")
                metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
            except Exception:
                metadata["Bill_Month_Year"] = line
            # Assume the next non-empty line is the person's name
            if i + 1 < len(lines):
                metadata["Person"] = lines[i + 1]
            found = True
            break
    
    # If not found, prompt the user
    if not metadata["Bill_Month_Year"]:
        metadata["Bill_Month_Year"] = st.text_input("Enter the bill month and year (MM-YYYY):")
    if not metadata["Person"]:
        metadata["Person"] = st.text_input("Enter your name:")
    
    return metadata

# --- Mapping Persistence Functions ---
# We store a mapping from Person to User_ID in session state.
def load_customer_ids(mapping_path):
    if os.path.exists(mapping_path):
        df_map = pd.read_csv(mapping_path)
        return dict(zip(df_map["Person"], df_map["User_ID"]))
    else:
        return {}

def save_customer_ids(mapping, mapping_path):
    df_map = pd.DataFrame(mapping.items(), columns=["Person", "User_ID"])
    os.makedirs(os.path.dirname(mapping_path) or ".", exist_ok=True)
    df_map.to_csv(mapping_path, index=False)

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
    
    # Load or initialize customer_ids mapping.
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = load_customer_ids(MAPPING_PATH)
    
    person = metadata.get("Person", "")
    if person in st.session_state.customer_ids:
        user_id = st.session_state.customer_ids[person]
    else:
        user_id = str(uuid.uuid4())
        st.session_state.customer_ids[person] = user_id
        save_customer_ids(st.session_state.customer_ids, MAPPING_PATH)
    
    # Check for duplicate bill in Google Sheets.
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
    existing = worksheet.get_all_records()
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

# Privacy Disclaimer
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
    # Duplicate detection via bill hash.
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    existing = worksheet.get_all_records()
    duplicate = any(r.get("Bill_Hash") == bill_hash for r in existing)
    if duplicate:
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(file_io)
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
