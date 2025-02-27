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

# --- Helper: Reassemble Table Rows Using Word Clustering ---
def extract_table_rows(page, tolerance=5):
    """
    Group words by their vertical coordinate (within a tolerance)
    to reassemble the table rows.
    """
    words = page.extract_words()
    rows = {}
    for word in words:
        key = round(word['top'] / tolerance) * tolerance
        rows.setdefault(key, []).append(word)
    sorted_rows = []
    for key in sorted(rows.keys()):
        row_words = sorted(rows[key], key=lambda w: w['x0'])
        row_text = " ".join(w['text'] for w in row_words)
        sorted_rows.append(row_text)
    return sorted_rows

# --- Helper: Standardize Charge Type Names ---
def standardize_charge_type(charge_type):
    charge_type = charge_type.strip()
    # Unify any charge that contains "Total Electric Charges"
    if "Total Electric Charges" in charge_type:
        return "Total Electric Charges"
    # For 'First' charges.
    if "First" in charge_type:
        charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type).strip()
        if "Distribution" in charge_type:
            return "Distribution Charge First kWh"
        elif "Transmission" in charge_type:
            return "Transmission Charge First kWh"
        else:
            return charge_type
    # For 'Last' charges.
    elif "Last" in charge_type:
        charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type).strip()
        if "Distribution" in charge_type:
            return "Distribution Charge Last kWh"
        elif "Transmission" in charge_type:
            return "Transmission Charge Last kWh"
        else:
            return charge_type
    # Otherwise, remove extra keywords and numbers.
    charge_type = re.sub(r'\b(First|Last|Next)\b', '', charge_type).strip()
    charge_type = re.sub(r'\s*\d+\s*k(?:Wh|W)', ' kWh', charge_type).strip()
    return charge_type

# --- Key Helper: Extract Total Use (More Robust) ---
def extract_total_use_from_pdf(file_bytes):
    with pdfplumber.open(file_bytes) as pdf:
        if len(pdf.pages) < 2:
            return ""
        page = pdf.pages[1]
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            if "meter energy" in line.lower() and "number total" in line.lower():
                if i + 1 < len(lines) and "number type" in lines[i + 1].lower() and "days use" in lines[i + 1].lower():
                    if i + 2 < len(lines):
                        tokens = lines[i + 2].split()
                        for j in range(len(tokens) - 1, -1, -1):
                            if tokens[j].isdigit():
                                return tokens[j]
        for line in lines:
            tokens = line.split()
            if (len(tokens) >= 6 and tokens[0].startswith("1ND") and "kWh" in " ".join(tokens)):
                for j in range(len(tokens) - 1, -1, -1):
                    if tokens[j].isdigit():
                        return tokens[j]
        return ""

# --- PDF Extraction Functions: Charges from the Table ---
def extract_charges_from_pdf(file_bytes):
    rows_out = []
    with pdfplumber.open(file_bytes) as pdf:
        # Process pages that likely contain the charge table (e.g., pages 1 and 2)
        for page_index in [1, 2]:
            if page_index < len(pdf.pages):
                page = pdf.pages[page_index]
                table_rows = extract_table_rows(page)
                header_found = False
                seen_total = False  # Flag to indicate a row with "Total" has been encountered.
                for row in table_rows:
                    row_lower = row.lower()
                    # Look for header row.
                    if not header_found and "type of charge" in row_lower and "amount($" in row_lower:
                        header_found = True
                        continue
                    if header_found:
                        # Check if this row's description starts with "total"
                        # (after we extract the description, we'll later clean it)
                        temp_desc = row.strip().split()[0].lower() if row.strip() else ""
                        is_total_row = temp_desc.startswith("total")
                        if is_total_row:
                            seen_total = True
                        else:
                            # If we've seen one or more total rows and now encounter a non-total row,
                            # assume we've reached the bottom of the table.
                            if seen_total:
                                break
                        # Use regex to capture the row.
                        pattern = (
                            r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+(?:[−-])?)(?:\s+per\s+k(?:Wh|W)))?\s+(?P<amount>-?[\d,]+(?:\.\d+)?(?:[−-])?)\s*$"
                        )
                        match = re.match(pattern, row)
                        if match:
                            desc = match.group("desc").strip()
                            # Clean up description: remove stray numeric tokens (e.g. "11532 kWh").
                            desc = re.sub(r'\b\d+\s*k(?:Wh|W)\b', '', desc).strip()
                            rate_val = match.group("rate") or ""
                            raw_amount = match.group("amount").replace(",", "")
                            try:
                                amount = float(raw_amount)
                            except ValueError:
                                continue
                            rows_out.append({
                                "Charge_Type": desc,
                                "Rate": rate_val,
                                "Amount": amount
                            })
    return rows_out

def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Account_Number": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        for line in text.splitlines():
            match = re.search(r"Bill Issue date:\s*(.+)", line, re.IGNORECASE)
            if match:
                date_text = match.group(1).strip()
                try:
                    parsed_date = date_parse(date_text, fuzzy=True)
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except:
                    pass
                break
        for line in text.splitlines():
            match = re.search(r"Account\s*number:\s*([\d\s]+)", line, re.IGNORECASE)
            if match:
                metadata["Account_Number"] = match.group(1).strip()
                break
    return metadata

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    total_use = extract_total_use_from_pdf(file_io)

    # Consolidate charges by standardizing their type.
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

    # Build or retrieve user and bill IDs.
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

    # Build the final output row.
    output_row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash,
        "Total Use": total_use
    }
    for ct in consolidated:
        if consolidated[ct]["Amount"] != 0:
            output_row[f"{ct} Amount"] = consolidated[ct]["Amount"]
        if consolidated[ct]["Rate"]:
            output_row[f"{ct} Rate"] = consolidated[ct]["Rate"]

    return output_row

# --- Sheet Append Function ---
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

    if full_headers != headers:
        worksheet.update("A1", [full_headers])
        if existing_rows:
            for i, row in enumerate(existing_rows, start=2):
                row_dict_existing = dict(zip(headers, row))
                padded_row = [str(row_dict_existing.get(h, "")) for h in full_headers]
                worksheet.update(f"A{i}", [padded_row])

    row_values = [str(row_dict.get(h, "")) for h in full_headers]
    worksheet.append_row(row_values)

# --- Streamlit App Interface ---
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
