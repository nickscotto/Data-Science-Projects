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

# --- Key Helper: Extract Total Use ---
def extract_total_use_from_pdf(file_bytes):
    expected_headings = "Meter Number Energy Type End Date Start Date Number Of Days Total Use"
    with pdfplumber.open(file_bytes) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = text.splitlines()
            # Search for the heading block in consecutive lines.
            for i in range(len(lines) - 13):
                headings_text = " ".join(lines[i:i+13]).strip()
                if headings_text == expected_headings:
                    # The next line should contain the values.
                    if i + 13 < len(lines):
                        value_line = lines[i + 13]
                        tokens = value_line.split()
                        # Expect 9 tokens:
                        # 1 token for Meter Number,
                        # 2 tokens for Energy Type,
                        # 2 tokens for End Date,
                        # 2 tokens for Start Date,
                        # 1 token for Number Of Days,
                        # 1 token for Total Use.
                        if len(tokens) >= 9:
                            return tokens[8]  # Total Use value.
    return ""

# --- PDF Extraction Functions ---
def extract_charges_from_pdf(file_bytes):
    rows = []
    pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+(?:[−-])?)(?:\s+per\s+kWh))?\s+(?P<amount>-?[\d,]+(?:\.\d+)?(?:[−-])
