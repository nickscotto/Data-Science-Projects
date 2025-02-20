import streamlit as st
import io
import re
import uuid
import hashlib
from datetime import datetime
from collections import OrderedDict
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
try:
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
except Exception as e:
    st.error("Error opening spreadsheet: " + str(e))
    st.stop()
worksheet = spreadsheet.sheet1

# --- Mapping for Charge Types ---
CHARGE_TYPE_MAP = {
    "customer charge": "Customer Charge",
    "distribution charge": "Distribution Charge",
    "environmental surcharge": "Environmental Surcharge",
    "empower maryland charge": "EmPOWER Maryland Charge",
    "administrative credit": "Administrative Credit",
    "universal service program": "Universal Service Program",
    "md franchise tax": "MD Franchise Tax",
    "total electric delivery charges": "Total Electric Delivery Charges",
    "standard offer service & transmission": "Standard Offer Service & Transmission",
    "procurement cost adjustment": "Procurement Cost Adjustment",
    "total electric supply charges": "Total Electric Supply Charges",
    "total electric charges - residential service": "Total Electric Charges - Residential Service"
}

# Define the complete set of expected output keys in the desired order
EXPECTED_HEADERS = [
    "User_ID",
    "Bill_ID",
    "Bill_Month_Year",
    "Bill_Hash",
    "Customer Charge Amount",
    "Distribution Charge Amount",
    "Distribution Charge Rate",
    "Environmental Surcharge Amount",
    "Environmental Surcharge Rate",
    "EmPOWER Maryland Charge Amount",
    "EmPOWER Maryland Charge Rate",
    "Administrative Credit Amount",
    "Universal Service Program Amount",
    "MD Franchise Tax Amount",
    "MD Franchise Tax Rate",
    "Total Electric Delivery Charges Amount",
    "Standard Offer Service & Transmission Amount",
    "Standard Offer Service & Transmission Rate",
    "Procurement Cost Adjustment Amount",
    "Procurement Cost Adjustment Rate",
    "Total Electric Supply Charges Amount",
    "Total Electric Charges - Residential Service Amount"
]

# --- Utility Functions ---
def get_all_records_safe(ws):
    try:
        return ws.get_all_records()
    except Exception as e:
        st.error("Error fetching records: " + str(e))
        data = ws.get_all_values()
        if data and len(data) > 1:
            headers = data[0]
            seen = {}
            unique_headers = []
            for h in headers:
                if h in seen:
                    seen[h] += 1
                    unique_headers.append(f"{h}_{seen[h]}")
                else:
                    seen[h] = 0
                    unique_headers.append(h)
            return [dict(zip(unique_headers, row)) for row in data[1:]]
        return []

def map_charge_description(desc):
    desc_lower = desc.lower()
    for partial, standard in CHARGE_TYPE_MAP.items():
        if partial in desc_lower:
            return standard
    return None

def get_user_id(name):
    normalized = name.strip().upper() or "UNKNOWN"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

# --- Parsing and Extraction Functions ---
def parse_charge_line(line):
    patterns = [
        # Pattern for lines with kWh calculation (e.g., "95 kWh X $0.0600150 per kWh 5.70")
        r"^(?P<desc>.+?)(?:\s+\d+\s*kWh\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?\s+(?P<amount>-?[\d,\.]+)$",
        # Pattern for simple amount lines (e.g., "Customer Charge 9.19")
        r"^(?P<desc>.+?)\s+\$(?P<amount>-?[\d,\.]+)$"
    ]
    
    for pattern in patterns:
        m = re.match(pattern, line)
        if m:
            raw_desc = m.group("desc").strip()
            rate_val = m.group("rate") if "rate" in m.groupdict() and m.group("rate") else ""
            amt_str = m.group("amount").replace(",", "").replace("−", "-")
            try:
                amt_val = float(amt_str)
                return {"desc": raw_desc, "rate": rate_val, "amount": amt_val}
            except ValueError:
                continue
    return None

def extract_charge_tables(file_bytes):
    tables = []
    with pdfplumber.open(file_bytes) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            i = 0
            while i < len(lines):
                if ("type of charge" in lines[i].lower() and 
                    "how we calculate" in lines[i].lower() and 
                    "amount($" in lines[i].lower()):
                    table_lines = []
                    i += 1
                    while i < len(lines):
                        curr = lines[i]
                        if ("type of charge" in curr.lower() and 
                            "how we calculate" in curr.lower() and 
                            "amount($" in curr.lower()):
                            break
                        if curr.strip():
                            table_lines.append(curr)
                        i += 1
                    
                    if table_lines:
                        combined = []
                        buffer = ""
                        for line in table_lines:
                            candidate = (buffer + " " + line).strip() if buffer else line
                            parsed = parse_charge_line(candidate)
                            if parsed:
                                combined.append(parsed)
                                buffer = ""
                            else:
                                buffer = candidate
                        if combined:
                            tables.append(combined)
                else:
                    i += 1
    return tables

def extract_charges(file_bytes):
    tables = extract_charge_tables(file_bytes)
    charges = []
    
    for table in tables:
        for parsed in table:
            cleaned_desc = re.sub(r"\d+\s*kWh", "", parsed["desc"], flags=re.IGNORECASE).strip()
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
    meta = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    
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

    candidate = ""
    if date_idx is not None:
        for ln in lines[date_idx+1:]:
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
    charges = extract_charges(file_io)
    meta = extract_metadata_from_pdf(file_io)
    user_id = get_user_id(meta["Person"])

    output = OrderedDict()
    for key in EXPECTED_HEADERS:
        output[key] = ""

    output["User_ID"] = user_id
    output["Bill_ID"] = str(uuid.uuid4())
    output["Bill_Month_Year"] = meta["Bill_Month_Year"]
    output["Bill_Hash"] = bill_hash

    for ch in charges:
        mapped = ch.get("Mapped")
        if mapped:
            amt_key = f"{mapped} Amount"
            rate_key = f"{mapped} Rate"
            if amt_key in output:
                output[amt_key] = ch["Amount"]
            if ch["Rate"] and rate_key in output:
                output[rate_key] = ch["Rate"]
    return output

def append_row_to_sheet(row_dict):
    existing = get_all_records_safe(worksheet)
    if existing:
        headers = list(existing[0].keys())
    else:
        headers = list(EXPECTED_HEADERS)
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
