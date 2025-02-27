import streamlit as st
import io
import re
import uuid
import hashlib
from datetime import datetime
from dateutil.parser import parse as date_parse
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials

# --- Google Sheets Setup ---
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
gc = gspread.authorize(creds)

SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
worksheet = spreadsheet.sheet1

# ------------------------------------------------------------------------
# 1) HELPER: Extract Table from a Page Using pdfplumber's "lines" Strategy
# ------------------------------------------------------------------------
def extract_bill_table(pdf_page):
    """
    Attempt to extract a single table from the given pdfplumber page
    using a lines-based strategy. The result is a list of rows, where
    each row is a list of cell values.
    """
    # You can tweak these settings to match your PDF’s layout
    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "intersection_x_tolerance": 5,
        "intersection_y_tolerance": 5,
    }
    table = pdf_page.extract_table(table_settings)
    return table  # list of lists (rows)

# ------------------------------------------------------------------------
# 2) HELPER: Parse the Extracted Table into a List of Dicts
# ------------------------------------------------------------------------
def parse_bill_table(table_data):
    """
    Convert the raw table rows into a list of dicts:
      [ { "Charge_Type": "...", "Calculation": "...", "Amount": 0.0 }, ... ]
    Expects the table to have columns roughly like:
      [ "Type of charge", "How we calculate this charge", "Amount($)" ]
    """
    parsed_rows = []
    if not table_data:
        return parsed_rows

    # Identify if the first row is a header row
    # (We check if it includes strings like 'Type of charge' and 'Amount')
    first_row = [cell.lower().strip() if cell else "" for cell in table_data[0]]
    is_header = (
        len(first_row) >= 3
        and "type of charge" in first_row[0]
        and "amount" in first_row[-1]
    )

    start_idx = 1 if is_header else 0
    for row in table_data[start_idx:]:
        # Ensure row has at least 3 columns (Charge, Calculation, Amount)
        # If columns are missing, fill them with ""
        while len(row) < 3:
            row.append("")

        charge_type = (row[0] or "").strip()
        calculation = (row[1] or "").strip()
        amount_str = (row[2] or "").replace(",", "").strip()

        # Attempt to convert the amount to float
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0.0

        # Skip empty lines or obviously invalid data
        if not charge_type and not calculation and amount == 0.0:
            continue

        parsed_rows.append({
            "Charge_Type": charge_type,
            "Calculation": calculation,
            "Amount": amount
        })

    return parsed_rows

# ------------------------------------------------------------------------
# 3) HELPER: Standardize Charge Type Names
# ------------------------------------------------------------------------
def standardize_charge_type(charge_type):
    """
    Cleans and normalizes the charge description so that similar
    entries are grouped (e.g., "Distribution Charge First kWh").
    """
    charge_type = charge_type.strip()
    # If the description contains "Total Electric Charges" anywhere, unify it.
    if "total electric charges" in charge_type.lower():
        return "Total Electric Charges"

    # For 'First' charges.
    if "first" in charge_type.lower():
        # Remove "#### kWh/kW"
        charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type, flags=re.IGNORECASE).strip()
        if "distribution" in charge_type.lower():
            return "Distribution Charge First kWh"
        elif "transmission" in charge_type.lower():
            return "Transmission Charge First kWh"
        else:
            return charge_type

    # For 'Last' charges.
    if "last" in charge_type.lower():
        charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type, flags=re.IGNORECASE).strip()
        if "distribution" in charge_type.lower():
            return "Distribution Charge Last kWh"
        elif "transmission" in charge_type.lower():
            return "Transmission Charge Last kWh"
        else:
            return charge_type

    # Otherwise, remove words like "First/Last/Next" plus numeric kWh/kW tokens
    charge_type = re.sub(r'\b(First|Last|Next)\b', '', charge_type, flags=re.IGNORECASE).strip()
    charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type, flags=re.IGNORECASE).strip()
    return charge_type

# ------------------------------------------------------------------------
# 4) HELPER: Extract Total Use
# ------------------------------------------------------------------------
def extract_total_use_from_pdf(file_bytes):
    """
    Attempts to read "Total Use" from page 2 (index=1) by scanning
    for lines referencing meter or kWh usage. 
    """
    with pdfplumber.open(file_bytes) as pdf:
        if len(pdf.pages) < 2:
            return ""
        page = pdf.pages[1]
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # Try a known pattern
        for i, line in enumerate(lines):
            if "meter energy" in line.lower() and "number total" in line.lower():
                if i + 1 < len(lines) and "number type" in lines[i + 1].lower() and "days use" in lines[i + 1].lower():
                    if i + 2 < len(lines):
                        tokens = lines[i + 2].split()
                        for j in range(len(tokens) - 1, -1, -1):
                            if tokens[j].isdigit():
                                return tokens[j]
        # Fallback: look for a line with "1ND..." + "kWh"
        for line in lines:
            tokens = line.split()
            if (len(tokens) >= 6 and tokens[0].startswith("1ND") and "kWh" in " ".join(tokens)):
                for j in range(len(tokens) - 1, -1, -1):
                    if tokens[j].isdigit():
                        return tokens[j]
        return ""

# ------------------------------------------------------------------------
# 5) HELPER: Extract Metadata (Bill Date, Account Number) from Page 0
# ------------------------------------------------------------------------
def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Account_Number": ""}
    with pdfplumber.open(file_bytes) as pdf:
        if not pdf.pages:
            return metadata

        text = pdf.pages[0].extract_text() or ""
        lines = text.splitlines()

        # Bill Issue Date
        for line in lines:
            match = re.search(r"Bill Issue date:\s*(.+)", line, re.IGNORECASE)
            if match:
                date_text = match.group(1).strip()
                try:
                    parsed_date = date_parse(date_text, fuzzy=True)
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except:
                    pass
                break

        # Account Number
        for line in lines:
            match = re.search(r"Account\s*number:\s*([\d\s]+)", line, re.IGNORECASE)
            if match:
                metadata["Account_Number"] = match.group(1).strip()
                break

    return metadata

# ------------------------------------------------------------------------
# 6) EXTRACT CHARGES FROM PDF (Combining Pages 1 and 2)
# ------------------------------------------------------------------------
def extract_charges_from_pdf(file_bytes):
    """
    Opens pages 1 and 2, attempts to extract tables from each,
    and parses them into a list of dicts:
       [ {"Charge_Type":..., "Calculation":..., "Amount":...}, ... ]
    """
    rows_out = []
    with pdfplumber.open(file_bytes) as pdf:
        # Typically, page 0 is the cover page, so we check pages 1 and 2 for charges
        for page_index in [1, 2]:
            if page_index < len(pdf.pages):
                page = pdf.pages[page_index]
                table_data = extract_bill_table(page)
                parsed_rows = parse_bill_table(table_data)
                rows_out.extend(parsed_rows)
    return rows_out

# ------------------------------------------------------------------------
# 7) MAIN PDF Processing Function
# ------------------------------------------------------------------------
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()

    # Extract main table rows
    table_charges = extract_charges_from_pdf(file_io)
    # Extract metadata and total use
    metadata = extract_metadata_from_pdf(file_io)
    total_use = extract_total_use_from_pdf(file_io)

    # Consolidate the charges by standardizing their type
    consolidated = {}
    for row in table_charges:
        ct = standardize_charge_type(row["Charge_Type"])
        amt = row["Amount"]
        # If you want the "Calculation" text, you can store it or parse it for rates
        # but for now we only store an optional rate if found in the text
        # Example: "First 1000 kWh X $0.0723610 per kWh" => 0.0723610
        rate_match = re.search(r'\$([\d\.]+(?:[−-])?)\s*per\s*k(?:Wh|W)', row["Calculation"], re.IGNORECASE)
        rate_val = rate_match.group(1) if rate_match else ""

        if ct not in consolidated:
            consolidated[ct] = {"Amount": 0.0, "Rate": ""}
        consolidated[ct]["Amount"] += amt
        if not consolidated[ct]["Rate"] and rate_val:
            consolidated[ct]["Rate"] = rate_val

    # Build or retrieve user/bill IDs
    account_number = metadata.get("Account_Number", "").replace(" ", "")
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    if account_number:
        if account_number in st.session_state.customer_ids:
            user_id = st.session_state.customer_ids[account_number]
        else:
            user_id = str(uuid.uuid4())
            st.session_state.customer_ids[account_number] = user_id
    else:
        user_id = str(uuid.uuid4())

    existing = worksheet.get_all_records()
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())

    # Build the final output row for the Google Sheet
    output_row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash,
        "Total Use": total_use
    }
    # Add each consolidated charge to the row
    for ct in consolidated:
        if consolidated[ct]["Amount"] != 0:
            output_row[f"{ct} Amount"] = consolidated[ct]["Amount"]
        if consolidated[ct]["Rate"]:
            output_row[f"{ct} Rate"] = consolidated[ct]["Rate"]

    return output_row

# ------------------------------------------------------------------------
# 8) SHEET Append Function
# ------------------------------------------------------------------------
def append_row_to_sheet(row_dict):
    meta_cols = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash", "Total Use"]
    current_data = worksheet.get_all_values()
    if current_data:
        headers = current_data[0]
        existing_rows = current_data[1:]
    else:
        headers = []
        existing_rows = []

    charge_cols = [col for col in row_dict if col not in meta_cols]
    existing_charge_cols = [col for col in headers if col not in meta_cols]
    new_charge_cols = [col for col in charge_cols if col not in existing_charge_cols]
    all_charge_cols = existing_charge_cols + new_charge_cols
    full_headers = meta_cols + all_charge_cols

    # If the headers changed, rewrite them (and re-pad existing rows)
    if full_headers != headers:
        worksheet.update("A1", [full_headers])
        if existing_rows:
            for i, row in enumerate(existing_rows, start=2):
                row_dict_existing = dict(zip(headers, row))
                padded_row = [str(row_dict_existing.get(h, "")) for h in full_headers]
                worksheet.update(f"A{i}", [padded_row])

    row_values = [str(row_dict.get(h, "")) for h in full_headers]
    worksheet.append_row(row_values)

# ------------------------------------------------------------------------
# 9) STREAMLIT App Interface
# ------------------------------------------------------------------------
st.title("Delmarva BillWatch")
st.write("Upload your PDF bill. Your deidentified utility charge information will be stored in Google Sheets.")
st.write("**Privacy Disclaimer:** By submitting your form, you agree that your response may be used to support an investigation into billing issues with Delmarva Power. Your information will not be shared publicly or sold. This form is for informational and organizational purposes only and does not constitute legal representation.")

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
