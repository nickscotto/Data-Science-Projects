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
creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=scope
)
gc = gspread.authorize(creds)

# Use your spreadsheet ID to open your sheet.
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
try:
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
except Exception as e:
    st.error("Error opening spreadsheet: " + str(e))
    st.stop()
worksheet = spreadsheet.sheet1

# --- Mapping for Charge Types ---
# Maps partial strings in a charge description to a standardized name.
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

# --- Utility Functions ---
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
    """Return the standardized charge type if any key in CHARGE_TYPE_MAP is found."""
    desc_lower = desc.lower()
    for partial, standard in CHARGE_TYPE_MAP.items():
        if partial in desc_lower:
            return standard
    return None

def get_user_id(name):
    """Generate a deterministic User_ID by hashing the name; if name is blank, use 'Unknown'."""
    name = name.strip() or "Unknown"
    return hashlib.sha256(name.encode("utf-8")).hexdigest()

# --- PDF Extraction for Charges ---
def extract_charges_from_pdf(file_bytes):
    """
    Scan all lines on pages 1 and 2 for patterns matching:
       <description> [X $<rate> per kWh] <amount>
    If the description maps to a known charge type, return it.
    """
    pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?"
        r"\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    charges = []
    with pdfplumber.open(file_bytes) as pdf:
        for page_idx in [0, 1]:  # try pages 1 and 2 (index 0 and 1) – adjust if needed
            if page_idx < len(pdf.pages):
                text = pdf.pages[page_idx].extract_text() or ""
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(pattern, line)
                    if m:
                        raw_desc = m.group("desc").strip()
                        rate_val = m.group("rate") or ""
                        amt_str = m.group("amount").replace(",", "").replace("−", "")
                        try:
                            amt_val = float(amt_str)
                        except ValueError:
                            continue
                        std_desc = raw_desc  # remove digits from "kWh" parts if any
                        std_desc = re.sub(r"\d+\s*kWh", "kWh", std_desc, flags=re.IGNORECASE).strip()
                        mapped = map_charge_description(std_desc)
                        if mapped:
                            charges.append({
                                "Mapped": mapped,
                                "Rate": rate_val,
                                "Amount": amt_val
                            })
    return charges

# --- PDF Extraction for Metadata (Date & Name) ---
def extract_metadata_from_pdf(file_bytes):
    """
    Extract Bill_Month_Year and Person from page 1.
    Scans all non-empty lines for various date formats.
    Returns:
      - Bill_Month_Year in MM-YYYY format (if found)
      - Person: a candidate line that appears to be a name based on heuristics, or "Unknown"
    """
    meta = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    
    # Date patterns: (pattern, corresponding datetime format)
    patterns = [
        (r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$", "%B %Y"),
        (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}$", "%b %Y"),
        (r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$", "%B %d, %Y"),
        (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}$", "%b %d, %Y")
    ]
    
    date_found = False
    date_idx = None
    for i, line in enumerate(lines):
        for pat, fmt in patterns:
            if re.match(pat, line, re.IGNORECASE):
                try:
                    parsed_date = datetime.strptime(line, fmt)
                    meta["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except Exception:
                    meta["Bill_Month_Year"] = ""
                date_found = True
                date_idx = i
                break
        if date_found:
            break

    # Now, search for a candidate name in lines after the date.
    # Heuristic: the candidate should not contain common billing words, must have a space, and be mostly uppercase.
    candidate = ""
    if date_idx is not None:
        for ln in lines[date_idx+1:]:
            if any(word in ln.lower() for word in ["account", "bill", "period", "address", "issue", "summary"]):
                continue
            if " " in ln:
                # Check if the line is mostly uppercase (at least 80% of letters)
                letters = [ch for ch in ln if ch.isalpha()]
                if letters:
                    ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
                    if ratio >= 0.8:
                        candidate = ln
                        break
    if not candidate:
        candidate = "Unknown"
    meta["Person"] = candidate
    return meta

# --- Main PDF Processing Function ---
def process_pdf(file_io):
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    charges = extract_charges_from_pdf(file_io)
    meta = extract_metadata_from_pdf(file_io)
    user_id = get_user_id(meta["Person"])

    # Check for duplicate bill using Bill_Hash.
    existing = get_all_records_safe(worksheet)
    bill_id = None
    for row in existing:
        if row.get("Bill_Hash") == bill_hash:
            bill_id = row.get("Bill_ID")
            break
    if not bill_id:
        bill_id = str(uuid.uuid4())

    # Build output row with base columns.
    output = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": meta["Bill_Month_Year"],
        "Bill_Hash": bill_hash
    }

    # For each charge, add columns only if value is non-empty.
    for ch in charges:
        mapped = ch.get("Mapped")
        if mapped:
            amount_col = mapped + " Amount"
            rate_col = mapped + " Rate"
            output[amount_col] = ch["Amount"]
            if ch["Rate"]:
                output[rate_col] = ch["Rate"]

    return output

def append_row_to_sheet(row_dict):
    # Get existing records from the sheet.
    existing = get_all_records_safe(worksheet)
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash"]
    # Only add new columns if the value is non-empty.
    for key, val in row_dict.items():
        if key not in headers and val != "":
            headers.append(key)
    if not existing:
        worksheet.append_row(headers)
    row_values = [str(row_dict.get(h, "")) for h in headers]
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
    # Duplicate detection using bill hash.
    bill_hash = hashlib.md5(file_bytes).hexdigest()
    existing = get_all_records_safe(worksheet)
    if any(r.get("Bill_Hash") == bill_hash for r in existing):
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(io.BytesIO(file_bytes))
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
