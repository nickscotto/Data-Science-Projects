import streamlit as st
import pdfplumber
import re
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

def extract_charges_from_text(text):
    """
    Look through each line for table rows.
    Rows are split on two or more spaces.
    If the last column can be parsed as a float, treat the first column as the charge category.
    """
    charges = {}
    for line in text.splitlines():
        parts = re.split(r'\s{2,}', line.strip())
        if len(parts) < 2:
            continue
        # Skip header rows
        if parts[0].lower().startswith("type of charge"):
            continue
        amt_str = parts[-1].replace('−','').strip()  # remove any trailing minus sign variant
        try:
            amount = float(amt_str)
        except ValueError:
            continue
        category = parts[0].strip()
        charges[category] = amount
    return charges

def extract_charges(pdf_file):
    """Extract charge rows from all pages of the PDF."""
    all_charges = {}
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            page_charges = extract_charges_from_text(text)
            # Merge charges (if duplicate keys occur later, later ones overwrite)
            all_charges.update(page_charges)
    return all_charges

def append_to_google_sheet(row):
    """
    Append a new row to the Google Sheet.
    The sheet is expected to have a header row. If not, it creates one using the keys.
    """
    creds_dict = st.secrets["gcp_service_account"]
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key("1km-vdnfpgYWCP_NXNJC1aCoj-pWc2A2BUU8AFkznEEY").sheet1

    # Get headers (first row) or initialize if empty.
    headers = sheet.row_values(1)
    # We'll include Filename and Timestamp as the first two columns.
    if not headers:
        headers = ["Filename", "Timestamp"] + sorted(row.keys())
        sheet.append_row(headers)

    # Build the row in header order.
    # Ensure Filename and Timestamp are in the row.
    row["Filename"] = row.get("Filename", "")
    row["Timestamp"] = row.get("Timestamp", "")
    row_data = [row.get(h, "") for h in headers]
    sheet.append_row(row_data)

st.title("PDF Charges Extractor")

uploaded_file = st.file_uploader("Upload a PDF", type="pdf")

if uploaded_file is not None:
    # Extract charges from the PDF.
    charges = extract_charges(uploaded_file)
    # Add metadata.
    charges["Filename"] = uploaded_file.name
    charges["Timestamp"] = datetime.now().isoformat()

    st.write("Extracted Charges:")
    st.write(charges)

    if st.button("Append to Google Sheet"):
        append_to_google_sheet(charges)
        st.success("Data appended to Google Sheet.")
