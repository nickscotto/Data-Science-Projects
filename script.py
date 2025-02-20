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
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
try:
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
except Exception as e:
    st.error("Error opening spreadsheet: " + str(e))
    st.stop()
worksheet = spreadsheet.sheet1

# --- Mapping for Charge Types ---
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
    "total electric charges - residential service": "Total Electric Charges - Residential Service",
    "delivery": "Delivery",
    "supply": "Supply"
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
    desc_lower = desc.lower()
    for partial, standard in CHARGE_TYPE_MAP.items():
        if partial in desc_lower:
            return standard
    return None

def get_user_id(name):
    name = name.strip() or "Unknown"
    return hashlib.sha256(name.encode("utf-8")).hexdigest()

# --- Charge Table Extraction ---
def extract_charge_tables(file_bytes):
    """
    Scans every page for a header line containing "type of charge" and "amount".
    For each header found, collects subsequent lines until a line that seems to be outside the table.
    Returns a list of lists (each inner list is one table of charge lines).
    """
    tables = []
    with pdfplumber.open(file_bytes) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            i = 0
            while i < len(lines):
                line = lines[i]
                if "type of charge" in line.lower() and "amount" in line.lower():
                    # Found header: now collect subsequent lines as table rows.
                    table = []
                    i += 1
                    while i < len(lines):
                        curr = lines[i]
                        # Heuristic: if the line is too short or doesn't contain numbers or "kWh", assume end of table.
                        if len(curr) < 3 or (not re.search(r"\d", curr) and "kwh" not in curr.lower()):
                            break
                        table.append(curr)
                        i += 1
                    if table:
                        tables.append(table)
                else:
                    i += 1
    return tables

def parse_charge_line(line):
    """
    Attempts to parse a charge line using two patterns:
      Pattern 1: <desc> [X $<rate> per kWh] <amount>
      Pattern 2: <desc> $<amount>
    Returns a dict with keys "desc", "rate", "amount" or None if no match.
    """
    pattern1 = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    pattern2 = r"^(?P<desc>.+?)\s+\$(?P<amount>[\d,\.]+)$"
    m = re.match(pattern1, line)
    if not m:
        m = re.match(pattern2, line)
    if m:
        raw_desc = m.group("desc").strip()
        rate_val = m.group("rate") if "rate" in m.groupdict() else ""
        amt_str = m.group("amount").replace(",", "").replace("−", "")
        try:
            amt_val = float(amt_str)
        except ValueError:
            return None
        return {"desc": raw_desc, "rate": rate_val, "amount": amt_val}
    return None

def extract_charges_from_pdf(file_bytes):
    """
    Uses extract_charge_tables() to obtain all tables and then parses each charge line.
    Only returns charges that map to a known standardized description.
    """
    tables = extract_charge_tables(file_bytes)
    charges = []
    for table in tables:
        for line in table:
            parsed = parse_charge_line(line)
            if parsed:
                # Remove digits from "kWh" portions for cleaning.
                cleaned_desc = re.sub(r"\d+\s*kWh", "kWh", parsed["desc"], flags=re.IGNORECASE).strip()
                mapped = map_charge_description(cleaned_desc)
                if mapped:
                    charges.append({
                        "Mapped": mapped,
                        "Rate": parsed["rate"],
                        "Amount": parsed["amount"]
                    })
    return charges

# --- Metadata Extraction ---
def extract_metadata_from_pdf(file_bytes):
    """
    Extracts the bill date and a candidate name from page 1.
    Searches for date patterns anywhere in each line and, once found,
    looks among subsequent lines for a candidate that seems like a name.
    """
    meta = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    
    # Define multiple date patterns.
    patterns = [
        (r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}", "%B %Y"),
        (r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}", "%b %Y"),
        (r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", "%B %d, %Y"),
        (r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}", "%b %d, %Y")
    ]
    
    date_found = False
    date_idx = None
    for i, line in enumerate(lines):
        for pat, fmt in patterns:
            match = re.search(pat, line, re.IGNORECASE)
            if match:
                date_str = match.group(0)
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    meta["Bill_Month_Year"] = parsed.strftime("%m-%Y")
                except Exception:
                    meta["Bill_Month_Year"] = date_str
                date_found = True
                date_idx = i
                break
        if date_found:
            break

    # Look for a candidate name after the date header.
    candidate = ""
    if date_idx is not None:
        for ln in lines[date_idx+1:]:
            # Skip lines with common billing words.
            if any(word in ln.lower() for word in ["account", "bill", "period", "address", "issue", "summary", "total"]):
                continue
            if " " in ln:
                letters = [ch for ch in ln if ch.isalpha()]
                if letters and (sum(1 for ch in letters if ch.isupper()) / len(letters)) >= 0.8:
                    candidate = ln
                    break
    if not candidate:
        candidate = "Unknown"
    meta["Person"] = candidate
    return meta

# --- Main PDF Processing ---
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

    # Build the output row (always include the four base fields).
    output = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": meta["Bill_Month_Year"],
        "Bill_Hash": bill_hash
    }
    # For each recognized charge, only add a column if the value exists.
    for ch in charges:
        mapped = ch.get("Mapped")
        if mapped:
            amt_col = f"{mapped} Amount"
            rate_col = f"{mapped} Rate"
            output[amt_col] = ch["Amount"]
            if ch["Rate"]:
                output[rate_col] = ch["Rate"]
    return output

def append_row_to_sheet(row_dict):
    existing = get_all_records_safe(worksheet)
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = ["User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash"]
    # Only add new columns if they have a non-empty value.
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
    bill_hash = hashlib.md5(file_bytes).hexdigest()
    existing = get_all_records_safe(worksheet)
    if any(r.get("Bill_Hash") == bill_hash for r in existing):
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        output_row = process_pdf(io.BytesIO(file_bytes))
        append_row_to_sheet(output_row)
        st.success("Thank you for your contribution!")
