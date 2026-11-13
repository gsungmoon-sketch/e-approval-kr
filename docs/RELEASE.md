# 공개 저장소 갱신 절차

정본(비공개) 저장소의 이력에는 운영 메모가 있으므로 공개 저장소는 **따로 만든 깨끗한 사본**을 씁니다.
공개 저장소를 직접 고치지 말고, 항상 정본에서 고친 뒤 내보냅니다.

1. **정본에서 수정·커밋** — 전체 테스트(`cd tests && python -m unittest discover -p "test_*.py"`)와
   `python tests/e2e_scenario.py` 통과 확인. 버전을 올리면 `pyproject.toml`·`gyeoljae/__init__.py` 의
   버전을 맞추고 주석 태그(`git tag -a vX.Y.Z`)를 답니다.
2. **공개용 사본 갱신** — 정본에서 `python scripts/make_public.py <공개 사본 디렉터리> --author "이름 <noreply 메일>"`.
   - 처음이면 새 git 저장소(커밋 1개), 이미 있으면 파일을 동기화하고 변경분만 커밋합니다.
   - `.publicignore` 에 적힌 내부 문서는 빠지고, 내부 호스트·경로·IP·비밀 의심 문자열이 있으면 아무것도 만들지 않고 멈춥니다.
3. **공개 사본에서 시험** — 사본 디렉터리에서 단위 테스트와 e2e 를 다시 돌립니다(정본에 없는 파일 의존이 없는지 확인).
4. **검토 후 push** — `git log -p -1` 로 내용을 확인하고 사람이 직접 push(태그도 `git tag -a vX.Y.Z && git push --tags`).

커밋 작성자는 개인 메일 대신 GitHub noreply 주소(`<id>+<계정>@users.noreply.github.com`)를 씁니다.
