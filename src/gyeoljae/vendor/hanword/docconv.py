#!/usr/bin/env python3
# docconv.py — 레거시 Word 97-2003(.doc, OLE2/CFB) → 에디터 HTML(텍스트만). 표준 라이브러리만.
#   CFB 읽기(FAT/미니FAT·디렉터리 트리) → WordDocument 스트림 FIB → 1Table/0Table 의 CLX 조각표(piece table)
#   → 본문(ccpText) 글자만 문단으로. 서식·표 구조·그림·각주는 버림(UI가 '텍스트만' 안내).
#   입력은 신뢰 불가: 모든 체인·개수·크기에 상한, 순환 체인 거부, 오류는 전부 InputError.
import sys, struct, array, html as htmlmod

try:
    from hwpxconv import HwpxInputError as InputError
except Exception:                                   # pragma: no cover — 단독 사용
    class InputError(ValueError): pass

CFB_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_BYTES = 64 * 1024 * 1024        # 입력 상한
MAX_DIR = 50000                     # 디렉터리 엔트리 상한
MAX_PIECES = 200000                 # 조각표 조각 상한
MAX_CHARS = 8 * 1024 * 1024         # 본문 글자 상한
MAX_PARAS = 200000                  # 출력 문단 상한
FREE, ENDCHAIN = 0xFFFFFFFF, 0xFFFFFFFE

def _err(msg): raise InputError(msg)

def _u32(b):   # 리틀엔디언 uint32 배열(array('I') — 정수 객체 튜플 대비 메모리 1/7). 'I'가 4바이트 아닌 플랫폼은 'L'
    a = array.array("I" if array.array("I").itemsize == 4 else "L")
    a.frombytes(bytes(b[:len(b) // 4 * 4]))
    if sys.byteorder == "big": a.byteswap()
    return a

class _Cfb:
    """최소 CFB 리더 — 루트 저장소 직속 스트림만 이름으로 꺼냄(ObjectPool 내 내장문서의 동명 스트림 혼동 방지)."""
    def __init__(self, data):
        if len(data) < 512 or data[:8] != CFB_SIG: _err("OLE 문서가 아닙니다")
        if len(data) > MAX_BYTES: _err("문서가 너무 큽니다")
        self.d = data
        ver, bom, sshift, mshift = struct.unpack_from("<HHHH", data, 0x1A)
        if bom != 0xFFFE or (ver, sshift) not in ((3, 9), (4, 12)) or mshift != 6: _err("손상된 OLE 헤더")
        self.ss = 1 << sshift; self.ms = 64
        (self.ndir, nfat, self.dir0, _, self.cutoff, self.mfat0, nmfat, difat0, ndifat) = struct.unpack_from("<IIIIIIIII", data, 0x28)
        self.nsec = (len(data) - 512 + self.ss - 1) // self.ss
        if nfat > self.nsec or ndifat > self.nsec: _err("손상된 OLE 헤더")
        # DIFAT → FAT 섹터 목록
        fatsecs = [x for x in struct.unpack_from("<109I", data, 0x4C) if x < 0xFFFFFFFA]
        s, seen = difat0, set()
        per = self.ss // 4 - 1
        while s < 0xFFFFFFFA and len(fatsecs) < nfat:
            if s in seen or s >= self.nsec: _err("손상된 OLE(DIFAT)")
            seen.add(s); blk = self._sec(s)
            fatsecs += [x for x in struct.unpack_from("<%dI" % per, blk, 0) if x < 0xFFFFFFFA]
            s = struct.unpack_from("<I", blk, per * 4)[0]
        fatsecs = fatsecs[:nfat]
        fat = bytearray()
        for fs in fatsecs:
            if fs >= self.nsec: _err("손상된 OLE(FAT)")
            fat += self._sec(fs)
        self.fat = _u32(fat); del fat
        self.dirs = self._dirs()
        root = self.dirs[0]
        self.mini = self._chain_read(root[3], root[4]) if root[3] < 0xFFFFFFFA else b""
        raw = self._chain_read(self.mfat0, None) if nmfat else b""
        self.mfat = _u32(raw)
        self.names = self._root_children()

    def _sec(self, n):
        o = 512 + n * self.ss
        b = self.d[o:o + self.ss]
        return b if len(b) == self.ss else b + b"\0" * (self.ss - len(b))

    def _chain(self, start, table, limit):
        out, s, seen = [], start, set()
        while s != ENDCHAIN:
            if s >= len(table) or s in seen or len(out) >= limit: _err("손상된 OLE(체인)")
            seen.add(s); out.append(s); s = table[s]
        return out

    def _chain_read(self, start, size):
        if start in (ENDCHAIN, FREE): return b""
        secs = self._chain(start, self.fat, self.nsec)
        b = b"".join(self._sec(s) for s in secs)
        if size is not None:
            if size > len(b): _err("손상된 OLE(스트림 크기)")
            b = b[:size]
        return b

    def _dirs(self):
        raw = self._chain_read(self.dir0, None)
        n = min(len(raw) // 128, MAX_DIR)
        out = []
        for i in range(n):
            e = raw[i * 128:(i + 1) * 128]
            nl = struct.unpack_from("<H", e, 64)[0]
            name = e[:max(0, min(nl, 64) - 2)].decode("utf-16-le", "replace")
            typ = e[66]
            left, right, child = struct.unpack_from("<III", e, 68)
            start = struct.unpack_from("<I", e, 116)[0]
            size = struct.unpack_from("<Q", e, 120)[0]
            if self.ss == 512: size &= 0xFFFFFFFF
            out.append((name, typ, (left, right, child), start, size))
        if not out or out[0][1] != 5: _err("손상된 OLE(루트)")
        return out

    def _root_children(self):   # 루트 자식 레드블랙트리 순회(반복, 방문 집합)
        names, stack, seen = {}, [self.dirs[0][2][2]], set()
        while stack:
            i = stack.pop()
            if i == FREE or i >= len(self.dirs) or i in seen: continue
            seen.add(i); e = self.dirs[i]
            if e[1] in (1, 2): names.setdefault(e[0], i)
            stack += [e[2][0], e[2][1]]
        return names

    def stream(self, name):
        i = self.names.get(name)
        if i is None or self.dirs[i][1] != 2: return None
        _, _, _, start, size = self.dirs[i]
        if size > len(self.d): _err("손상된 OLE(스트림 크기)")
        if size < self.cutoff:
            secs = self._chain(start, self.mfat, len(self.mini) // self.ms + 1) if size else []
            b = b"".join(self.mini[s * self.ms:(s + 1) * self.ms] for s in secs)
            if size > len(b): _err("손상된 OLE(미니 스트림)")
            return b[:size]
        return self._chain_read(start, size)

_LAST = [None]   # 단일 슬롯: cfb_kind가 해석한 .doc CFB를 바로 이은 doc_to_html이 재사용(같은 bytes 객체일 때만, 사용 후 비움)

def _cfb(data):
    c = _LAST[0]; _LAST[0] = None
    return c if c is not None and c.d is data else _Cfb(data)

_WDNAME = "WordDocument".encode("utf-16-le") + b"\0\0"

def _dir_guess(data):   # 손상 OLE: 헤더의 첫 디렉터리 섹터부터 연속 64섹터 안, 128바이트 정렬 엔트리 이름만 확인
    if len(data) < 512 or data[:8] != CFB_SIG: return False
    sshift = struct.unpack_from("<H", data, 0x1E)[0]
    if sshift not in (9, 12): return False
    ss = 1 << sshift; d0 = struct.unpack_from("<I", data, 0x30)[0]
    if d0 >= 0xFFFFFFFA: return False
    o0 = 512 + d0 * ss
    for o in range(o0, min(len(data), o0 + 64 * ss) - 128 + 1, 128):
        if data[o:o + 26] == _WDNAME and struct.unpack_from("<H", data, o + 64)[0] == 26: return True
    return False

def cfb_kind(data):
    """OLE/CFB 바이트 → 'doc'(Word 97-2003) | 'hwp'(HWP5·기타 OLE). 해석 실패도 'hwp'(기존 경로가 오류 처리)."""
    try:
        c = _Cfb(data)
    except Exception:   # 손상 OLE: 디렉터리 영역 이름 → .doc 손상을 'hwp 변환 실패'로 오보하지 않게
        if _dir_guess(data): return "doc"
        for off in range(512, min(len(data), 4 << 20) - 4, 512):   # 디렉터리 유실(잘림): 섹터 머리의 FIB 서명(wIdent 0xA5EC·nFib 97+)
            if data[off:off + 2] == b"\xec\xa5" and 0xC1 <= struct.unpack_from("<H", data, off + 2)[0] <= 0x0200: return "doc"
        return "hwp"
    if "WordDocument" in c.names:
        _LAST[0] = c; return "doc"
    return "hwp"

def doc_text(data):
    """.doc 바이트 → 본문 문자열(제어문자 정리 전, Word 원문자)."""
    c = _cfb(data)
    wd = c.stream("WordDocument")
    if wd is None: _err("Word 문서가 아닙니다(WordDocument 없음)")
    if len(wd) < 34: _err("손상된 Word 문서(FIB)")
    ident, nfib = struct.unpack_from("<HH", wd, 0)
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if ident != 0xA5EC: _err("손상된 Word 문서(FIB)")
    if nfib < 0xC1: _err("Word 95 이하 구형 .doc는 지원하지 않습니다(Word에서 .docx로 저장 후 여세요)")
    if flags & 0x0100: _err("암호가 걸린 .doc는 열 수 없습니다")
    o = 32
    csw = struct.unpack_from("<H", wd, o)[0]; o += 2 + csw * 2
    if o + 2 > len(wd): _err("손상된 Word 문서(FIB)")
    cslw = struct.unpack_from("<H", wd, o)[0]; o += 2
    if cslw < 4 or o + cslw * 4 + 2 > len(wd): _err("손상된 Word 문서(FIB)")
    ccp_text = struct.unpack_from("<i", wd, o + 12)[0]; o += cslw * 4
    cb = struct.unpack_from("<H", wd, o)[0]; o += 2
    if cb < 34 or o + 34 * 8 > len(wd): _err("손상된 Word 문서(FIB)")
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, o + 33 * 8)
    if not (0 <= ccp_text <= MAX_CHARS): _err("문서가 너무 큽니다")
    tbl = c.stream("1Table" if flags & 0x0200 else "0Table")
    if tbl is None or lcb_clx == 0 or fc_clx + lcb_clx > len(tbl): _err("손상된 Word 문서(조각표 없음)")
    clx = tbl[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 1:              # Prc(grpprl) 건너뜀
        if i + 3 > len(clx): _err("손상된 Word 문서(CLX)")
        cb_prl = struct.unpack_from("<h", clx, i + 1)[0]
        if cb_prl < 0: _err("손상된 Word 문서(CLX)")
        i += 3 + cb_prl
    if i + 5 > len(clx) or clx[i] != 2: _err("손상된 Word 문서(CLX)")
    lcb = struct.unpack_from("<I", clx, i + 1)[0]; plc = clx[i + 5:i + 5 + lcb]
    if lcb != len(plc) or lcb < 16 or (lcb - 4) % 12: _err("손상된 Word 문서(조각표)")
    n = (lcb - 4) // 12
    if n > MAX_PIECES: _err("문서가 너무 복잡합니다")
    cps = struct.unpack_from("<%di" % (n + 1), plc, 0)
    out, total = [], 0
    for k in range(n):
        if total >= ccp_text: break
        a, b = cps[k], cps[k + 1]
        if b < a or a < 0: _err("손상된 Word 문서(조각표)")
        cnt = min(b - a, ccp_text - total)
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
        if fc & 0x40000000:   # 범위 검사는 디코드 전 바이트 길이로(UTF-16 서로게이트 쌍=2코드단위→1글자라 글자 수 비교 불가)
            off = (fc & 0x3FFFFFFF) // 2; raw = wd[off:off + cnt]
            if len(raw) < cnt: _err("손상된 Word 문서(본문 범위)")
            s = raw.decode("cp1252", "replace")
        else:
            off = fc; raw = wd[off:off + cnt * 2]
            if len(raw) < cnt * 2: _err("손상된 Word 문서(본문 범위)")
            s = raw.decode("utf-16-le", "replace")
        out.append(s); total += cnt
    return "".join(out)

def _clean(text):
    """Word 제어문자 → 문단 목록. 필드(\\x13 코드 \\x14 결과 \\x15)는 결과만, 셀/행 끝(\\x07)·쪽(\\x0c)=문단 경계."""
    paras, cur, stack = [], [], []   # stack: 필드 중첩(True=코드 구간), 깊이 상한 64(초과분은 over로 짝만 맞춤)
    ncode = over = 0                 # ncode=스택 안 코드 구간 수(문자마다 any() 스캔 대신 O(1))
    for ch in text:
        o = ord(ch)
        if o == 0x13:
            if len(stack) >= 64: over += 1
            else: stack.append(True); ncode += 1
            continue
        if o == 0x14:
            if not over and stack and stack[-1]: stack[-1] = False; ncode -= 1
            continue
        if o == 0x15:
            if over: over -= 1
            elif stack: ncode -= 1 if stack.pop() else 0
            continue
        if ncode or over: continue
        if o in (0x0D, 0x07, 0x0C):
            paras.append("".join(cur)); cur = []
            if len(paras) > MAX_PARAS: break
        elif o == 0x0B: cur.append("\n")
        elif o == 0x09: cur.append("\t")
        elif o == 0x1E: cur.append("-")
        elif o < 0x20: continue
        else: cur.append(ch)
    if cur: paras.append("".join(cur))
    while paras and not paras[-1].strip(): paras.pop()
    out, blank = [], 0                          # 빈 문단 3개 이상 연속 → 1개로(셀 끝 표식 잔재)
    for p in paras:
        blank = blank + 1 if not p.strip() else 0
        if blank <= 1: out.append(p)
    return out

def doc_to_html(data):
    """.doc 바이트 → 에디터 HTML(텍스트만). 오류는 InputError."""
    try:
        paras = _clean(doc_text(data))
    except InputError: raise
    except (struct.error, IndexError, ValueError, OverflowError, MemoryError, RecursionError) as e:
        raise InputError("손상된 .doc 문서(%s)" % type(e).__name__)
    h = "".join("<p>%s</p>" % htmlmod.escape(p).replace("\n", "<br>").replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;") if p.strip() else "<p><br></p>"
                for p in paras)
    return h or "<p><br></p>"
