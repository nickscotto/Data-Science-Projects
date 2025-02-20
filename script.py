def extract_metadata_from_pdf(file_bytes):
    """
    Extract metadata from page 1.
    Tries multiple patterns to extract a date (converted to "MM-YYYY")
    and the person's name.
    If extraction fails, prompts the user to input the values.
    Returns a dict with keys:
      - "Bill_Month_Year": formatted as "MM-YYYY"
      - "Person": the extracted name.
    """
    metadata = {"Bill_Month_Year": "", "Person": ""}
    with pdfplumber.open(file_bytes) as pdf:
        text = pdf.pages[0].extract_text() or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    # Try different regex patterns for the date
    date_patterns = [
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$",
        r"^\d{1,2}/\d{1,2}/\d{4}$",  # e.g. 01/2025 or 1/1/2025; adjust if needed
    ]
    found_date = False
    for i, line in enumerate(lines):
        for pattern in date_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                # Try to parse as a month and year
                try:
                    # First pattern: full month name and year.
                    parsed_date = datetime.strptime(line, "%B %Y")
                    metadata["Bill_Month_Year"] = parsed_date.strftime("%m-%Y")
                    found_date = True
                    # Assume the next line is the person's name.
                    if i + 1 < len(lines):
                        metadata["Person"] = lines[i+1]
                    break
                except Exception:
                    pass
                # You can add additional parsing logic for other patterns here.
        if found_date:
            break
    
    # If extraction failed, prompt the user for input.
    if not metadata["Bill_Month_Year"]:
        metadata["Bill_Month_Year"] = st.text_input("Enter the bill month and year (MM-YYYY):")
    if not metadata["Person"]:
        metadata["Person"] = st.text_input("Enter your name:")
    
    return metadata
