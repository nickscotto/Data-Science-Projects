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

# --- Helper: Standardize Charge Type Names ---
def standardize_charge_type(charge_type):
    if "Distribution Charge" in charge_type or "Transmission" in charge_type:
        charge_type = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type).strip()
    else:
        charge_type = re.sub(r'\b(First|Last|Next)\b', '', charge_type).strip()
        charge_type = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type).strip()
    return charge_type

# --- Key Helper: Extract Total Use (Updated for Split Header Pattern) ---
def extract_total_use_from_pdf(file_bytes):
    with pdfplumber.open(file_bytes) as pdf:
        if len(pdf.pages) < 2:
            return ""  # Return empty if page 2 doesn’t exist
        page = pdf.pages[1]  # Page 2 (index 1)
        text = page.extract_text() or ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # Debug output to inspect
        st.write("Debug - Page 2 lines:")
        for i, line in enumerate(lines):
            st.write(f"Line {i}: '{line}'")
            # Look for the first header line
            if "meter energy end start number total" in line.lower():
                # Check if the next line is the second header
                if i + 1 < len(lines) and "number type date date of days use" in lines[i + 1].lower():
                    # The next line (i + 2) should be the data row
                    if i + 2 < len(lines):
                        tokens = lines[i + 2].split()
                        if tokens and tokens[-1].isdigit():  # Last token should be Total Use
                            return tokens[-1]
    return ""  # Return empty string if not found

# --- PDF Extraction Functions ---
def extract_charges_from_pdf(file_bytes):
    rows = []
    pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+(?:[−-])?)(?:\s+per\s+kWh))?\s+(?P<amount>-?[\d,]+(?:\.\d+)?(?:[−-])?)\s*$"
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
                        match = re.match(pattern, line)
                        if match:
                            desc = match.group("desc").strip()
                            rate_val = match.group("rate") or ""
                            raw_amount = match.group("amount").replace(",", "")
                            
                            # Fix trailing minus signs
                            if rate_val.endswith(("−", "-")):
                                rate_val = rate_val.rstrip("−-")
                                if not rate_val.startswith("-"):
                                    rate_val = "-" + rate_val
                            if raw_amount.endswith(("−", "-")):
                                raw_amount = raw_amount.rstrip("−-")
                                if not raw_amount.startswith("-"):
                                    raw_amount = "-" + raw_amount
                            
                            try:
                                amount = float(raw_amount)
                            except ValueError:
                                continue
                            
                            # Filter out junk lines
                            if any(k in desc.lower() for k in ["page", "year", "meter", "temp", "date"]):
                                continue
                            
                            rows.append({
                                "Charge_Type": desc,
                                "Rate": rate_val,
                                "Amount": amount
                            })
    return rows

def extract_metadata_from_pdf(file_bytes):
    metadata = {"Bill_Month_Year": "", "Account_Number": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = text.splitlines()
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
        for line in lines:
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

    # Build user_id from account number
    account_number
