def extract_charges_from_pdf(file_bytes):
    rows = []
    patterns = [
        r"^(?P<desc>.*?)\s+\$(?P<rate>[\d\.]+(?:[−-])?)\s+(?P<amount>-?[\d,]+(?:\.\d+)?(?:[−-])?)\s*$",
        r"^(?P<desc>.*?)\s+(?P<amount>-?[\d,]+(?:\.\d+)?(?:[−-])?)\s*$"
    ]
    
    with pdfplumber.open(file_bytes) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = text.splitlines()
            header_found = False
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Look for headers that indicate the start of the charges table
                if not header_found and ("type of charge" in line.lower() or "charge description" in line.lower()) and ("amount" in line.lower() or "rate" in line.lower()):
                    header_found = True
                    continue
                
                if header_found:
                    for pattern in patterns:
                        match = re.match(pattern, line)
                        if match:
                            desc = match.group("desc").strip()
                            rate_val = match.group("rate") or ""
                            raw_amount = match.group("amount").replace(",", "")
                            
                            # Handle negative values
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
                            break
    return rows
