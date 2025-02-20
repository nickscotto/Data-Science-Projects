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

# --- Mappings ---
# Map partial strings from the PDF to standardized keys
CHARGE_TYPE_MAP = {
    "customer charge": "Customer Charge",
    "distribution charge first": "Distribution Charge First kWh",
    "distribution charge last": "Distribution Charge Last kWh",
    "environmental surcharge": "Environmental Surcharge kWh",
    "empower maryland": "EmPOWER Maryland kWh",
    "charge kwh": "EmPOWER Maryland kWh",  # if your PDF says "Charge kWh" for EmPOWER
    "universal service program": "Universal Service Program",
    "md franchise tax": "MD Franchise Tax kWh",
    "total electric delivery charges": "Total Electric Delivery Charges",
    "transmission first": "Transmission First kWh",
    "transmission last": "Transmission Last kWh",
    "adjustment": "Adjustment kWh",
    "total electric supply charges": "Total Electric Supply Charges",
    "total electric charges - residential service": "Total Electric Charges - Residential Service"
}

# The final columns in the order you want them to appear
ORDERED_COLUMNS = [
    "User_ID",
    "Bill_ID",
    "Bill_Month_Year",
    "Bill_Hash",
    "Customer Charge Amount",
    "Customer Charge Rate",
    "Distribution Charge First kWh Amount",
    "Distribution Charge First kWh Rate",
    "Distribution Charge Last kWh Amount",
    "Distribution Charge Last kWh Rate",
    "Environmental Surcharge kWh Amount",
    "Environmental Surcharge kwh Rate",
    "EmPOWER Maryland kWh Amount",
    "EmPOWER Maryland kWh Rate",
    "Universal Service Program Amount",
    "MD Franchise Tax kWh Amount",
    "MD Franchise Tax kWh Rate",
    "Total Electric Delivery Charges Amount",
    "Transmission First kWh Amount",
    "Transmission First kWh Rate",
    "Transmission Last kWh Amount",
    "Transmission Last kWh Rate",
    "Adjustment kWh Amount",
    "Adjustment kWh Rate",
    "Total Electric Supply Charges Amount",
    "Total Electric Charges - Residential Service Amount"
]

# --- Utility Functions ---
def map_charge_type(desc):
    """
    Given a partial description like 'Distribution Charge First 1000 kWh X $0.078...',
    return a standardized key (e.g. 'Distribution Charge First kWh').
    We do a fuzzy match by checking each key in CHARGE_TYPE_MAP.
    """
    desc_lower = desc.lower()
    for partial, standard in CHARGE_TYPE_MAP.items():
        if partial in desc_lower:
            return standard
    return None  # unrecognized

def get_all_records_safe(ws):
    """
    Retrieve existing records with expected headers or fallback to get_all_values.
    We won't enforce expected_headers here because we handle stable columns ourselves.
    """
    try:
        return ws.get_all_records()
    except Exception as e:
        st.error("Error fetching records: " + str(e))
        data = ws.get_all_values()
        if data and len(data) > 1:
            headers = data[0]
            return [dict(zip(headers, row)) for row in data[1:]]
        return []

def get_user_id(name):
    """Deterministic ID by hashing the name. If blank, random UUID."""
    name = name.strip()
    if not name:
        return str(uuid.uuid4())
    return hashlib.sha256(name.encode("utf-8")).hexdigest()

def standardize_charge_type(charge_type):
    """
    Remove numeric kWh values from the textual desc, e.g. 'Distribution Charge Last 2190 kWh' -> 'Distribution Charge Last kWh'
    Then map it using CHARGE_TYPE_MAP.
    """
    # Remove any digits + 'kWh' pattern, then map
    # e.g. 'Distribution Charge Last 2190 kWh' -> 'Distribution Charge Last kWh'
    cleaned = re.sub(r"\d+\s*kWh", "kWh", charge_type, flags=re.IGNORECASE)
    return cleaned.strip()

# --- PDF Extraction: Charges ---
def extract_charges_from_pdf(file_bytes):
    rows = []
    charge_pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?"
        r"\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    import pdfplumber
    with pdfplumber.open(file_bytes) as pdf:
        # Typically pages 1-2 contain the "Type of charge" table
        for page_index in [1, 2]:
            if page_index < len(pdf.pages):
                text = pdf.pages[page_index].extract_text() or ""
                lines = text.splitlines()
                header_found = False
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Check if we found "Type of charge" and "Amount($" on the same line
                    if not header_found and "Type of charge" in line.lower() and "amount($" in line.lower():
                        header_found = True
                        continue
                    if header_found:
                        m = re.match(charge_pattern, line)
                        if m:
                            desc = m.group("desc").strip()
                            rate_val = m.group("rate") or ""
                            amount_str = m.group("amount").replace(",", "").replace("−", "")
                            try:
                                amount_val = float(amount_str)
                            except ValueError:
                                continue
                            # Now map the desc to a standardized type
                            mapped = map_charge_type(desc)
                            if mapped:
                                rows.append({
                                    "Mapped": mapped,  # e.g. 'Distribution Charge First kWh'
                                    "Rate": rate_val,
                                    "Amount": amount_val
                                })
    return rows

# --- PDF Extraction: Metadata (Date, Name) ---
def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Date patterns
    date_patterns = [
        (r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$", "%B %Y"),
        (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}$", "%b %Y"),
        (r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$", "%B %d, %Y"),
        (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}$", "%b %d, %Y")
    ]

    date_found = False
    date_idx = None
    for i, line in enumerate(lines):
        for pat, fmt in date_patterns:
            if re.match(pat, line, re.IGNORECASE):
                try:
                    parsed = datetime.strptime(line, fmt)
                    metadata["Bill_Month_Year"] = parsed.strftime("%m-%Y")
                except Exception:
                    metadata["Bill_Month_Year"] = ""  # if parse fails, keep empty
                date_found = True
                date_idx = i
                break
        if date_found:
            break

    # Heuristic for name: look in subsequent lines for uppercase line
    # or just "NOURHAN SCOTTO" or something that looks like a name
    name_candidate = ""
    if date_idx is not None:
        for ln in lines[date_idx+1:]:
            if "account" in ln.lower() or "bill" in ln.lower() or "period" in ln.lower() or "address" in ln.lower():
                continue
            letters = [ch for ch in ln if ch.isalpha()]
            # If there's at least one space and it's mostly uppercase letters
            if " " in ln and letters and all(ch.isupper() for ch in letters):
                name_candidate = ln
                break
    if not name_candidate:
        name_candidate = "Unknown"

    metadata["Person"] = name_candidate
    return metadata

# --- Safe retrieval of existing sheet data ---
def get_all_records_safe(ws):
    try:
        return ws.get_all_records()
    except Exception as e:
        st.error("Error fetching records: " + str(e))
        data = ws.get_all_values()
        if data and len(data) > 1:
            headers = data[0]
            return [dict(zip(headers, row)) for row in data[1:]]
        return []

# --- Main PDF Processing ---
def process_pdf(file_io):
    # Compute a bill hash for duplicates
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()

    # Extract charges & metadata
    charges = extract_charges_from_pdf(file_io)
    meta = extract_metadata_from_pdf(file_io)

    # Generate a user_id from the person's name
    user_id = get_user_id(meta["Person"])

    # Check for duplicate
    existing = get_all_records_safe(worksheet)
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())

    # Build an output row with stable columns
    # Start with the 4 main fields
    output = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": meta["Bill_Month_Year"],
        "Bill_Hash": bill_hash
    }

    # For each extracted charge, place it in the correct "X Amount" and "X Rate" columns
    # according to the mapped name
    for ch in charges:
        mapped_name = ch["Mapped"]  # e.g. 'Distribution Charge First kWh'
        amount_col = mapped_name + " Amount"
        rate_col   = mapped_name + " Rate"
        # If they appear in your ORDERED_COLUMNS, populate them
        output[amount_col] = ch["Amount"]
        if ch["Rate"]:
            output[rate_col] = ch["Rate"]

    return output

def append_row_to_sheet(row_dict):
    existing = get_all_records_safe(worksheet)
    if existing:
        headers = list(existing[0].keys())
    else:
        # If sheet is empty, start with your desired ORDERED_COLUMNS
        headers = []

    # We want to ensure columns appear in a stable order
    # Merge existing headers with ORDERED_COLUMNS
    # and also add any new columns from row_dict
    final_headers = list(headers)
    # Add from ORDERED_COLUMNS first if they're not in existing
    for col in ORDERED_COLUMNS:
        if col not in final_headers:
            final_headers.append(col)
    # Then add any new columns from row_dict if not already present
    for key in row_dict.keys():
        if key not in final_headers:
            final_headers.append(key)

    # If the sheet was empty, append final_headers as the first row
    if not existing:
        worksheet.append_row(final_headers)

    # Build the row in the order of final_headers
    row_values = []
    for col in final_headers:
        val = row_dict.get(col, "")
        row_values.append(str(val))
    worksheet.append_row(row_values)

# --- Streamlit UI ---
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
    bill_hash = hashlib.md5(file_bytes).hexdigest()
    existing = get_all_records_safe(worksheet)
    # Duplicate detection
    if any(r.get("Bill_Hash") == bill_hash for r in existing):
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(io.BytesIO(file_bytes))
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
