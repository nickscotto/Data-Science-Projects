\import streamlit as st
import pdfplumber
import re
from collections import OrderedDict
import hashlib
import uuid
import gspread
from google.oauth2.service_account import Credentials
import logging
from io import StringIO

# --- Logging Setup ---
class StreamLogger:
    def __init__(self):
        self.stream = StringIO()
    def write(self, buf):
        self.stream.write(buf)
    def flush(self):
        pass

log_stream = StreamLogger()
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(log_stream)]
)
logger = logging.getLogger()

# --- Google Sheets Setup ---
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
SPREADSHEET_ID = "1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY"
try:
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
except Exception as e:
    st.error(f"Failed to connect to Google Sheets: {e}")
    st.stop()

# --- Charge Mapping and Headers ---
CHARGE_MAP = {
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

HEADERS = [
    "User_ID", "Bill_ID", "Bill_Month_Year", "Bill_Hash",
    "Customer Charge Amount", "Distribution Charge Amount", "Distribution Charge Rate",
    "Environmental Surcharge Amount", "Environmental Surcharge Rate",
    "EmPOWER Maryland Charge Amount", "EmPOWER Maryland Charge Rate",
    "Administrative Credit Amount", "Universal Service Program Amount",
    "MD Franchise Tax Amount", "MD Franchise Tax Rate",
    "Total Electric Delivery Charges Amount",
    "Standard Offer Service & Transmission Amount", "Standard Offer Service & Transmission Rate",
    "Procurement Cost Adjustment Amount", "Procurement Cost Adjustment Rate",
    "Total Electric Supply Charges Amount", "Total Electric Charges - Residential Service Amount"
]

# --- Helper Functions ---
def get_user_id(name):
    return hashlib.sha256((name.strip().upper() or "UNKNOWN").encode("utf-8")).hexdigest()

def parse_charge_line(line):
    # Patterns for charge lines
    patterns = [
        r"^(.*?)(?:\s+\d+\s*kWh\s+X\s+\$(.*?)(?:-)?\s+per\s+kWh)?\s+(-?[\d\.]+)$",
        r"^(.*?)\s+\$?(-?[\d\.]+)$"
    ]
    for pattern in patterns:
        match = re.match(pattern, line.strip())
        if match:
            desc, rate, amount = match.groups()
            desc = desc.strip()
            rate = rate or ""
            try:
                amount = float(amount.replace("−", "-"))
                return {"desc": desc, "rate": rate, "amount": amount}
            except ValueError:
                continue
    logger.debug(f"Could not parse line: {line}")
    return None

def extract_tables_from_pdf(file_bytes):
    tables = []
    try:
        with pdfplumber.open(file_bytes) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                lines = text.splitlines()
                i = 0
                while i < len(lines):
                    if "type of charge how we calculate this charge amount($" in lines[i].lower():
                        table = []
                        i += 1
                        while i < len(lines) and not "type of charge" in lines[i].lower():
                            line = lines[i].strip()
                            if line:
                                parsed = parse_charge_line(line)
                                if parsed:
                                    table.append(parsed)
                                else:
                                    # Combine with previous line if parsing fails (multi-line entry)
                                    if table and "desc" in table[-1]:
                                        table[-1]["desc"] += " " + line
                                        new_parse = parse_charge_line(table[-1]["desc"])
                                        if new_parse:
                                            table[-1] = new_parse
                            i += 1
                        if table:
                            tables.append(table)
                    else:
                        i += 1
        logger.info(f"Extracted {len(tables)} tables")
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
    return tables

def process_charges(tables):
    charges = OrderedDict.fromkeys([h for h in HEADERS if h.endswith("Amount") or h.endswith("Rate")], "")
    for table in tables:
        for entry in table:
            desc = entry["desc"].lower()
            mapped = next((v for k, v in CHARGE_MAP.items() if k in desc), None)
            if mapped:
                amt_key = f"{mapped} Amount"
                rate_key = f"{mapped} Rate"
                if amt_key in charges:
                    charges[amt_key] = charges[amt_key] or 0
                    charges[amt_key] += entry["amount"]
                if rate_key in charges and entry["rate"]:
                    charges[rate_key] = entry["rate"]
    return charges

def extract_metadata(file_bytes):
    meta = {"Bill_Month_Year": "", "Person": ""}
    try:
        with pdfplumber.open(file_bytes) as pdf:
            text = pdf.pages[0].extract_text() or ""
            lines = text.splitlines()
            for line in lines:
                if "your electric bill -" in line.lower():
                    month_year = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}", line, re.I)
                    if month_year:
                        meta["Bill_Month_Year"] = datetime.strptime(month_year.group(), "%b %Y").strftime("%m-%Y")
                elif "account number:" in line.lower():
                    next_line = lines[lines.index(line) + 1]
                    if not any(x in next_line.lower() for x in ["account", "bill", "period"]):
                        meta["Person"] = next_line.strip()
    except Exception as e:
        logger.error(f"Metadata extraction failed: {e}")
    return meta

def process_pdf(file_bytes):
    logger.info("Processing PDF")
    bill_hash = hashlib.md5(file_bytes).hexdigest()
    tables = extract_tables_from_pdf(file_bytes)
    charges = process_charges(tables)
    meta = extract_metadata(file_bytes)
    
    output = OrderedDict.fromkeys(HEADERS, "")
    output["User_ID"] = get_user_id(meta["Person"])
    output["Bill_ID"] = str(uuid.uuid4())
    output["Bill_Month_Year"] = meta["Bill_Month_Year"] or "Unknown"
    output["Bill_Hash"] = bill_hash
    output.update(charges)
    
    logger.info("PDF processing complete")
    return output

def append_to_sheet(data):
    try:
        existing = sheet.get_all_records()
        headers = HEADERS if not existing else list(existing[0].keys())
        if not existing:
            sheet.append_row(headers)
        row = [str(data.get(h, "")) for h in headers]
        sheet.append_row(row)
        logger.info("Data appended to sheet")
    except Exception as e:
        logger.error(f"Sheet append failed: {e}")
        raise

# --- Streamlit UI ---
st.title("Delmarva BillWatch")
st.write("Upload your Delmarva Power PDF bill to store charge data in Google Sheets.")

st.markdown("""
**Privacy Disclaimer:**  
By submitting your bill, you agree that your deidentified data may be used to investigate billing issues with Delmarva Power.  
Your information will not be shared publicly or sold. This is for informational purposes only and does not constitute legal representation.
""")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file:
    with st.spinner("Processing your bill..."):
        file_bytes = uploaded_file.read()
        bill_hash = hashlib.md5(file_bytes).hexdigest()
        
        existing = sheet.get_all_records()
        if any(r.get("Bill_Hash") == bill_hash for r in existing):
            st.warning("This bill has already been uploaded.")
        else:
            log_stream.stream.truncate(0)
            log_stream.stream.seek(0)
            try:
                result = process_pdf(file_bytes)
                append_to_sheet(result)
                st.success("Upload successful! Thank you for your contribution.")
                log_output = log_stream.stream.getvalue()
                if "WARNING" in log_output or "ERROR" in log_output:
                    st.warning(f"Processing details:\n{log_output}")
            except Exception as e:
                st.error(f"Processing failed: {e}")
                st.warning(f"Logs:\n{log_stream.stream.getvalue()}")
