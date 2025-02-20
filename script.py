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
    """
    Standardizes charge type names by removing distinctions like 'First', 'Last', 'Next',
    and numbers before 'kWh', ensuring similar charges are grouped together.
    """
    charge_type = re.sub(r'\b(First|Last|Next)\b', '', charge_type).strip()
    charge_type = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type).strip()
    return charge_type

# --- PDF Extraction Functions ---
def extract_charges_from_pdf(file_bytes):
    """Extracts charge details from the PDF bill."""
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
    """Extracts metadata like bill month and person from the PDF bill."""
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
    """Processes the PDF bill, consolidating charges and preparing row data."""
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    
    # Consolidate charges with standardized types
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
    
    # Assign or retrieve User_ID
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    person = metadata.get("Person", "")
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
    
    # Build output row with metadata first
    output_row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash
    }
    # Add consolidated charge data
    for ct, data in consolidated.items():
        if data["Amount"] != 0:
            output_row[f"{ct} Amount"] = data["Amount"]
        if data["Rate"]:
            output_row[f"{ct} Rate"] = data["Rate"]
    return output_row

# --- Updated Sheet Append Function ---
def append_row_to_sheet(row_dict):
    """Appends a row to the Google Sheet with a consistent column order."""
    # Define fixed metadata columns
    metadata_columns = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash"]
    
    # Get current sheet data
    current_data = worksheet.get_all_values()
    if current_data:
        headers = current_data[0]  # First row is headers
        existing_rows = current_data[1:]  # Remaining rows are data
    else:
        headers = []
        existing_rows = []

    # Collect and sort charge-related columns
    charge_columns = [col for col in row_dict.keys() if col not in metadata_columns]
    existing_charge_columns = [col for col in headers if col not in metadata_columns]
    new_charge_columns = list(set(charge_columns) - set(existing_charge_columns))
    all_charge_columns = sorted(existing_charge_columns + new_charge_columns)
    
    # Define full headers: metadata followed by sorted charge columns
    full_headers = metadata_columns + all_charge_columns
    
    # Update sheet headers if changed
    if full_headers != headers:
        worksheet.update("A1", [full_headers])
        
        # Pad existing rows for new columns
        if existing_rows:
            for i, row in enumerate(existing_rows, start=2):
                row_dict = dict(zip(headers, row))
                padded_row = [str(row_dict.get(header, "")) for header in full_headers]
                worksheet.update(f"A{i}", [padded_row])
    
    # Prepare and append the new row
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
