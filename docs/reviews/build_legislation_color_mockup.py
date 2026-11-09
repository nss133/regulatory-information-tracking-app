"""승인한 입법 시안을 실제 운영 PDF 생성기로 재생성한다."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from briefing.reports import build_pdf

if __name__ == '__main__':
    report = json.loads((ROOT / 'reports/examples/insurance-sales-company-bill-2221395.json').read_text())
    output = ROOT / 'output/pdf/보험업법_입법리포트_컬러시안_20261109.pdf'
    print(build_pdf(report, output))
