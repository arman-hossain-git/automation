import re

lines = [
    "09/28/2021 PELL 21-22 $ 0.00 $ 1,623.75 $ -1,623.75",
    "01/25/2022 Tuition - 21/22 Contrato 2021-22 $ 1,833.75 $ 0.00 $ 210.00",
    "03/03/2022 PELL 21-22 PELL GRANT 2021-2022 Receipt # 100024 $ 0.00 $ 811.87 $ -601.87",
    "03/25/2022 Tuition - 21/22 Contrato 2021-22 $ 1,833.75 $ 0.00 $ 1,231.88",
]

# Pattern testing:
# Notice ledger codes are like:
# (PELL\s+\d{2}-\d{2}|Tuition\s*-\s*\d{2}/\d{2}|[A-Za-z]+(?:\s*-\s*\S+|\s+\d{2}[/-]\d{2})?)
# Or:
# Group 1: Date ^(\d{2}/\d{2}/\d{4})
# Group 2: Ledger code
# Group 3: Description (optional)
# Group 4: Receipt (optional, e.g. Receipt\s*#\s*\S+|Check\s*#\s*\S+)
# Group 5: Debit
# Group 6: Credit
# Group 7: Balance

# Let's inspect the middle section of each line:
for l in lines:
    m = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*([-\d,.]+)\s+[-]?\$?\s*([-\d,.]+)$", l)
    if m:
        dt, middle, deb, cred, bal = m.groups()
        print(f"Date: {dt} | Middle: '{middle}' | Deb: {deb} | Cred: {cred} | Bal: {bal}")
        
        # Check receipt
        rec = ""
        rm = re.search(r"(Receipt\s*#?\s*\S+|Check\s*#?\s*\S+)", middle, re.IGNORECASE)
        if rm:
            rec = rm.group(1)
            middle_no_rec = middle[:rm.start()].strip()
        else:
            middle_no_rec = middle.strip()
            
        print(f"   No-Rec: '{middle_no_rec}' | Rec: '{rec}'")
