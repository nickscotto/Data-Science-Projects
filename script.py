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

# A dictionary that maps partial strings to a standard charge name
CHARGE_TYPE_MAP = {
    "customer charge": "Customer Charge",
    "distribution charge first": "Distribution Charge First kWh",
    "distribution charge last": "Distribution Charge Last kWh",
    "environmental surcharge": "Environmental Surcharge kWh",
    "empower maryland": "EmPOWER Maryland kWh",
    "universal service program": "Universal Service Program",
    "md franchise tax": "MD Franchise Tax kWh",
    "total electric delivery charges": "Total Electric Delivery Charges",
    "transmission first": "Transmission First kWh",
    "transmission last": "Transmission Last kWh",
    "adjustment": "Adjustment kWh",
    "total electric supply charges": "Total Electric Supply Charges",
    "total electric charges - residential service": "Total Electric Charges - Residential Service"
}

# The final columns in a stable order
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
    "Environmental Surcharge kWh Rate",
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

def map_charge_description(desc):
    """
    Return a standardized charge name by matching partial strings from CHARGE_TYPE_MAP.
    If none match, return None.
    """
    desc_lower = desc.lower()
    for partial, standard in CHARGE_TYPE_MAP.items():
        if partial in desc_lower:
            return standard
    return None

def get_user_id(name):
    """Deterministic ID by hashing the name. If blank, random UUID."""
    name = name.strip()
    if not name:
        return str(uuid.uuid4())
    return hashlib.sha256(name.encode("utf-8")).hexdigest()

def standardize_charge_type(charge_type):
    """
    Remove numeric kWh values from textual desc, e.g. 'Distribution Charge Last 2190 kWh' -> 'Distribution Charge Last kWh'
    Then map it using map_charge_description
    """
    cleaned = re.sub(r"\d+\s*kWh", "kWh", charge_type, flags=re.IGNORECASE)
    return cleaned.strip()

def extract_charges_from_pdf(file_bytes):
    """
    Scan every line on pages 1 & 2 for pattern: 
      <desc> [X $<rate> per kWh] <amount>
    Then map the desc to a standard name via map_charge_description.
    """
    pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?"
        r"\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    charges = []
    with pdfplumber.open(file_bytes) as pdf:
        for page_idx in [1, 2]:
            if page_idx < len(pdf.pages):
                text = pdf.pages[page_idx].extract_text() or ""
                lines = text.splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(pattern, line)
                    if m:
                        raw_desc = m.group("desc").strip()
                        rate_val = m.group("rate") or ""
                        amount_str = m.group("amount").replace(",", "").replace("−", "")
                        try:
                            amount_val = float(amount_str)
                        except ValueError:
                            continue
                        # standardize & map
                        cleaned_desc = standardize_charge_type(raw_desc)
                        mapped = map_charge_description(cleaned_desc)
                        if mapped:
                            charges.append({
                                "Mapped": mapped,
                                "Rate": rate_val,
                                "Amount": amount_val
                            })
    return charges

def extract_metadata_from_pdf(file_bytes):
    """
    Extract date from page 1 using multiple patterns. 
    Then look for uppercase line afterwards as a name, else 'Unknown'.
    """
    meta = {"Bill_Month_Year": "", "Person": ""}
    date_patterns = [
        (r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$", "%B %Y"),
        (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}$", "%b %Y"),
        (r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$", "%B %d, %Y"),
        (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}$", "%b %d, %Y")
    ]
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    date_found = False
    date_idx = None
    for i, line in enumerate(lines):
        for pat, fmt in date_patterns:
            if re.match(pat, line, re.IGNORECASE):
                try:
                    parsed = datetime.strptime(line, fmt)
                    meta["Bill_Month_Year"] = parsed.strftime("%m-%Y")
                except:
                    meta["Bill_Month_Year"] = ""
                date_found = True
                date_idx = i
                break
        if date_found:
            break

    # Attempt name extraction in subsequent lines
    name_candidate = ""
    if date_idx is not None:
        for ln in lines[date_idx+1:]:
            # skip lines with typical billing words
            if any(word in ln.lower() for word in ["account", "bill", "period", "address", "issue", "summary"]):
                continue
            letters = [ch for ch in ln if ch.isalpha()]
            if " " in ln and letters and all(ch.isupper() for ch in letters):
                name_candidate = ln
                break
    if not name_candidate:
        name_candidate = "Unknown"
    meta["Person"] = name_candidate

    return meta

def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    charges = extract_charges_from_pdf(file_io)
    meta = extract_metadata_from_pdf(file_io)
    user_id = get_user_id(meta["Person"])

    # Check for duplicates
    existing = get_all_records_safe(worksheet)
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())

    # Build output row
    output = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": meta["Bill_Month_Year"],
        "Bill_Hash": bill_hash
    }
    # Fill in recognized charges
    for ch in charges:
        mapped_name = ch["Mapped"]  # e.g. 'Distribution Charge First kWh'
        amount_col = mapped_name + " Amount"
        rate_col   = mapped_name + " Rate"
        output[amount_col] = ch["Amount"]
        if ch["Rate"]:
            output[rate_col] = ch["Rate"]

    return output

def append_row_to_sheet(row_dict):
    existing = get_all_records_safe(worksheet)
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = []

    # Merge ORDERED_COLUMNS with existing
    final_headers = list(headers)
    for col in ORDERED_COLUMNS:
        if col not in final_headers:
            final_headers.append(col)
    # also add any new columns from row_dict if not in final_headers
    for key in row_dict.keys():
        if key not in final_headers:
            final_headers.append(key)

    # if empty, append final_headers
    if not existing:
        worksheet.append_row(final_headers)

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
    if any(r.get("Bill_Hash") == bill_hash for r in existing):
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(io.BytesIO(file_bytes))
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
