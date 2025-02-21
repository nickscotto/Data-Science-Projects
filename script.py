import streamlit as st
import io
import re
import uuid
import hashlib
from datetime import datetime
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials

# --- Google Sheets Setup ---
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)

SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
worksheet = spreadsheet.sheet1

# --- Helper: Standardize Charge Type Names ---
def standardize_charge_type(charge_type):
    charge_type = re.sub(r'\b(First|Last|Next)\b', '', charge_type).strip()
    charge_type = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type).strip()
    return charge_type

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
        lines = text.splitlines()
        
        # Keywords to locate the bill date
        date_keywords = ["bill date", "statement date", "date of bill"]
        date_patterns = [
            r"\d{1,2}/\d{1,2}/\d{4}",  # MM/DD/YYYY
            r"\w+ \d{1,2}, \d{4}",     # January 1, 2024
            r"\w+ \d{4}"               # January 2024
        ]
        
        for line in lines:
            line = line.strip()
            if any(keyword.lower() in line.lower() for keyword in date_keywords):
                for pattern in date_patterns:
                    match = re.search(pattern, line)
                    if match:
                        date_str = match.group()
                        try:
                            parsed_date = datetime.strptime(date_str, "%m/%d/%Y")
                            metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                        except ValueError:
                            try:
                                parsed_date = datetime.strptime(date_str, "%B %d, %Y")
                                metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                            except ValueError:
                                try:
                                    parsed_date = datetime.strptime(date_str, "%B %Y")
                                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                                except ValueError:
                                    pass
                        if metadata["Bill_Month_Year"]:
                            break
            if metadata["Bill_Month_Year"]:
                break
        
        # Extract person's name (next non-empty line after date, if found)
        if metadata["Bill_Month_Year"]:
            for i, line in enumerate(lines):
                if metadata["Bill_Month_Year"] in line:
                    for j in range(i+1, len(lines)):
                        candidate = lines[j].strip()
                        if candidate:
                            metadata["Person"] = candidate
                            break
                    break
        else:
            # Fallback for person's name if date not found
            for line in lines:
                if "Account Holder" in line or "Customer Name" in line:
                    metadata["Person"] = line.split(":")[-1].strip()
                    break
    return metadata

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    
    # Consolidate charges
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
    
    # Normalize person's name for User ID consistency
    person = metadata.get("Person", "").strip().lower()
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    if person in st.session_state.customer_ids:
        user_id = st.session_state.customer_ids[person]
    else:
        user_id = str(uuid.uuid4())
        st.session_state.customer_ids[person] = user_id
    
    # Check for existing bill and assign Bill_ID
    existing = worksheet.get_all_records()
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())
    
    # Build output row
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

# --- Updated Sheet Append Function ---
def append_row_to_sheet(row_dict):
    metadata_columns = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash"]
    current_data = worksheet.get_all_values()
    if current_data:
        headers = current_data[0]
        existing_rows = current_data[1:]
    else:
        headers = []
        existing_rows = []

    charge_columns = [col for col in row_dict.keys() if col not in metadata_columns]
    existing_charge_columns = [col for col in headers if col not in metadata_columns]
    new_charge_columns = list(set(charge_columns) - set(existing_charge_columns))
    all_charge_columns = sorted(existing_charge_columns + new_charge_columns)
    full_headers = metadata_columns + all_charge_columns
    
    if full_headers != headers:
        worksheet.update("A1", [full_headers])
        if existing_rows:
            for i, row in enumerate(existing_rows, start=2):
                row_dict = dict(zip(headers, row))
                padded_row = [str(row_dict.get(header, "")) for header in full_headers]
                worksheet.update(f"A{i}", [padded_row])
    
    row_values = [str(row_dict.get(header, "")) for header in full_headers]
    worksheet.append_row(row_values)

# --- Streamlit App Interface ---
st.title("Delmarva BillWatch")
st.write("Upload your PDF bill. Your deidentified utility charge information will be stored in Google Sheets.")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf", accept_multiple_files=False)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    file_io = io.BytesIO(file_bytes)
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    existing = worksheet.get_all_records()
    duplicate = any(r.get("Bill_Hash") == bill_hash for r in existing)
    if duplicate:
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(file_io)
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
