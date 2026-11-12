"""국회 법안의 메일·리포트 노출 기준. 수집·보존과 수신자 노출을 구별한다."""
import re

# 공식 검색 PRGRS_0003의 실제 세부 상태와 그 이후 단계만 노출한다.
_LATE_STATES = {
    '체계자구', '체계자구상정', '체계자구심사', '체계자구의결', '법사위회부', '법사위심사',
    '본회의부의', '본회의상정', '본회의심의', '본회의가결', '정부이송', '공포',
}
_LATE_MILESTONES = {'체계자구심사', '본회의부의', '본회의가결', '정부이송', '공포'}


def _compact(text: str) -> str:
    return re.sub(r'[\s·ㆍ]', '', text or '')


def late_stage(state: str) -> bool:
    return _compact(state) in _LATE_STATES


def reportable_legislation(item) -> bool:
    if item.source != 'na':
        return True
    milestone = (item.source_item_key or '').partition(':')[0]
    if _compact(milestone) in _LATE_MILESTONES:
        return True
    match = re.search(r'^심사진행상태:\s*([^\n]+)', item.raw_text or '', re.MULTILINE)
    return bool(match and _compact(match.group(1)) in _LATE_STATES)
