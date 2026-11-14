"""HTMLParser 의 EOF 처리를 파이썬 판과 무관하게 선형으로.

옛 html.parser(CVE-2025-6069 수정 전: 3.9~3.11 의 마지막 바이너리 3.10.11·3.11.9, 3.12.10 이하, 3.13.3 이하)는
close() 때 닫히지 않은 구성(태그·주석·표시 구역)을 만날 때마다 끝까지 다시 훑어 2차 시간이 든다
('<img ' * 100000 → 수천 초). Windows 공식 설치본은 보안 패치판 바이너리가 없어 이 옛 판이 그대로 쓰인다.
close() 직전, 아직 파싱 안 된 꼬리에서 '끝까지 닫히지 않는' 구성의 '<' 만 글자(&lt;)로 바꿔 넘긴다.
(닫힌 구성은 그대로 → 정상 문서는 바이트 그대로; 바뀌는 건 어차피 글자로 남거나 버려질 꼬리뿐)
"""
import re
from html.parser import HTMLParser

_GT = re.compile(r">")
_CCLOSE = re.compile(r"--\s*>")                 # 옛 commentclose
_MS_ANY = re.compile(r"\]\s*>")                 # <![if ..]> 류(조건부 주석)
_MS_CDATA = re.compile(r"\]\s*\]\s*>")          # <![CDATA[ ..]]> 류
_MS_OPEN = re.compile(r"<!\[(\s*(?:if|else|endif)\b)?", re.I)

def _last_end(rx, s):
    e = -1
    for m in rx.finditer(s): e = m.end()
    return e

def eof_safe(s):
    """닫히지 않은 구성의 '<' → '&lt;'. 선형."""
    if "<" not in s: return s
    gt = s.rfind(">")
    head, tail = s[:gt + 1], s[gt + 1:].replace("<", "&lt;")   # 마지막 '>' 뒤 '<' = 어떤 구성도 닫히지 않음
    if "<!" in head:
        cs = -1
        for m in _CCLOSE.finditer(head): cs = m.start()  # 옛 파서: <!-- (i) 는 i+4 이후 '--\s*>' 가 있어야 닫힘
        if "<!--" in head:
            head = re.sub(r"<!--", lambda m: m.group(0) if m.start() + 4 <= cs else "&lt;!--", head)
        if "<![" in head:
            a, b = _last_end(_MS_ANY, head), _last_end(_MS_CDATA, head)
            def ms(m):
                lim = a if m.group(1) else b
                return m.group(0) if lim >= 0 and m.end() < lim else "&lt;" + m.group(0)[1:]
            head = _MS_OPEN.sub(ms, head)
    return head + tail

class EofSafeParser(HTMLParser):
    def close(self):
        if self.rawdata: self.rawdata = eof_safe(self.rawdata)
        super().close()
