import pdfplumber
import os

pdf_path = r"E:\Projects\Semester\BCC\Data Export\Student Ledger\Quiñones Medina Julysel_26801.pdf"
if not os.path.exists(pdf_path):
    # Try with wildcard or listdir
    folder = r"E:\Projects\Semester\BCC\Data Export\Student Ledger"
    for f in os.listdir(folder):
        if "Julysel" in f:
            pdf_path = os.path.join(folder, f)
            break

print("PDF Path:", pdf_path)

with pdfplumber.open(pdf_path) as pdf:
    p = pdf.pages[0]
    words = p.extract_words()
    header_y = None
    for w in words:
        if 'Trans' in w['text']:
            header_y = w['top']
            break
    print("Header Y:", header_y)
    header_words = [w for w in words if abs(w['top'] - header_y) < 5]
    print("\nHeader words:")
    for w in sorted(header_words, key=lambda x: x['x0']):
        print(f"  {w['text']:<18} x0={w['x0']:.1f}, x1={w['x1']:.1f}")

    rows_words = [w for w in words if header_y + 5 <= w['top'] <= header_y + 150]
    lines_map = {}
    for w in rows_words:
        matched = None
        for y in lines_map:
            if abs(w['top'] - y) < 4:
                matched = y
                break
        if matched is None:
            matched = w['top']
            lines_map[matched] = []
        lines_map[matched].append(w)

    print("\nData rows:")
    for y in sorted(lines_map.keys()):
        line_w = sorted(lines_map[y], key=lambda x: x['x0'])
        line_str = " ".join(f"{w['text']}[{w['x0']:.0f}-{w['x1']:.0f}]" for w in line_w)
        print(f"  {line_str}")
