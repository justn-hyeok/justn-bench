# justn-bench

> ⚠️ **개인용입니다. 너무 믿지 마세요.**
>
> 제 머신, 제 저장소, 제가 쓰는 하네스 설정에 맞춰 만든 벤치마크입니다.
> 과제 수가 적고, 채점은 단순한 규칙(테스트 통과, 키워드 포함)이며,
> 같은 조합도 돌릴 때마다 결과가 꽤 흔들립니다. 여기서 나온 숫자는
> "내 작업에서 대충 이런 경향" 정도로만 보세요. 모델이나 하네스의 일반적인
> 우열을 말해주지 않습니다.

코딩 에이전트 **하네스 × provider/모델** 조합을, 제 저장소의 실제 커밋으로 만든 과제로
돌려보고 기록·비교하는 스킬입니다.

## 무엇을 재나

세 축으로 나눕니다.

- **과제** — 제 Rust / TS / Go 저장소의 실제 커밋에서 만듭니다.
  | kind | 만드는 법 | 채점 |
  | --- | --- | --- |
  | `bugfix` | 수정 커밋을 되돌리고 테스트는 남김 | 테스트 통과 |
  | `feature` | 기능 커밋 이전 상태 + 명세, 테스트는 숨김 | 숨긴 테스트 통과 |
  | `review` | 버그를 만든 diff를 주고 찾게 함 | 정답 키워드 포함 비율 |
  | `explore` | "X는 어디서 왜?" 질문 | 정답 파일/함수 포함 비율 |
  | `ui` | UI 변경 과제 | 직접 점수 (`bench rate`) |
- **프롬프트 버전** — 모든 과제에 `loose`(모호한 한두 줄)와 `strict`(파일·조건·완료 기준까지)
  두 가지. 둘의 점수 차이가 "모호한 지시에 얼마나 강한가"입니다.
- **측정값** — pass/score, 토큰(입력·출력), 걸린 시간, diff 크기, 직접 매긴 사용감 점수.

`bugfix`/`feature` 과제는 등록할 때 "과제 상태에서 테스트 실패 → 원래 커밋에서 통과"를
자동으로 확인한 것만 받습니다.

## 쓰는 법

```sh
B=scripts/bench.py

# 과제 등록 (bugfix는 실패→통과 게이트 자동 확인)
python3 $B task add --id ts-foo --repo ~/dev/x --commit abc123 --kind bugfix --lang ts \
  --setup-cmd "npm ci" --test-cmd "npx tsx --test tests/foo.test.ts" \
  --loose "foo가 이상해. 고쳐줘" --strict "tests/foo.test.ts가 통과하게 ... 테스트 파일은 수정 금지"
python3 $B task list

# 실행: 하네스와 모델은 매번 직접 고름. --yes 없으면 계획만 출력 (유료 호출 없음)
python3 $B run --harness local.opencode --model hive/zai-org/glm-5.3-flash --task 'ts-*'
python3 $B run --harness local.opencode --model hive/zai-org/glm-5.3-flash --task 'ts-*' --yes

# 비교 / 사용감 점수
python3 $B compare --by harness,model,variant
python3 $B compare --by model,kind --task bugfix
python3 $B rate <run_id> 4 --note "결과는 맞는데 diff가 너무 큼"
```

## 구조

- 실행은 [brgr](https://github.com/justn-hyeok/brgr)에 맡깁니다 (`brgr run --headless --capture-diff`).
  과제마다 새 clone → brgr worktree에서 에이전트가 작업 → diff를 깨끗한 과제 상태에 적용해
  채점합니다. 채점 전에 테스트 파일을 원본으로 되돌리므로 테스트를 고쳐서 통과할 수는 없습니다.
- 토큰은 실행된 worktree 경로로 각 하네스의 로컬 로그에서 찾습니다:
  OpenCode(sqlite), Claude Code(`~/.claude/projects`), Codex(`~/.codex/sessions`),
  Command Code(`~/.commandcode/projects`). 그 외 하네스는 `unknown`.
- 결과: `~/.agents/bench/results.jsonl` (한 줄 = 한 실행). 실행별 답변·테스트 출력은
  `~/.agents/bench/work/<run_id>/`.
- 과제 정의: `tasks/<id>/task.json`.
- 의존성: Python 3.11+ 표준 라이브러리, git, brgr.

## 알려진 한계

- 과제가 적습니다. Go는 저장소가 작아서 특히 적습니다.
- brgr worktree에는 git에 추적되는 파일만 들어갑니다. 에이전트는 `node_modules` 같은 의존성을
  직접 설치해야 할 수 있고, 이게 시간·토큰에 섞입니다.
- `review`/`explore` 채점은 키워드 포함 여부라서 말만 맞게 하면 점수가 나옵니다.
- 토큰 집계는 하네스 로그 형식이 바뀌면 조용히 `none`이 됩니다. 지금 실제 실행으로 확인한 건
  OpenCode뿐이고, Claude Code / Codex / Command Code 추출기는 로그 형식만 보고 짠 상태입니다.
- `out_tok`에는 reasoning 토큰이 포함됩니다. 답변 길이가 아닙니다.
- `review`/`explore`/`ui` 경로는 코드만 있고 실제 과제로 돌려본 적이 없습니다.
- 모델 응답은 비결정적입니다. 비교하려면 `--repeat`로 여러 번 돌리세요.
