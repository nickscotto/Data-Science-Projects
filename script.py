import streamlit as st
import io
import os
import re
import uuid
import hashlib
from datetime import datetime
import pdfplumber
import pandas as pd

# --- Helper function to standardize charge type names ---
def standardize_charge_type(charge_type):
    """
    Remove numeric kWh values from the charge type string.
    For example, "Distribution Charge Last 2190 kWh" and 
    "Distribution Charge Last 1980 kWh" both become "Distribution Charge Last kWh".
    """
    standardized = re.sub(r'\s*\d+\s*kWh', ' kWh', charge_type, flags=re.IGNORECASE)
    return standardized.strip()

# --- Functions to extract data from a PDF bill ---

def extract_charges_from_pdf(file_bytes):
    """
    Extract charge rows from pages 2 and 3.
    Uses a regex to capture:
      - The charge description (desc)
      - An optional rate (rate) of the form 'X $<rate> per kWh'
      - The trailing amount (amount)
    Filters out lines with unwanted keywords.
    Returns a list of dicts with keys: Charge_Type, Rate, Amount.
    """
    rows = []
    regex_pattern = (
        r"^(?P<desc>.*?)(?:\s+X\s+\$(?P<rate>[\d\.]+)(?:-)?\s+per\s+kWh)?"
        r"\s+(?P<amount>-?[\d,]+(?:\.\d+)?)(?:\s*)$"
    )
    with pdfplumber.open(file_bytes) as pdf:
        # Process pages 2 and 3 (0-indexed: pages[1] and pages[2])
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
                        match = re.match(regex_pattern, line)
                        if match:
                            desc = match.group("desc").strip()
                            rate_val = match.group("rate") or ""
                            amount_str = match.group("amount").replace(",", "").replace("−", "")
                            try:
                                amount = float(amount_str)
                            except ValueError:
                                continue
                            lower_desc = desc.lower()
                            if any(keyword in lower_desc for keyword in ["page", "year", "meter", "temp", "date"]):
                                continue
                            rows.append({
                                "Charge_Type": desc,
                                "Rate": rate_val,
                                "Amount": amount
                            })
    return rows

def extract_metadata_from_pdf(file_bytes):
    """
    Extract metadata from page 1.
    Looks for a line like "January 2025" and converts it to "MM-YYYY".
    Also assumes the next non-empty line is the person's name.
    Returns a dict with keys:
      - "Bill_Month_Year": formatted as "MM-YYYY"
      - "Person": the extracted name.
    """
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = text.splitlines()
        month_regex = r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$"
        for i, line in enumerate(lines):
            candidate = line.strip()
            if re.match(month_regex, candidate, re.IGNORECASE):
                try:
                    parsed_date = datetime.strptime(candidate, "%B %Y")
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                except Exception:
                    metadata["Bill_Month_Year"] = candidate
                # Assume the next non-empty line is the person's name.
                for j in range(i+1, len(lines)):
                    candidate2 = lines[j].strip()
                    if candidate2:
                        metadata["Person"] = candidate2
                        break
                break
    return metadata

def process_pdf(file_io):
    """
    Process a PDF bill (provided as a file-like object) and return a row dictionary.
    The row includes:
      - User_ID (unique per Person)
      - Bill_ID (unique per bill, determined by bill hash)
      - Bill_Month_Year
      - Bill_Hash (used internally for duplicate detection)
      - One column per standardized charge type (with separate Amount and Rate columns).
    Note: The Person field is used internally for mapping but is not output.
    """
    # Compute bill hash from the file bytes.
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    
    charges = extract_charges_from_pdf(file_io)
    metadata = extract_metadata_from_pdf(file_io)
    
    # Consolidate charges (sum duplicate amounts; keep first non-empty rate)
    consolidated = {}
    for c in charges:
        # Standardize charge type
        ct = standardize_charge_type(c["Charge_Type"])
        amt = c["Amount"]
        rate_val = c["Rate"]
        if ct in consolidated:
            consolidated[ct]["Amount"] += amt
            if not consolidated[ct]["Rate"] and rate_val:
                consolidated[ct]["Rate"] = rate_val
        else:
            consolidated[ct] = {"Amount": amt, "Rate": rate_val}
    
    # Determine the unique User_ID for the person.
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = {}
    person = metadata.get("Person", "")
    if person in st.session_state.customer_ids:
        user_id = st.session_state.customer_ids[person]
    else:
        user_id = str(uuid.uuid4())
        st.session_state.customer_ids[person] = user_id
    
    # Check if a bill with this hash already exists.
    bill_id = None
    if "df" in st.session_state and not st.session_state.df.empty and "Bill_Hash" in st.session_state.df.columns:
        duplicates = st.session_state.df[st.session_state.df["Bill_Hash"] == bill_hash]
        if not duplicates.empty:
            # Use the existing Bill_ID from the duplicate.
            bill_id = duplicates.iloc[0]["Bill_ID"]
    
    # If not duplicate, generate a new Bill_ID.
    if not bill_id:
        bill_id = str(uuid.uuid4())
    
    # Build the row.
    row = {
        "User_ID": user_id,
        "Bill_ID": bill_id,
        "Bill_Month_Year": metadata.get("Bill_Month_Year", ""),
        "Bill_Hash": bill_hash
    }
    for ct, data in consolidated.items():
        if data["Amount"] != 0:
            row[f"{ct} Amount"] = data["Amount"]
        if data["Rate"]:
            row[f"{ct} Rate"] = data["Rate"]
    return row

def save_excel_to_disk(df, excel_file=r"C:\Users\nicho\Downloads\output.xlsx"):
    """
    Save the DataFrame as an Excel file to the predetermined path.
    """
    os.makedirs(os.path.dirname(excel_file), exist_ok=True)
    df.to_excel(excel_file, index=False)

# --- Streamlit App ---

st.title("Electricity Bill Processor")
st.write("Please, upload your PDF bill. Your deidentified utility charge information will be added to our secured database.")

# Predetermined path for the output Excel file.
excel_path = r"C:\Users\nicho\Downloads\output.xlsx"

# Load existing Excel file if available and rebuild the customer_ids mapping.
if "df" not in st.session_state:
    if os.path.exists(excel_path):
        st.session_state.df = pd.read_excel(excel_path)
        # Rebuild the mapping from Person to User_ID if the Person column exists.
        if "Person" in st.session_state.df.columns:
            st.session_state.customer_ids = {}
            for _, row in st.session_state.df.iterrows():
                # Here, if the Person column is not output, you may store it internally.
                # For this example, we assume the Person was used to build the mapping previously.
                person_val = row.get("Person", "")
                user_id = row.get("User_ID", "")
                if person_val and user_id:
                    st.session_state.customer_ids[person_val] = user_id
        else:
            st.session_state.customer_ids = {}
    else:
        st.session_state.df = pd.DataFrame()
        st.session_state.customer_ids = {}

# Allow only one PDF file at a time.
uploaded_file = st.file_uploader("Choose a PDF file", type="pdf", accept_multiple_files=False)

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    file_io = io.BytesIO(file_bytes)
    # Compute bill hash to check for duplicate
    bill_hash = hashlib.md5(file_io.getvalue()).hexdigest()
    if "Bill_Hash" in st.session_state.df.columns and (st.session_state.df["Bill_Hash"] == bill_hash).any():
        st.warning("This bill has already been uploaded. Duplicate not added.")
    else:
        row = process_pdf(file_io)
        st.session_state.df = pd.concat([st.session_state.df, pd.DataFrame([row])], ignore_index=True)
        save_excel_to_disk(st.session_state.df, excel_file=excel_path)
        st.success("Thank you for your contribution!")
